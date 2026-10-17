"""Phase 4 tests: knowledge modules (Goal A) + local MCP bridge (Goal B).

Two groups, both runnable offline except for the live Slither delegation checks:

1. Knowledge / prompt assembly — modules parse, carry versions, route by task,
   strip their Sources footer, and always end with the shared guardrails.
2. MCP bridge — the stdlib stdio server + client round-trip (handshake,
   tools/list, tools/call, resources), the delegation of ``run_slither`` to the
   existing adapter, argument/host safety, and the *approval gate*: a chat
   tool proposal must NOT run anything until the user-approved endpoint runs it.
   The direct ``POST /api/slither`` path is asserted to keep working.

Run:  py tests/test_mcp_bridge.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from backend import knowledge, mcp_client, mcp_server, ollama_client  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402

SRC = "pragma solidity ^0.8.20; contract A { function f() external {} }"
results: list[bool] = []

# The app records the "current analysis" for the read-only MCP server after each
# run; route that at a throwaway file so these suites never clobber real state.
_STATE_TMP = str(Path(tempfile.gettempdir()) / "auditor-test-bridge-current-analysis.json")


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


# --------------------------------------------------------------------------
# Goal A: knowledge modules
# --------------------------------------------------------------------------


def test_knowledge() -> None:
    ids = knowledge.module_ids()
    check(
        "three knowledge modules registered",
        set(ids) == {"secure-authoring", "audit-reporting", "tool-usage"},
        str(ids),
    )

    for mid in ids:
        body = knowledge.load_body(mid)
        ver = knowledge.module_version(mid)
        check(f"module '{mid}' has a version", ver != "unknown", ver)
        check(f"module '{mid}' has a non-trivial body", len(body) > 200, f"{len(body)} chars")
        check(
            f"module '{mid}' strips its Sources footer",
            "## Sources" not in body and "not sent to the model" not in body,
        )

    review = knowledge.build_system_prompt("review")
    check(
        "review prompt includes untrusted-data guardrail",
        "UNTRUSTED DATA" in review,
        "",
    )
    check(
        "review prompt forbids inventing tool findings",
        "do not create, delete" in review.lower(),
    )
    # Deterministic routing: review = audit-reporting + tool-usage + guardrails.
    expected_review = "\n\n".join(
        [
            knowledge.load_body("audit-reporting"),
            knowledge.load_body("tool-usage"),
            knowledge.GUARDRAILS,
        ]
    )
    check("review prompt assembles the exact task plan", review == expected_review)
    check(
        "review prompt excludes authoring-only guidance",
        knowledge.load_body("secure-authoring") not in review,
    )
    suggest = knowledge.build_system_prompt("suggest")
    expected_suggest = "\n\n".join(
        [knowledge.load_body("secure-authoring"), knowledge.GUARDRAILS]
    )
    check("suggest prompt assembles the exact task plan", suggest == expected_suggest)
    check("task plans are minimal", knowledge.task_modules("suggest") == ("secure-authoring",))
    mf = knowledge.manifest()
    check(
        "manifest exposes ids/versions",
        all(m.get("version") and m.get("chars") for m in mf),
        str([(m["id"], m["version"]) for m in mf]),
    )


# --------------------------------------------------------------------------
# Goal B: MCP server/client
# --------------------------------------------------------------------------


def test_mcp_client_server() -> None:
    async def run() -> None:
        info = await mcp_client.describe_server()
        names = [t["name"] for t in info["tools"]]
        check("MCP tools/list exposes the two allowlisted tools",
              names == ["analyzer_status", "run_slither"], str(names))
        uris = [r["uri"] for r in info["resources"]]
        check(
            "MCP resources/list exposes the three knowledge resources",
            uris == [knowledge.resource_uri(m) for m in knowledge.module_ids()],
            str(uris),
        )
        check("MCP server advertises a version", bool(info["server_info"].get("version")))
        check("MCP protocol version negotiated", bool(info["protocol_version"]))

        status = await mcp_client.call_analyzer_status()
        tools = {t["name"]: t for t in status["tools"]}
        check("MCP analyzer_status reports slither", "slither" in tools)
        check("MCP analyzer_status reports solc", "solc" in tools)

        async with mcp_client.McpClient() as c:
            await c.initialize()
            body = await c.read_resource("auditor://knowledge/tool-usage")
            check("MCP read_resource returns a knowledge module", len(body) > 200, f"{len(body)} chars")

    asyncio.run(run())


def test_mcp_delegates_to_adapter() -> None:
    # run_slither must call the existing adapter, not a copy of the runner.
    # This runs the server's handler in-process so we can observe the call.
    import tempfile
    from pathlib import Path

    calls: list[str] = []
    real_run = mcp_server.slither_adapter.run_slither

    async def spy(source: str):
        calls.append(source)
        return await real_run(source)

    tmp = Path(tempfile.mkdtemp(prefix="auditor-mcp-test-"))
    src_file = tmp / "Submitted.sol"
    src_file.write_text(SRC, encoding="utf-8")
    old = os.environ.get(mcp_server.SOURCE_FILE_ENV)
    os.environ[mcp_server.SOURCE_FILE_ENV] = str(src_file)
    try:
        with mock.patch.object(mcp_server.slither_adapter, "run_slither", spy):
            payload = mcp_server._call_tool("run_slither", {})
    finally:
        if old is None:
            os.environ.pop(mcp_server.SOURCE_FILE_ENV, None)
        else:
            os.environ[mcp_server.SOURCE_FILE_ENV] = old
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    check("MCP run_slither delegates to slither_adapter.run_slither", len(calls) == 1)
    check(
        "MCP run_slither returns a normalized status",
        payload.get("status")
        in ("ok", "no_findings", "compiler_error", "unavailable", "error", "timeout"),
        str(payload.get("status")),
    )
    check(
        "MCP run_slither keeps tool provenance",
        payload.get("tool") == "slither" and "notice" in payload,
    )


def test_mcp_argument_safety() -> None:
    # Tools accept no arguments; any smuggled input is rejected.
    try:
        mcp_server._call_tool("run_slither", {"source": "contract Evil {}"})
        rejected = False
    except mcp_server.ToolError:
        rejected = True
    check("MCP rejects tool arguments (no code/path injection)", rejected)

    # run_slither needs the out-of-band source file; without it -> tool error.
    old = os.environ.pop(mcp_server.SOURCE_FILE_ENV, None)
    try:
        try:
            mcp_server._call_tool("run_slither", {})
            errored = False
        except mcp_server.ToolError:
            errored = True
        check("MCP run_slither errors when no out-of-band source is supplied", errored)
    finally:
        if old is not None:
            os.environ[mcp_server.SOURCE_FILE_ENV] = old

    # The model-facing tool schema must declare no required inputs.
    for t in mcp_server.tool_definitions_for_model():
        params = t["function"]["parameters"]
        check(
            f"model tool '{t['function']['name']}' declares no parameters",
            params.get("properties") == {} and params.get("additionalProperties") is False,
        )


# --------------------------------------------------------------------------
# Approval gate + endpoint behaviour
# --------------------------------------------------------------------------


def test_approval_gate() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    from backend.ollama_client import ToolChatResult

    proposal = ToolChatResult(
        content="",
        tool_calls=[{"function": {"name": "run_slither", "arguments": {"source": "evil"}}}],
        model_used="mock:1",
        elapsed_ms=9,
    )

    ran: list[dict] = []

    async def fake_run_once(source: str):
        ran.append({"source": source})
        return {"tool": "slither", "status": "ok", "summary": "1 finding", "finding_count": 1, "findings": [], "notice": "n"}

    with mock.patch.object(settings, "mcp_state_file", _STATE_TMP), \
         mock.patch.object(ollama_client, "chat_with_tools", return_value=proposal), \
         mock.patch.object(mcp_client, "run_slither_once", fake_run_once):
        r = client.post(
            "/api/chat",
            json={"source": SRC, "question": "check it", "tools_enabled": True},
        )
        body = r.json()
        check("tools-enabled chat -> 200", r.status_code == 200, f"got {r.status_code}")
        check("chat returns a tool proposal", bool(body.get("tool_proposal")))
        check("proposal requires approval", body["tool_proposal"]["requires_approval"] is True)
        check("chat with a proposal does NOT run the tool", ran == [])
        check("proposal reply is empty (nothing answered yet)", body.get("reply") == "")

        # Now approve it.
        with mock.patch.object(ollama_client, "chat", return_value=("Grounded answer.", "mock:1", 12)):
            r2 = client.post(
                "/api/tools/run",
                json={
                    "source": SRC,
                    "question": "check it",
                    "tool": "run_slither",
                },
            )
        b2 = r2.json()
        check("approved tools/run -> 200", r2.status_code == 200, f"got {r2.status_code}")
        check("approved run actually ran the tool", len(ran) == 1)
        check(
            "tool ran on the real source (not the model's smuggled arg)",
            ran and ran[0]["source"] == SRC,
            str(ran),
        )
        check("tools/run returns the grounded reply", b2.get("reply") == "Grounded answer.")
        check("tools/run reports tool_ok", b2.get("tool_ok") is True)


def test_tools_default_off_and_direct_slither() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    # tools default to off: plain chat path, no proposal.
    with mock.patch.object(ollama_client, "chat", return_value=("plain", "mock:1", 5)), \
         mock.patch.object(ollama_client, "chat_with_tools") as cwt:
        r = client.post("/api/chat", json={"source": SRC, "question": "q"})
        check("chat tools default off -> no tool call", r.json().get("tool_proposal") is None)
        check("chat tools default off does not use tool API", not cwt.called)

    # Non-allowlisted tool requests are ignored.
    from backend.ollama_client import ToolChatResult

    bad = ToolChatResult(content="", tool_calls=[{"function": {"name": "rm_rf", "arguments": {}}}], model_used="m", elapsed_ms=3)
    with mock.patch.object(ollama_client, "chat_with_tools", return_value=bad):
        r = client.post("/api/chat", json={"source": SRC, "question": "q", "tools_enabled": True})
        check("non-allowlisted tool -> no proposal", r.json().get("tool_proposal") is None)

    # Direct Slither endpoint is untouched by the bridge.
    with mock.patch.object(settings, "mcp_state_file", _STATE_TMP):
        r = client.post("/api/slither", json={"source": SRC})
    check("direct /api/slither still works", r.status_code == 200, f"got {r.status_code}")
    check(
        "direct /api/slither returns a tool report",
        r.json().get("tool") == "slither" and r.json().get("status") in ("ok", "no_findings"),
        str(r.json().get("status")),
    )

    # Health surfaces the MCP descriptor + knowledge manifest.
    r = client.get("/api/health")
    body = r.json()
    check(
        "health exposes knowledge manifest",
        [k["id"] for k in body.get("knowledge", [])]
        == ["secure-authoring", "audit-reporting", "tool-usage"],
    )
    check(
        "health exposes MCP tools",
        [t["name"] for t in (body.get("mcp") or {}).get("tools", [])]
        == ["analyzer_status", "run_slither"],
    )


def test_mcp_locations_match_direct() -> None:
    """Live: the approved MCP path must report the SAME source locations as the
    direct analyzer path.

    Regression guard for a Windows text-mode temp-file write that turned CRLF
    into CRCRLF and doubled every reported line number on the MCP path.
    """
    from pathlib import Path

    from backend import slither_adapter
    from backend.config import settings

    if not (Path(settings.slither_exe).exists() and Path(settings.solc_exe).exists()):
        print("[SKIP] live MCP location-equality -- canonical install not found")
        return

    sample = Path("N:/Auditor/tools/slither/samples/ReentrancyVault.sol")
    source = sample.read_text(encoding="utf-8") if sample.exists() else SRC

    direct = asyncio.run(slither_adapter.run_slither(source))
    d = sorted((f.check, f.start_line, f.end_line) for f in direct.findings)

    mcp = asyncio.run(mcp_client.run_slither_once(source))
    m = sorted((f["check"], f["start_line"], f["end_line"]) for f in mcp.get("findings", []))
    check("MCP run_slither locations match direct adapter", d == m, f"{d} vs {m}")

    crlf = source.replace("\r\n", "\n").replace("\n", "\r\n")
    mcp_crlf = asyncio.run(mcp_client.run_slither_once(crlf))
    m2 = sorted((f["check"], f["start_line"], f["end_line"]) for f in mcp_crlf.get("findings", []))
    check("MCP run_slither CRLF source locations match LF", d == m2, f"{d} vs {m2}")


def main() -> int:
    test_knowledge()
    test_mcp_client_server()
    test_mcp_delegates_to_adapter()
    test_mcp_argument_safety()
    test_approval_gate()
    test_tools_default_off_and_direct_slither()
    test_mcp_locations_match_direct()

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
