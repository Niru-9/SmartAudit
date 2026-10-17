"""FastAPI backend for the Auditor vertical slice.

Endpoints:
- GET  /api/health       → Model availability + real local tool status
- GET  /api/models       → List models from local Ollama (best-effort)
- POST /api/review       → AI-assisted review (explanation only)
- POST /api/suggest      → Safer rewrite with notes (separate panel; never overwrites source)
- POST /api/chat         → Follow-up Q&A with source + history in context
- POST /api/slither      → Run the local Slither analyzer on the source (tool findings)
- GET  /api/deployed/config            → Phase 6.x deployed-review options (offline)
- POST /api/deployed/validate-address  → Offline EIP-55 address validation
- POST /api/deployed/lookup            → Phase 6.2 consent-gated, read-only verified
                                         source/metadata via Sourcify (Ethereum
                                         mainnet + supported Ethereum testnets)
- POST /api/deployed/analyze-source    → Phase 6.3 explicit run of the existing
                                         local analyzer on the *retrieved* verified
                                         source (single Solidity file only); no
                                         auto-run, no model here
- GET  /*                → Static frontend (index.html) served by FastAPI

Local-only by default: the app calls the configured local Ollama endpoint and the
local Slither/solc installs under `tools/`. No hosted model, no blockchain, no
RPC, wallet, DB, deploy, or sign. One exception is deliberately added in Phase
6.2: `POST /api/deployed/lookup` performs a single **read-only** request to the
allowlisted Sourcify provider, and only when the user supplies an Ethereum
address, explicitly selects a supported Ethereum network (mainnet or a
Sourcify-supported testnet — never chosen implicitly), and explicitly
consents. It never connects a wallet, signs, sends a transaction, deploys, or
modifies a contract.

Safety: treats source as data; returns user-facing errors on timeouts/connection
failures; adds a consistent "notice" to all model responses.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import (
    analysis_store,
    deployed,
    deployed_analysis,
    deployed_source,
    knowledge,
    mcp_client,
    mcp_server,
    ollama_client,
    slither_adapter,
    solhint_adapter,
)
from .config import FRONTEND_DIR, settings, _env_summary
from .prompts import (
    PromptLoadError,
    build_chat_messages,
    build_explain_finding_messages,
    build_review_messages,
    build_suggest_messages,
)
from .schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnalyzerRun,
    ChatRequest,
    ChatResponse,
    DeployedAddressRequest,
    DeployedAddressResponse,
    DeployedAnalyzeRequest,
    DeployedAnalyzeResponse,
    DeployedConfigResponse,
    DeployedLookupRequest,
    DeployedLookupResponse,
    DeployedSourceFile,
    ExplainFindingRequest,
    ExplainFindingResponse,
    ExportReportRequest,
    ExportReportResponse,
    ExportToolRun,
    HealthResponse,
    KnowledgeInfo,
    McpResourceInfo,
    McpStatus,
    McpToolInfo,
    ReviewRequest,
    ReviewResponse,
    SlitherRequest,
    SlitherResponse,
    SolhintResponse,
    SuggestRequest,
    SuggestResponse,
    ToolCallProposal,
    ToolFinding,
    ToolRunRequest,
    ToolRunResponse,
)

app = FastAPI(
    title="Auditor",
    description=(
        "Local-first Solidity review slice — local Qwen model plus the local "
        "Slither analyzer, with an optional local stdio MCP bridge. No "
        "blockchain, RPC, wallet, or hosted model."
    ),
    version="0.3.0",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)


# Local-only CORS: allow only loopback since frontend is same-origin/static.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.exception_handler(PromptLoadError)
async def _prompt_error(_: Request, exc: PromptLoadError):
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                f"Failed to load knowledge prompts: {exc}. Ensure the modules "
                "under prompts/ (solidity-secure-authoring.md, "
                "solidity-audit-reporting.md, solidity-tool-usage.md) exist."
            ),
            "error": "prompt_load_failed",
        },
    )


@app.exception_handler(ollama_client.OllamaUnavailableError)
async def _ollama_unavailable(_: Request, exc: ollama_client.OllamaUnavailableError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": (
                "Local Ollama is not reachable. "
                f"Verify {settings.ollama_url}/api/version responds and a model is pulled. "
                "The UI shows model status and disables actions when unavailable."
            ),
            "error": "ollama_unavailable",
            "hint": "Check Ollama is running and the endpoint/model are correct.",
        },
    )


@app.exception_handler(ollama_client.OllamaTimeoutError)
async def _ollama_timeout(_: Request, exc: ollama_client.OllamaTimeoutError):
    return JSONResponse(
        status_code=504,
        content={
            "detail": (
                "Request to local Ollama timed out. The model may be busy or generating a long response. "
                "Try again or switch to a smaller model if available."
            ),
            "error": "ollama_timeout",
        },
    )


@app.exception_handler(ollama_client.OllamaHttpError)
async def _ollama_http(_: Request, exc: ollama_client.OllamaHttpError):
    return JSONResponse(
        status_code=502,
        content={
            "detail": f"Ollama returned an HTTP error: {exc}.",
            "error": "ollama_http_error",
        },
    )


@app.exception_handler(httpx.ConnectError)
async def _connect(_: Request, exc: httpx.ConnectError):
    return JSONResponse(
        status_code=503,
        content={
            "detail": f"Cannot connect to Ollama at {settings.ollama_url}.",
            "error": "ollama_connect_error",
        },
    )


@app.exception_handler(httpx.TimeoutException)
async def _timeout(_: Request, exc: httpx.TimeoutException):
    return JSONResponse(
        status_code=504,
        content={
            "detail": "Timeout waiting for Ollama to respond.",
            "error": "ollama_timeout",
        },
    )


@app.exception_handler(httpx.HTTPStatusError)
async def _http(_: Request, exc: httpx.HTTPStatusError):
    status = exc.response.status_code
    return JSONResponse(
        status_code=502,
        content={
            "detail": f"Ollama HTTP {status} error.",
            "error": "ollama_http_upstream",
        },
    )


def _knowledge_manifest() -> list[KnowledgeInfo]:
    """Project-owned knowledge modules currently loaded into prompt assembly."""
    out: list[KnowledgeInfo] = []
    for item in knowledge.manifest():
        out.append(
            KnowledgeInfo(
                id=str(item["id"]),
                title=str(item["title"]),
                filename=str(item["filename"]),
                version=str(item["version"]),
                chars=item["chars"] if isinstance(item["chars"], int) else None,
            )
        )
    return out


def _mcp_descriptor(live: bool = False) -> McpStatus:
    """Describe the local stdio MCP bridge (static config, or live if asked)."""
    return McpStatus(
        enabled=True,
        transport="stdio",
        server=mcp_server.SERVER_NAME,
        server_version=mcp_server.SERVER_VERSION,
        protocol_version=mcp_server.PROTOCOL_VERSION,
        live=live,
        tools=[
            McpToolInfo(
                name=t["name"],
                description=str(t.get("description") or ""),
            )
            for t in mcp_server.TOOL_DEFINITIONS
        ],
        resources=[
            McpResourceInfo(
                uri=r["uri"],
                name=r["name"],
                description=r.get("description"),
                mime_type=r.get("mimeType"),
            )
            for r in mcp_server.resource_definitions()
        ],
        detail=None,
    )


@app.get("/api/health", response_model=HealthResponse)
async def health():
    models = await ollama_client.list_models()
    status = "ok" if models else "model_unavailable"
    # Real local tool availability (subprocess probes are cached + run off the
    # event loop). Slither/solc/solhint are integrated; the rest are explicit.
    def _all_tools():
        tools, tool_details = slither_adapter.health_tools()
        # Solhint is integrated for the paste / retrieved-source flows. It is
        # appended here explicitly: `slither_adapter.get_tools_info()` (which the
        # read-only MCP server consumes) deliberately stays unchanged, so no MCP
        # capability is added by this integration.
        solhint_info = solhint_adapter.get_tool_info("solhint")
        tools["solhint"] = solhint_info.status
        tool_details.append(solhint_info)
        return tools, tool_details

    tools, tool_details = await asyncio.to_thread(_all_tools)
    return HealthResponse(
        status=status,
        ollama_url=settings.ollama_url,
        model_default=settings.model,
        models=models,
        tools=tools,
        tool_details=tool_details,
        knowledge=_knowledge_manifest(),
        mcp=_mcp_descriptor(live=False),
        notice=settings.notice,
    )


@app.get("/api/mcp/status", response_model=McpStatus)
async def mcp_status():
    """Live capability probe: spawn the local MCP server and read back what it
    advertises (tools + read-only resources). No hosted service is contacted."""
    try:
        info = await mcp_client.describe_server()
    except mcp_client.McpClientError as exc:
        status = _mcp_descriptor(live=False)
        status.detail = f"MCP bridge unavailable: {exc}"
        return status
    server_info = info.get("server_info") or {}
    return McpStatus(
        enabled=True,
        transport="stdio",
        server=server_info.get("name") or mcp_server.SERVER_NAME,
        server_version=server_info.get("version") or mcp_server.SERVER_VERSION,
        protocol_version=info.get("protocol_version") or mcp_server.PROTOCOL_VERSION,
        live=True,
        tools=[
            McpToolInfo(name=t["name"], description=t.get("description") or "")
            for t in info.get("tools", [])
        ],
        resources=[
            McpResourceInfo(
                uri=r["uri"],
                name=r.get("name") or "",
                description=r.get("description"),
                mime_type=r.get("mime_type"),
            )
            for r in info.get("resources", [])
        ],
        detail=None,
    )


@app.get("/api/models", response_model=list[str])
async def models():
    models = await ollama_client.list_models()
    # If unreachable, return empty list; UI will treat as unavailable.
    return models


@app.post("/api/review", response_model=ReviewResponse)
async def review(req: ReviewRequest):
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    msgs = build_review_messages(req.source)
    content, model_used, elapsed_ms = await ollama_client.chat(req.model, msgs)
    if not isinstance(content, str) or content.strip() == "":
        raise HTTPException(
            status_code=502,
            detail="Model returned an empty/invalid response. Try again or switch models.",
        )
    return ReviewResponse(
        model=model_used,
        content=_strip_prose_fence(content),
        notice=settings.notice,
        elapsed_ms=elapsed_ms,
    )


# Small local models sometimes wrap their whole answer in a prose fence like
# ```markdown ... ``` — strip that wrapper for display. Deliberately narrow:
# only prose languages (markdown/md/text) are unwrapped, so real embedded
# Solidity/code blocks are left intact.
_PROSE_FENCE = re.compile(
    r"^\s*```(?:markdown|md|text)\s*\r?\n(.*)\r?\n```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_PROSE_OPEN = re.compile(r"^\s*```(?:markdown|md|text)\s*\r?\n", re.IGNORECASE)


def _strip_prose_fence(text: str) -> str:
    stripped = text.strip()
    # Case 1: cleanly wrapped prose fence.
    m = _PROSE_FENCE.match(stripped)
    if m and m.group(1).strip():
        return m.group(1).strip()
    # Case 2: opening prose fence with a missing/extra closing fence.
    mo = _PROSE_OPEN.match(stripped)
    if mo:
        inner = stripped[mo.end():]
        inner = re.sub(r"\r?\n?```\s*$", "", inner).strip()
        if inner:
            return inner
    return stripped


def _extract_solidity_code(text: str) -> tuple[str | None, str]:
    """Extract the first Solidity fenced code block as suggested code.

    Returns (suggested_code, notes). notes = text with that block stripped
    (plus any leading/trailing whitespace cleaned).
    """
    # Try to find ```solidity ... ```
    pat = re.compile(r"```solidity\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    m = pat.search(text)
    if m:
        code = m.group(1).strip("\r\n")
        # Strip the block from notes; keep the rest, then clean.
        notes = pat.sub("", text).strip()
        return (code or None, notes or text.strip())
    # Fallback: ```sol ... ``` or generic fenced block containing Solidity-ish
    pat2 = re.compile(r"```(?:sol|solidity|)\s*(.*?)```", re.DOTALL | re.IGNORECASE)
    m = pat2.search(text)
    if m:
        code = m.group(1).strip("\r\n")
        notes = pat2.sub("", text).strip()
        return (code or None, notes or text.strip())
    return (None, text.strip())


@app.post("/api/suggest", response_model=SuggestResponse)
async def suggest(req: SuggestRequest):
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    msgs = build_suggest_messages(req.source, req.review)
    content, model_used, elapsed_ms = await ollama_client.chat(req.model, msgs)
    if not isinstance(content, str) or content.strip() == "":
        raise HTTPException(
            status_code=502,
            detail="Model returned an empty/invalid response. Try again or switch models.",
        )
    suggested_code, notes = _extract_solidity_code(content)
    if suggested_code is None:
        # Don't fail the request; surface as notes with a small hint.
        notes = _strip_prose_fence(content)
    else:
        notes = _strip_prose_fence(notes)
    return SuggestResponse(
        model=model_used,
        notes=notes,
        suggested_code=suggested_code,
        notice=settings.notice,
        elapsed_ms=elapsed_ms,
    )


def _proposal_summary(tool: str) -> str:
    if tool == "run_slither":
        return (
            "Run the local Slither analyzer on the current source under review. "
            "Nothing runs until you approve."
        )
    if tool == "analyzer_status":
        return "Check which local analyzers are available (read-only)."
    return f"Run the local tool '{tool}'."


# Cap on the tool result text handed back to the model (keeps the follow-up
# prompt bounded even if Slither returns many findings).
_MAX_TOOL_TEXT = 12_000


def _record_current_analysis_response(resp: SlitherResponse) -> None:
    """Best-effort: record a normalized Slither result as the current analysis.

    Used by the direct analyzer path; consumed read-only by the MCP server
    (backend/mcp_auditor.py). Auxiliary, so a filesystem problem must never
    break an analysis request.
    """
    try:
        analysis_store.record_analysis(resp)
    except Exception:  # pragma: no cover - aux recording must not fail requests
        pass


def _record_current_analysis_payload(payload: dict) -> None:
    """Best-effort: record the approved-MCP-path payload as the current analysis."""
    try:
        analysis_store.record_tool_payload(payload)
    except Exception:  # pragma: no cover - aux recording must not fail requests
        pass


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    if not req.question or req.question.strip() == "":
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    hist: list[dict[str, str]] = []
    for m in req.history:
        hist.append({"role": m.role, "content": m.content})

    # --- Plain chat (no tools): unchanged Phase 1/3 behaviour. -------------
    if not req.tools_enabled:
        msgs = build_chat_messages(req.source, req.question, hist)
        content, model_used, elapsed_ms = await ollama_client.chat(req.model, msgs)
        if not isinstance(content, str) or content.strip() == "":
            raise HTTPException(
                status_code=502,
                detail="Model returned an empty/invalid response. Try again or switch models.",
            )
        return ChatResponse(
            model=model_used,
            reply=_strip_prose_fence(content),
            notice=settings.notice,
            elapsed_ms=elapsed_ms,
            tool_proposal=None,
        )

    # --- Tool-enabled chat: the model may *propose* a call; nothing runs. --
    msgs = build_chat_messages(req.source, req.question, hist)
    result = await ollama_client.chat_with_tools(
        req.model, msgs, mcp_server.tool_definitions_for_model()
    )
    if result.tool_calls:
        call = result.tool_calls[0]
        fn = call.get("function") if isinstance(call, dict) else None
        fn = fn if isinstance(fn, dict) else {}
        name = str(fn.get("name") or "")
        if name in mcp_server.TOOL_NAMES:
            raw_args = fn.get("arguments")
            args: dict[str, object] = raw_args if isinstance(raw_args, dict) else {}
            # The model's arguments are shown for transparency but are never
            # forwarded to the tool (both tools take none; the server rejects
            # any). Execution happens only after explicit approval.
            return ChatResponse(
                model=result.model_used,
                reply="",
                notice=settings.notice,
                elapsed_ms=result.elapsed_ms,
                tool_proposal=ToolCallProposal(
                    id=f"call-{uuid.uuid4().hex[:12]}",
                    tool=name,
                    arguments=args,
                    requires_approval=True,
                    summary=_proposal_summary(name),
                ),
            )
        # Model asked for something that is not exposed; ignore it.
        note = f"(The model requested an unavailable tool '{name}'; it was ignored.)"
        base = _strip_prose_fence(result.content) if result.content.strip() else ""
        return ChatResponse(
            model=result.model_used,
            reply=(base + "\n\n" + note).strip(),
            notice=settings.notice,
            elapsed_ms=result.elapsed_ms,
            tool_proposal=None,
        )

    content = result.content
    if not isinstance(content, str) or content.strip() == "":
        raise HTTPException(
            status_code=502,
            detail="Model returned an empty/invalid response. Try again or switch models.",
        )
    return ChatResponse(
        model=result.model_used,
        reply=_strip_prose_fence(content),
        notice=settings.notice,
        elapsed_ms=result.elapsed_ms,
        tool_proposal=None,
    )


@app.post("/api/tools/run", response_model=ToolRunResponse)
async def tools_run(req: ToolRunRequest):
    """Execute a user-approved allowlisted tool, then ask the model to answer.

    This is the only place a model-requested tool actually runs, and it only
    runs when the frontend calls it after the user clicks Approve. The source
    is passed to the tool out-of-band; the model never supplies code, a path,
    or a command. The direct ``POST /api/slither`` endpoint is untouched.
    """
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    if not req.question or req.question.strip() == "":
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if req.tool not in mcp_server.TOOL_NAMES:
        raise HTTPException(
            status_code=400, detail=f"Tool '{req.tool}' is not available."
        )

    tool_ok = True
    try:
        if req.tool == "run_slither":
            payload = await mcp_client.run_slither_once(req.source)
            _record_current_analysis_payload(payload)
        else:
            payload = await mcp_client.call_analyzer_status()
    except mcp_client.McpClientError as exc:
        tool_ok = False
        payload = {
            "tool": req.tool,
            "status": "error",
            "summary": f"The local analysis bridge could not complete: {exc}",
        }
        if req.tool == "run_slither":
            _record_current_analysis_payload(payload)

    hist: list[dict[str, str]] = []
    for m in req.history:
        hist.append({"role": m.role, "content": m.content})

    tool_text = json.dumps(payload, ensure_ascii=False)
    if len(tool_text) > _MAX_TOOL_TEXT:
        tool_text = tool_text[:_MAX_TOOL_TEXT] + "... [tool result truncated]"

    msgs: list[dict] = build_chat_messages(req.source, req.question, hist)  # type: ignore[assignment]
    msgs.append(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": req.tool, "arguments": {}}}
            ],
        }
    )
    msgs.append({"role": "tool", "tool_name": req.tool, "content": tool_text})

    content, model_used, elapsed_ms = await ollama_client.chat(req.model, msgs)
    if not isinstance(content, str) or content.strip() == "":
        # Fall back to a plain, tool-grounded statement rather than erroring.
        summary = payload.get("summary") if isinstance(payload, dict) else None
        content = (
            f"The tool '{req.tool}' finished with status "
            f"'{payload.get('status', 'unknown')}'. {summary or ''}"
        ).strip()
    return ToolRunResponse(
        model=model_used,
        tool=req.tool,
        tool_ok=tool_ok,
        tool_result=payload,
        reply=_strip_prose_fence(content),
        notice=settings.notice,
        elapsed_ms=elapsed_ms,
    )


@app.post("/api/explain-finding", response_model=ExplainFindingResponse)
async def explain_finding(req: ExplainFindingRequest):
    """Explain one selected analyzer finding (Slither or Solhint) using local Qwen."""
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    if not req.finding:
        raise HTTPException(status_code=400, detail="No finding selected.")
    msgs = build_explain_finding_messages(req.source, req.finding.model_dump())
    content, model_used, elapsed_ms = await ollama_client.chat(req.model, msgs)
    if not isinstance(content, str) or content.strip() == "":
        raise HTTPException(
            status_code=502,
            detail="Model returned an empty/invalid response. Try again or switch models.",
        )
    return ExplainFindingResponse(
        model=model_used,
        content=_strip_prose_fence(content),
        notice=settings.notice + " AI-generated guidance — requires human review. Not a verified finding or security guarantee.",
        elapsed_ms=elapsed_ms,
    )


def _fmt_loc(file: str | None, start_line: int | None, end_line: int | None) -> str:
    """Format a source location as `file:start-end` (or `file:start`)."""
    name = file or "Submitted.sol"
    if start_line is None:
        return name
    if end_line is None or end_line == start_line:
        return f"{name}:{start_line}"
    return f"{name}:{start_line}-{end_line}"


def _run_status_note(status: str) -> str:
    """One honest sentence for a tool status in the exported report."""
    return {
        "ok": "This tool ran and reported the findings listed above.",
        "no_findings": (
            "This tool ran and reported no findings. A clean run does not mean "
            "the contract is safe."
        ),
        "unavailable": "This tool was not available locally, so nothing ran for it.",
        "compiler_error": "The source could not be parsed/compiled by this tool; nothing was reported.",
        "timeout": "This tool exceeded its time limit and was stopped; nothing was reported.",
        "error": "This tool failed unexpectedly; nothing was reported.",
        "skipped": "This tool was deliberately not run for this report.",
    }.get(status, "This tool's status is unknown; treat its results as unavailable.")


def _render_finding(idx: int, f: ToolFinding) -> list[str]:
    """Render one deterministic finding block for the report."""
    fid = f.id or f.check or f"finding_{idx}"
    out: list[str] = [
        f"### {idx}. {f.check} [{f.severity}] (tool: {f.tool}; confidence: {f.confidence})",
        f"**Finding ID:** {fid}",
        f"**Tool:** {f.tool} {f.tool_version or ''}".strip(),
        f"**Location:** {_fmt_loc(f.file, f.start_line, f.end_line)}",
    ]
    if f.title and f.title != f.check:
        out.append(f"**Title:** {f.title}")
    if f.description:
        out.append(f"**Description:** {f.description}")
    if f.evidence:
        out.append(f"**Evidence:** {f.evidence}")
    if f.reference:
        out.append(f"**Reference:** {f.reference}")
    if f.locations:
        out.append("**Extra locations:**")
        for loc in f.locations:
            out.append(f"- {_fmt_loc(loc.file, loc.start_line, loc.end_line)}")
    out.append("")
    return out


@app.post("/api/export-report", response_model=ExportReportResponse)
async def export_report(req: ExportReportRequest):
    """Export the current audit report.

    Deterministic findings are grouped **by tool** (each with its own run status)
    and attributed to the tool that produced them; AI explanations and the
    separate local-Qwen review are clearly separated and are never presented as
    tool output. Identifies as an automated analysis aid requiring human review.
    """
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC%z")
    lines: list[str] = []
    lines.append("# AUDITOR REPORT - AUTOMATED ANALYSIS AID")
    lines.append(f"Generated: {timestamp}")
    lines.append(
        "NOTE: This is an automated analysis aid. Findings labelled as tool output "
        "come from the named deterministic analyzer(s) only and are never invented "
        "or altered by the model. AI explanations and the local-Qwen review are "
        "clearly separated, AI-generated, and require human review. Do NOT assume "
        "the contract is safe, fully audited, or guaranteed free of vulnerabilities."
    )
    lines.append("")

    # --- Source provenance / scope (when applicable) ----------------------
    if req.scope or req.chain_id is not None or req.address:
        lines.append("## SOURCE PROVENANCE")
        if req.scope:
            lines.append(f"- Source: {req.scope}")
        if req.chain_id is not None or req.chain_name:
            where = req.chain_name or f"chain {req.chain_id}"
            if req.address:
                where = f"{where} · {req.address}"
            lines.append(f"- Network/contract: {where}")
        lines.append(
            "- Analyzer output applies ONLY to the source above; it is not a "
            "statement about any other deployment or revision."
        )
        lines.append("")

    # --- Analyzer run summary (per tool status) ---------------------------
    if req.tool_runs:
        lines.append("## ANALYZER RUN SUMMARY")
        lines.append(
            "Each analyzer is listed with the status of this run. A tool that did "
            "not run is shown as unavailable/skipped/error and has no attributed "
            "findings."
        )
        for run in req.tool_runs:
            version = f" {run.tool_version}" if run.tool_version else ""
            lines.append(
                f"- {run.tool}{version}: status `{run.status}` — "
                f"{len(run.findings)} finding(s)"
            )
        lines.append("")

    lines.append("## CONTRACT SOURCE")
    lines.append("```solidity")
    lines.append(req.source)
    lines.append("```")
    lines.append("")

    # --- Findings: grouped per tool when available, else the flat legacy list.
    if req.tool_runs:
        lines.append("## STATIC ANALYSIS FINDINGS (DETERMINISTIC)")
        lines.append(
            "Deterministic tool output, grouped by the tool that produced it. No "
            "finding here is model-generated."
        )
        lines.append("")
        for run in req.tool_runs:
            version = f" {run.tool_version}" if run.tool_version else ""
            lines.append(f"### Tool: {run.tool}{version} — status `{run.status}`")
            if run.summary:
                lines.append(f"**Run summary:** {run.summary}")
            lines.append(f"**Findings:** {len(run.findings)}")
            if not run.findings:
                lines.append(_run_status_note(run.status))
                lines.append("")
            else:
                lines.append("")
                for idx, f in enumerate(run.findings, 1):
                    lines.extend(_render_finding(idx, f))
    elif req.findings:
        lines.append(
            f"## STATIC ANALYSIS FINDINGS (DETERMINISTIC) - {len(req.findings)} finding(s)"
        )
        lines.append("")
        for idx, f in enumerate(req.findings, 1):
            lines.extend(_render_finding(idx, f))
    else:
        lines.append("## STATIC ANALYSIS FINDINGS")
        lines.append("No findings included in this report.")
        lines.append("")

    # --- AI explanations (separate, keyed by finding) ---------------------
    if req.explanations:
        lines.append("## AI-GENERATED EXPLANATIONS (SEPARATE SECTION)")
        lines.append("These are AI-generated guidance requiring human review. They are NOT verified findings and do NOT constitute a security guarantee.")
        lines.append("")
        for finding_id, explanation in req.explanations.items():
            # Find matching finding for context (across all tools: the legacy
            # flat list plus every per-tool run).
            pool = [
                *req.findings,
                *(f for run in req.tool_runs for f in run.findings),
            ]
            matching_f = None
            for f in pool:
                if f.id == finding_id or f.check == finding_id:
                    matching_f = f
                    break
            if matching_f:
                lines.append(f"### Explanation for: {matching_f.check} ({matching_f.id})")
                lines.append(f"**Detector:** {matching_f.check}")
                lines.append(f"**Tool:** {matching_f.tool}")
                lines.append(f"**Location:** {_fmt_loc(matching_f.file, matching_f.start_line, matching_f.end_line)}")
            else:
                lines.append(f"### Explanation for finding: {finding_id}")
            lines.append(explanation)
            lines.append("")
            lines.append("---")
            lines.append("")
    else:
        lines.append("## AI-GENERATED EXPLANATIONS")
        lines.append("No AI explanations were requested/attached to this report.")
        lines.append("")

    # --- Separate local-Qwen review (never tool output) -------------------
    if req.qwen_review and req.qwen_review.strip():
        model = req.qwen_model or "local model"
        lines.append("## LOCAL QWEN REVIEW (AI-GENERATED — SEPARATE FROM TOOL FINDINGS)")
        lines.append(
            f"Produced by the local model ({model}). This is AI-generated commentary, "
            "NOT a deterministic tool finding, and may be incomplete or incorrect. "
            "It does not alter, replace, or supersede any tool finding above."
        )
        lines.append("")
        lines.append(req.qwen_review.strip())
        lines.append("")
    else:
        lines.append("## LOCAL QWEN REVIEW (AI-GENERATED — SEPARATE FROM TOOL FINDINGS)")
        lines.append("No local-Qwen review was attached to this report.")
        lines.append("")

    lines.append("## REPORT LIMITATIONS")
    lines.append("- This report is for informational and review purposes only.")
    lines.append("- Static analysis findings are deterministic but may be incomplete or have false positives/negatives; a tool that did not run contributed no findings.")
    lines.append("- AI explanations and the local-Qwen review are not verified; human review is required for all items.")
    lines.append("- No analyzer, compiler, or model claims completeness or guarantees security.")
    lines.append("")

    report = "\n".join(lines)
    filename = f"auditor-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    return ExportReportResponse(report=report, filename=filename)


@app.post("/api/slither", response_model=SlitherResponse)
async def slither(req: SlitherRequest):
    """Run the local Slither analyzer on the submitted source.

    This is deliberately separate from the Qwen review flow. The adapter
    never raises for tool-level problems; it returns a structured response
    (`unavailable` / `compiler_error` / `timeout` / `error` / `no_findings` /
    `ok`). Validation (empty source) is handled here as a 400.
    """
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    resp = await slither_adapter.run_slither(req.source)
    _record_current_analysis_response(resp)
    return resp


@app.post("/api/solhint", response_model=SolhintResponse)
async def solhint(req: SlitherRequest):
    """Run the local Solhint linter on the submitted source.

    Added 2026-10-17. Solhint is a deterministic Solidity **linter** (distinct
    from Slither's symbolic analysis). Like ``/api/slither`` it is reached only
    from an explicit user action and never auto-runs; the adapter returns a
    structured response for every tool-level outcome rather than raising.
    Findings are attributed to ``tool="solhint"`` and are never merged into
    Slither's.
    """
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")
    return await solhint_adapter.run_solhint(req.source)


def _to_analyzer_run(resp) -> AnalyzerRun:
    """Copy a per-tool response into the combined-run shape (no mutation)."""
    return AnalyzerRun(
        tool=resp.tool,
        tool_version=resp.tool_version,
        status=resp.status,
        summary=resp.summary,
        finding_count=resp.finding_count,
        findings=resp.findings,
        source_name=resp.source_name,
        duration_ms=resp.duration_ms,
        notice=resp.notice,
        detail=resp.detail,
        raw_output=resp.raw_output,
        raw_output_truncated=resp.raw_output_truncated,
    )


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest):
    """Run the available local analyzers on the submitted source, one run each.

    Added 2026-10-17. Each known analyzer runs independently and reports its own
    status/version/findings — the response never merges one tool's output into
    another's, and never implies a tool ran when it did not. The ``tools``
    request field optionally restricts the run; any known analyzer not listed is
    reported with status ``skipped``.

    Explicit-action only: this endpoint runs nothing unless the client calls it
    (the UI's "Run analyzers" button); it is never triggered by a page load,
    source edit, lookup completion, or result render. Only Slither's run is
    recorded as the current analysis for the read-only MCP surface (unchanged
    behavior); Solhint's result is not exposed over MCP.
    """
    if not req.source or req.source.strip() == "":
        raise HTTPException(status_code=400, detail="Source cannot be empty.")

    requested = None
    if req.tools is not None:
        requested = {t.strip().lower() for t in req.tools if t and t.strip()}

    runs: list[AnalyzerRun] = []
    slither_resp: SlitherResponse | None = None
    for name in ("slither", "solhint"):
        if requested is not None and name not in requested:
            runs.append(
                AnalyzerRun(
                    tool=name,
                    status="skipped",
                    summary=f"{name} was not requested for this run.",
                    finding_count=0,
                    source_name="Submitted.sol",
                    duration_ms=0,
                    notice=settings.notice,
                )
            )
            continue
        if name == "slither":
            slither_resp = await slither_adapter.run_slither(req.source)
            runs.append(_to_analyzer_run(slither_resp))
        else:
            runs.append(_to_analyzer_run(await solhint_adapter.run_solhint(req.source)))

    # Preserve the existing read-only MCP behavior: only a Slither run becomes
    # the "current analysis".
    if slither_resp is not None:
        _record_current_analysis_response(slither_resp)

    total_ms = sum(r.duration_ms for r in runs)
    return AnalyzeResponse(
        source_name="Submitted.sol",
        tools=runs,
        duration_ms=total_ms,
        notice=settings.notice,
    )


# --------------------------------------------------------------------------
# Phase 6.x — read-only deployed-contract review.
#
# `/config` and `/validate-address` are fully offline (chain list + local EIP-55
# validation via `backend/deployed.py`). `/lookup` (Phase 6.2) is the SINGLE
# outbound endpoint: it retrieves a contract's public verified source + basic
# metadata from the allowlisted Sourcify provider, and only with explicit
# consent (see `backend/deployed_source.py`). `analyze-source` (Phase 6.3) runs
# the *existing* local analyzer on the source already retrieved for the current
# lookup — only on an explicit click, never automatically, and it never runs the
# model. None of these routes connect a wallet/sign/deploy/modify anything.
# --------------------------------------------------------------------------


@app.get("/api/deployed/config", response_model=DeployedConfigResponse)
async def deployed_config() -> DeployedConfigResponse:
    """Return the small, configurable chain list + consent/read-only wording.

    Pure local data: no network, no blockchain, no analyzer, no MCP.
    """
    cfg = deployed.get_config()
    return DeployedConfigResponse(
        phase=cfg["phase"],
        lookup_available=cfg["lookup_available"],
        chains=cfg["chains"],
        default_chain_id=cfg["default_chain_id"],
        consent_text=cfg["consent_text"],
        read_only_notice=cfg["read_only_notice"],
        notice=settings.notice,
    )


@app.post("/api/deployed/validate-address", response_model=DeployedAddressResponse)
async def deployed_validate_address(
    req: DeployedAddressRequest,
) -> DeployedAddressResponse:
    """Validate/normalize an EVM address offline (EIP-55).

    This performs local string/crypto work only. It does not look the address
    up anywhere; `lookup_performed` is always False in this phase and consent
    is echoed back only as recorded UI state.
    """
    result = deployed.validate_address(req.address)
    return DeployedAddressResponse(
        input=result["input"],
        valid=result["valid"],
        reason=result["reason"],
        message=result["message"],
        normalized=result["normalized"],
        checksum=result["checksum"],
        checksum_provided=result["checksum_provided"],
        checksum_ok=result["checksum_ok"],
        is_mixed_case=result["is_mixed_case"],
        chain_id=req.chain_id,
        consent=req.consent,
        lookup_performed=False,
        lookup_available=deployed.LOOKUP_AVAILABLE,
        notice=deployed.READ_ONLY_NOTICE,
    )


@app.post("/api/deployed/lookup", response_model=DeployedLookupResponse)
async def deployed_lookup(req: DeployedLookupRequest) -> DeployedLookupResponse:
    """Consent-gated, read-only retrieval of a contract's verified source.

    This is the **only** endpoint that performs an outbound request. It does so
    only when the request explicitly carries ``consent: true`` and a supported
    ``chain_id`` (Ethereum mainnet or a Sourcify-supported Ethereum testnet —
    never chosen implicitly); otherwise it returns a refusal status
    without contacting anyone. It retrieves public verified source/metadata via
    the allowlisted Sourcify provider (`backend.deployed_source`). It never
    connects a wallet, signs, sends a transaction, deploys, or modifies a
    contract, and it runs no analyzer or model.
    """
    result = await deployed_source.lookup_verified_source(
        req.address, req.chain_id, req.consent
    )
    return DeployedLookupResponse(
        status=result["status"],
        lookup_performed=result["lookup_performed"],
        consent=result["consent"],
        chain_id=result["chain_id"],
        chain_name=result["chain_name"],
        address=result["address"],
        checksum=result["checksum"],
        provider=result["provider"],
        provider_docs=result["provider_docs"],
        verified=result["verified"],
        match=result["match"],
        creation_match=result["creation_match"],
        runtime_match=result["runtime_match"],
        verified_at=result["verified_at"],
        match_id=result["match_id"],
        contract_name=result["contract_name"],
        compiler_version=result["compiler_version"],
        language=result["language"],
        deployment_block=result["deployment_block"],
        sources=[DeployedSourceFile(**f) for f in result["sources"]],
        sources_truncated=result["sources_truncated"],
        source_file_count=result["source_file_count"],
        abi_entries=result["abi_entries"],
        metadata_present=result["metadata_present"],
        message=result["message"],
        notice=result["notice"],
    )


@app.post("/api/deployed/analyze-source", response_model=DeployedAnalyzeResponse)
async def deployed_analyze_source(
    req: DeployedAnalyzeRequest,
) -> DeployedAnalyzeResponse:
    """Phase 6.3 — run the existing local analyzer on *retrieved* verified source.

    Reached **only** from the deployed panel's explicit "Analyze" button. It
    performs **no** network request and runs **no** model. It analyzes nothing
    unless the request carries a valid address, a supported Ethereum chain, and
    exactly one complete (non-truncated) Solidity source file — the source must
    be the one retrieved by the current verified lookup. Multi-file verified
    source is refused as unsupported in this phase rather than partially
    analyzed. The returned ``analyzer`` is the unchanged normalized
    ``SlitherResponse`` (the same shape as a pasted-source run).
    """
    result = await deployed_analysis.analyze_retrieved_source(
        req.address,
        req.chain_id,
        req.sources,
        sources_truncated=req.sources_truncated,
        language=req.language,
    )
    return DeployedAnalyzeResponse(**result)


# Mount static frontend AFTER API routes so /api/* take precedence.
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="static")


@app.on_event("startup")
async def _startup_log():
    # Log minimal configuration; never log source.
    print(f"[auditor] startup: {_env_summary()}")
    print(f"[auditor] frontend_dir={FRONTEND_DIR} exists={FRONTEND_DIR.exists()}")
    print(
        f"[auditor] knowledge modules={knowledge.module_ids()} "
        f"version={knowledge.KNOWLEDGE_VERSION}"
    )
    print(
        f"[auditor] mcp bridge=stdio server={mcp_server.SERVER_NAME} "
        f"tools={list(mcp_server.TOOL_NAMES)}"
    )


__all__ = ["app"]
