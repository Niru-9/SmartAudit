"""Pydantic request/response models for the Phase 1 vertical slice.

All messages keep the interface simple and forward-compatible with later
security-tool adapters. No analyzer-specific fields are required in this phase.

Notes on bounds:
- Solidity source is text only; length is not capped here but the caller
  validates for empty input.
- History is kept bounded on the frontend/backend to limit tokens; the
  `history` array is small by design.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Base model that permits descriptive field names like `model_default`."""

    model_config = ConfigDict(protected_namespaces=())


class Message(ApiModel):
    """A single chat turn. We only accept plain strings for Phase 1."""

    role: Literal["user", "assistant"]
    content: str = Field(min_length=0)


class ReviewRequest(ApiModel):
    """Request to review a Solidity contract with the local model."""

    source: str = Field(min_length=1)
    model: str | None = Field(default=None)


class ReviewResponse(ApiModel):
    """Explanation-only review response (no suggested code split here)."""

    model: str
    content: str
    notice: str
    elapsed_ms: int


class SuggestRequest(ApiModel):
    """Request to suggest a safer version of the Solidity contract."""

    source: str = Field(min_length=1)
    model: str | None = Field(default=None)
    review: str | None = Field(default=None)


class SuggestResponse(ApiModel):
    """Suggested code in a separate panel, never overwriting source."""

    model: str
    notes: str
    suggested_code: str | None = Field(default=None)
    notice: str
    elapsed_ms: int


class ChatRequest(ApiModel):
    """Follow-up Q&A with the source and prior conversation in context."""

    source: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)
    model: str | None = Field(default=None)
    # Phase 4: when true, the model may request an allowlisted local tool via
    # the MCP bridge. The request itself never runs anything — it only returns
    # a proposal the user must approve.
    tools_enabled: bool = Field(default=False)


class ToolCallProposal(ApiModel):
    """A tool the model asked to run, pending explicit user approval.

    Nothing has executed when this is returned. It carries only an allowlisted
    tool name and the (empty) arguments the model proposed.
    """

    id: str
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)
    requires_approval: bool = True
    summary: str


class ChatResponse(ApiModel):
    """Follow-up reply, or a pending tool-call proposal.

    When `tool_proposal` is set, `reply` is empty: the model wants to call a
    local tool and is waiting for the user to approve it.
    """

    model: str
    reply: str
    notice: str
    elapsed_ms: int
    tool_proposal: ToolCallProposal | None = Field(default=None)


class ToolRunRequest(ApiModel):
    """User-approved execution of a proposed allowlisted tool.

    The source and question are re-sent so the backend can run the tool and
    then ask the model for a final answer grounded in the tool result.
    """

    source: str = Field(min_length=1)
    question: str = Field(min_length=1)
    history: list[Message] = Field(default_factory=list)
    model: str | None = Field(default=None)
    tool: Literal["run_slither", "analyzer_status"]


class ToolRunResponse(ApiModel):
    """Result of an approved tool run plus the model's grounded follow-up."""

    model: str
    tool: str
    tool_ok: bool
    tool_result: dict[str, object]
    reply: str
    notice: str
    elapsed_ms: int


# --- Normalized analyzer report (Phase 3: Slither) ------------------------


Severity = Literal["critical", "high", "medium", "low", "informational", "unknown"]
RunStatus = Literal[
    "ok",  # ran and reported findings
    "no_findings",  # ran and reported none (NOT a safety guarantee)
    "unavailable",  # not installed / not found; nothing ran
    "compiler_error",  # source could not be parsed/compiled; nothing reported
    "timeout",  # exceeded the wall-clock limit; nothing reported
    "error",  # unexpected tool failure; nothing reported
    "skipped",  # deliberately not attempted for this run
]


class ToolLocation(ApiModel):
    """A single source location for a tool finding.

    `file` is a neutral label (the submitted file's basename), never an
    absolute local path.
    """

    file: str
    start_line: int | None = None
    end_line: int | None = None
    start_column: int | None = None
    end_column: int | None = None


class ToolFinding(ApiModel):
    """Normalized finding produced by a deterministic analyzer.

    Provenance is explicit: `kind` distinguishes tool output from model
    output, and the reviewer must not alter these fields.
    """

    id: str
    kind: Literal["tool"] = "tool"
    status: Literal["confirmed"] = "confirmed"
    tool: str
    tool_version: str | None = None
    check: str  # detector / check identifier, e.g. "reentrancy-eth"
    severity: Severity  # normalized from the tool's impact
    impact: str  # original impact string, preserved verbatim
    confidence: str  # original confidence string, preserved verbatim
    title: str
    description: str
    reference: str | None = None
    file: str
    start_line: int | None = None
    end_line: int | None = None
    locations: list[ToolLocation] = Field(default_factory=list)
    evidence: str | None = None


class ToolInfo(ApiModel):
    """Availability/version status for one tool."""

    name: str
    status: Literal["available", "unavailable", "not_integrated"]
    available: bool
    version: str | None = None
    detail: str | None = None


class SlitherRequest(ApiModel):
    """Request to run the local Slither analyzer on the submitted source."""

    source: str = Field(min_length=1)


class SlitherResponse(ApiModel):
    """Result of a Slither run.

    A run always returns a structured response (including `unavailable`,
    `timeout`, `compiler_error`, `error`) so the UI can show partial results;
    HTTP-level validation errors (empty source) are separate.
    """

    tool: str
    tool_version: str | None = None
    status: RunStatus
    summary: str
    finding_count: int
    findings: list[ToolFinding] = Field(default_factory=list)
    source_name: str
    duration_ms: int
    notice: str
    # Short, path-redacted human detail (e.g. first compile error line).
    detail: str | None = None
    # Bounded, path-redacted raw process output for troubleshooting.
    raw_output: str | None = None
    raw_output_truncated: bool = False


class SolhintResponse(ApiModel):
    """Result of a Solhint run (added 2026-10-17).

    Same shape and status vocabulary as :class:`SlitherResponse`, but every
    finding is attributed to ``tool="solhint"`` and is never merged with another
    tool's findings. Solhint is a linter: a ``no_findings`` run is not a safety
    statement.
    """

    tool: str = "solhint"
    tool_version: str | None = None
    status: RunStatus
    summary: str
    finding_count: int
    findings: list[ToolFinding] = Field(default_factory=list)
    source_name: str
    duration_ms: int
    notice: str
    detail: str | None = None
    raw_output: str | None = None
    raw_output_truncated: bool = False


class ToolRunSummary(ApiModel):
    """One tool's run summary carried inside a larger payload.

    Used where a per-analyzer result travels inside another response (e.g. the
    retrieved-source analysis): it mirrors the tool-level fields of
    ``SlitherResponse`` / ``SolhintResponse`` while omitting ``source_name`` and
    ``notice``. Each entry keeps its own ``tool`` attribution and ``status``, so
    one tool's output is never merged into (or presented as) another's, and a
    tool that did not run is reported with its honest status.
    """

    tool: str
    tool_version: str | None = None
    status: RunStatus
    summary: str = ""
    finding_count: int = 0
    findings: list[ToolFinding] = Field(default_factory=list)
    duration_ms: int = 0
    detail: str | None = None
    raw_output: str | None = None
    raw_output_truncated: bool = False


class AnalyzerRun(ApiModel):
    """One tool's normalized run inside a combined analyzer response.

    Structurally identical to ``SlitherResponse`` / ``SolhintResponse``; used by
    the combined ``/api/analyze`` endpoint so each tool keeps its own status,
    version, summary, and findings (no cross-tool merging).
    """

    tool: str
    tool_version: str | None = None
    status: RunStatus
    summary: str
    finding_count: int
    findings: list[ToolFinding] = Field(default_factory=list)
    source_name: str
    duration_ms: int
    notice: str
    detail: str | None = None
    raw_output: str | None = None
    raw_output_truncated: bool = False


class AnalyzeRequest(ApiModel):
    """Request to run the local analyzers on the submitted source.

    ``tools`` optionally restricts the run to a subset of the known analyzers
    (by name, e.g. ``["slither"]``); any known tool not listed is reported with
    status ``skipped``. When omitted, every known analyzer is attempted.
    """

    source: str = Field(min_length=1)
    tools: list[str] | None = Field(default=None)


class AnalyzeResponse(ApiModel):
    """Combined per-tool analyzer result.

    Each entry in ``tools`` is an independent run with its own status, version,
    and findings. Ordering is stable and never implies that one tool's output
    depends on another's.
    """

    source_name: str
    tools: list[AnalyzerRun] = Field(default_factory=list)
    duration_ms: int
    notice: str


class ExplainFindingRequest(ApiModel):
    """Request to explain a specific analyzer finding with local Qwen."""
    source: str = Field(min_length=1)
    model: str | None = Field(default=None)
    finding: ToolFinding = Field(...)


class ExplainFindingResponse(ApiModel):
    """AI-generated explanation of a specific finding (read-only)."""
    model: str
    content: str
    notice: str
    elapsed_ms: int


class ExportToolRun(ApiModel):
    """One tool's run status + findings for the downloadable report.

    Kept separate per tool so the report can state, for each analyzer, whether it
    ran with findings, ran with none, was unavailable, skipped, or errored —
    without implying that a tool ran when it did not.
    """

    tool: str
    status: RunStatus
    tool_version: str | None = None
    summary: str = ""
    findings: list[ToolFinding] = Field(default_factory=list)


class ExportReportRequest(ApiModel):
    """Request to export the current audit report.

    ``findings``/``explanations`` remain the original flat fields (back-compat).
    ``tool_runs`` (added 2026-10-17) carries the per-analyzer status + findings
    so the report can attribute each result to its tool; ``qwen_review`` carries
    the separate local-Qwen review text. Nothing here is ever produced by a tool.
    """

    source: str = Field(min_length=1)
    findings: list[ToolFinding] = Field(default_factory=list)
    explanations: dict[str, str] = Field(default_factory=dict)  # finding_id -> explanation content
    # Per-tool run summaries (optional; when present the report is grouped by tool).
    tool_runs: list[ExportToolRun] = Field(default_factory=list)
    # Separate local-model review (AI-generated; never tool output).
    qwen_review: str | None = Field(default=None)
    qwen_model: str | None = Field(default=None)
    # Provenance of the analyzed source, when applicable.
    scope: str | None = Field(default=None)
    chain_id: int | None = Field(default=None)
    chain_name: str | None = Field(default=None)
    address: str | None = Field(default=None)


class ExportReportResponse(ApiModel):
    """Exported report content."""

    report: str
    filename: str


class KnowledgeInfo(ApiModel):
    """One project-owned knowledge/prompt module (Goal A)."""

    id: str
    title: str
    filename: str
    version: str
    chars: int | None = None


class McpToolInfo(ApiModel):
    """A tool exposed by the local MCP bridge."""

    name: str
    description: str = ""


class McpResourceInfo(ApiModel):
    """A read-only resource exposed by the local MCP bridge."""

    uri: str
    name: str = ""
    description: str | None = None
    mime_type: str | None = None


class McpStatus(ApiModel):
    """Status of the local stdio MCP bridge (no hosted service involved)."""

    enabled: bool = True
    transport: str = "stdio"
    server: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    # True when the values came from a live spawn of the server.
    live: bool = False
    tools: list[McpToolInfo] = Field(default_factory=list)
    resources: list[McpResourceInfo] = Field(default_factory=list)
    detail: str | None = None


class HealthResponse(ApiModel):
    """Health check reporting model availability and tool state.

    `tools` keeps the original string map for the frontend/back-compat.
    Phase 3: slither/solc now report real availability; the remaining
    analyzers are still not integrated.
    Phase 4: `knowledge` lists the project-owned prompt modules and `mcp`
    describes the local stdio bridge.
    """

    status: str  # "ok" or "model_unavailable"
    ollama_url: str
    model_default: str
    models: list[str]
    tools: dict[str, str] = Field(
        default_factory=lambda: {
            "slither": "unavailable",
            "solc": "unavailable",
            "solhint": "unavailable",
            "mythril": "not_integrated",
            "echidna": "not_integrated",
            "aderyn": "not_integrated",
            "foundry": "not_integrated",
        }
    )
    # Structured detail (name/status/version) for the tool-status UI.
    tool_details: list[ToolInfo] = Field(default_factory=list)
    # Project-owned knowledge modules loaded into prompt assembly.
    knowledge: list[KnowledgeInfo] = Field(default_factory=list)
    # Local MCP bridge status (tools/resources advertised).
    mcp: McpStatus | None = Field(default=None)
    notice: str


class ChainOption(ApiModel):
    """One selectable network for the (future) read-only address review."""

    chain_id: int
    name: str
    is_testnet: bool = False


class DeployedConfigResponse(ApiModel):
    """Deployed-contract review panel configuration (read-only, no I/O).

    Read-only and offline: this describes choices only. ``lookup_available`` is
    True once a consent-gated provider exists (Phase 6.2), but ``default_chain_id``
    is ``None`` so the network stays unselected until the user picks one, and no
    source is fetched until an explicit lookup request carries consent.
    """

    phase: str
    lookup_available: bool
    chains: list[ChainOption] = Field(default_factory=list)
    default_chain_id: int | None = None
    consent_text: str
    read_only_notice: str
    notice: str


class DeployedAddressRequest(ApiModel):
    """Local validation request. ``address`` is the only field that is read."""

    address: str = Field(default="", max_length=128)
    chain_id: int | None = Field(default=None)
    consent: bool = Field(default=False)


class DeployedAddressResponse(ApiModel):
    """Offline address-validation result for the deployed-review panel.

    ``lookup_performed`` is always ``False`` in this phase: nothing is fetched
    and no chain/RPC/explorer is contacted.
    """

    input: str
    valid: bool
    reason: str
    message: str
    normalized: str | None = None
    checksum: str | None = None
    checksum_provided: bool = False
    checksum_ok: bool | None = None
    is_mixed_case: bool = False
    chain_id: int | None = None
    consent: bool = False
    lookup_performed: bool = False
    lookup_available: bool = False
    notice: str


class DeployedLookupRequest(ApiModel):
    """Explicit, consent-gated verified-source lookup (Ethereum networks only).

    The backend performs **no** lookup unless ``consent`` is true and
    ``chain_id`` is a supported Ethereum network (mainnet or a
    Sourcify-supported Ethereum testnet); there is no default chain.
    ``address`` is validated locally first.
    """

    address: str = Field(default="", max_length=128)
    chain_id: int | None = Field(default=None)
    consent: bool = Field(default=False)


class DeployedSourceFile(ApiModel):
    """One retrieved verified source file (path + content, size-bounded)."""

    path: str
    chars: int = 0
    content: str = ""


class DeployedLookupResponse(ApiModel):
    """Read-only verified-source lookup result.

    ``status`` is a machine code (``verified`` | ``not_verified`` |
    ``invalid_address`` | ``unsupported_chain`` | ``consent_required`` |
    ``rate_limited`` | ``timeout`` | ``provider_error`` | ``too_large``).
    ``lookup_performed`` is true only when the provider was actually called.
    Never carries any provider key or secret.
    """

    status: str
    lookup_performed: bool = False
    consent: bool = False
    chain_id: int | None = None
    chain_name: str | None = None
    address: str = ""
    checksum: str | None = None
    provider: str = "sourcify"
    provider_docs: str = ""
    verified: bool = False
    match: str | None = None
    creation_match: str | None = None
    runtime_match: str | None = None
    verified_at: str | None = None
    match_id: str | None = None
    contract_name: str | None = None
    compiler_version: str | None = None
    language: str | None = None
    deployment_block: str | None = None
    sources: list[DeployedSourceFile] = Field(default_factory=list)
    sources_truncated: bool = False
    source_file_count: int = 0
    abi_entries: int = 0
    metadata_present: bool = False
    message: str = ""
    notice: str


class DeployedAnalyzeRequest(ApiModel):
    """Explicit request to analyze the *current* verified lookup's source.

    The caller resends the exact ``sources`` returned by the lookup for the
    currently selected ``address`` + ``chain_id`` so the analysis is provably
    associated with that lookup. ``sources_truncated`` and ``language`` let the
    backend refuse to analyze incomplete or non-Solidity source. There is no
    default chain and no implicit lookup: this endpoint never fetches anything.
    """

    address: str = Field(default="", max_length=128)
    chain_id: int | None = Field(default=None)
    sources: list[DeployedSourceFile] = Field(default_factory=list)
    sources_truncated: bool = Field(default=False)
    language: str | None = Field(default=None)


class DeployedAnalyzeResponse(ApiModel):
    """Result of analyzing the retrieved verified source with the local analyzer.

    ``allowed`` is true only when the source is a single, complete, non-empty
    Solidity file for a supported Ethereum network; otherwise it is false with a
    plain ``reason`` and ``analyzer`` is ``None`` (nothing ran). When allowed,
    ``analyzer`` holds the unchanged normalized ``SlitherResponse`` — the same
    shape and provenance as a pasted-source run. ``kind:"tool"`` findings remain
    authoritative; Qwen never appears here. ``analyzers`` carries per-tool
    summaries (including Solhint) when available.
    """

    allowed: bool = False
    reason_code: str | None = None
    reason: str | None = None
    address: str = ""
    checksum: str | None = None
    chain_id: int | None = None
    chain_name: str | None = None
    source_file_count: int = 0
    source_path: str | None = None
    language: str | None = None
    limitation: str | None = None
    analyzer: SlitherResponse | None = None
    # The same retrieved source is also linted locally with Solhint. Kept as a
    # separate, attributed run (never merged into `analyzer`), and `None` when
    # no run produced it (a refusal, or an unavailable linter).
    solhint: SolhintResponse | None = None
    analyzers: list[ToolRunSummary] = Field(default_factory=list)


# Compatibility alias: the Solhint endpoint accepts the same request shape as
# Slither (a single `source` field). `SolhintResponse` is deliberately NOT
# aliased — each tool keeps its own model identity.
SolhintRequest = SlitherRequest


__all__ = [
    "ApiModel",
    "Message",
    "ReviewRequest",
    "ReviewResponse",
    "SuggestRequest",
    "SuggestResponse",
    "ChatRequest",
    "ChatResponse",
    "ToolCallProposal",
    "ToolRunRequest",
    "ToolRunResponse",
    "HealthResponse",
    "KnowledgeInfo",
    "McpToolInfo",
    "McpResourceInfo",
    "McpStatus",
    "Severity",
    "RunStatus",
    "ToolLocation",
    "ToolFinding",
    "ToolInfo",
    "SlitherRequest",
    "SlitherResponse",
    "SolhintRequest",
    "SolhintResponse",
    "ToolRunSummary",
    "AnalyzerRun",
    "AnalyzeRequest",
    "AnalyzeResponse",
    "ExplainFindingRequest",
    "ExplainFindingResponse",
    "ExportToolRun",
    "ExportReportRequest",
    "ExportReportResponse",
    "ChainOption",
    "DeployedConfigResponse",
    "DeployedAddressRequest",
    "DeployedAddressResponse",
    "DeployedLookupRequest",
    "DeployedSourceFile",
    "DeployedLookupResponse",
    "DeployedAnalyzeRequest",
    "DeployedAnalyzeResponse",
]
