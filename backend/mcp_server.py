"""Minimal, dependency-free MCP (Model Context Protocol) server over stdio.

Why this exists
---------------
The official ``mcp`` Python SDK is **not** installed on purpose: it requires
``pydantic>=2.12.0`` (this app pins 2.5.3) and pulls ~12 transitive packages, so
adding it would risk breaking the existing environment. This module speaks the
subset of the MCP stdio protocol that this workbench actually needs, using only
the standard library. See ``research.md`` for the compatibility findings.

What it exposes
---------------
Tools (called by the backend only *after* the user approves):

- ``analyzer_status`` — read-only. Reports which local analyzers are available
  (Slither + solc) and their versions. Takes no arguments.
- ``run_slither`` — runs the **existing** ``slither_adapter.run_slither`` on the
  source under review. Takes **no arguments**: the source is supplied
  out-of-band by the trusted client (see below), so the model can never pass
  code, a path, or a command. This function never re-implements the runner.

Resources (read-only, project-owned guidance):

- ``auditor://knowledge/{secure-authoring,audit-reporting,tool-usage}``

Trust / safety model
--------------------
- stdio is newline-delimited JSON-RPC 2.0. Only MCP data is written to stdout;
  all logging goes to stderr, because a stray stdout line would corrupt the
  protocol.
- The model never supplies the source, a path, or a command. The trusted client
  spawns this server with the environment variable ``AUDITOR_MCP_SOURCE_FILE``
  pointing at a temp file containing the source under review. ``run_slither``
  reads that file, enforces a size cap, and delegates to the existing adapter
  (which itself writes an isolated temp copy, fixes the argv, bounds time, and
  caps output).
- ``run_slither`` is only ever invoked because the *backend* decided to call it
  after explicit user approval; the model's tool request alone does not run it.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from . import knowledge
from . import slither_adapter
from .mcp_protocol import (
    PROTOCOL_VERSION,
    error as _error,
    result as _result,
    serve_stdio,
    text_content as _text_content,
)

# The legacy ``initialize`` handshake we implement. Our own client speaks the
# same revision, so no negotiation surprises.
SERVER_NAME = "auditor-mcp"
SERVER_VERSION = "1.0.0"

# The trusted client provides the source-under-review through this env var
# (a path to a temp file it creates and cleans up). Never set by the model.
SOURCE_FILE_ENV = "AUDITOR_MCP_SOURCE_FILE"

# Hard cap on the source the bridge will read (bytes). Mirrors the app's
# intent: refuse absurdly large inputs rather than forward them to Slither.
MAX_SOURCE_BYTES = 1_000_000

# Cap on how many findings are serialized back to the model (keeps the tool
# result small; the UI still shows the full report via the direct endpoint).
MAX_FINDINGS_RETURNED = 50

# Canonical tool definitions — the single source of truth for both the MCP
# ``tools/list`` response and the Ollama tool schema used by ``main.py``.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "analyzer_status",
        "description": (
            "Report which local static analyzers are available in this offline "
            "workbench (Slither and the solc compiler) and their versions. "
            "Read-only; takes no arguments and runs no analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "run_slither",
        "description": (
            "Run the local Slither static analyzer on the CURRENT source under "
            "review. Takes no arguments: the source is supplied by the host, "
            "not by you, so never pass code, a file path, or a command. Only "
            "call this when the user wants a Slither analysis; the host will "
            "ask the user to approve it before anything runs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = tuple(t["name"] for t in TOOL_DEFINITIONS)


def resource_definitions() -> list[dict[str, Any]]:
    """Read-only resources: the project-owned knowledge modules."""
    out: list[dict[str, Any]] = []
    for mid in knowledge.module_ids():
        module = knowledge.MODULES[mid]
        out.append(
            {
                "uri": knowledge.resource_uri(mid),
                "name": module.title,
                "description": module.description,
                "mimeType": "text/markdown",
            }
        )
    return out


def tool_definitions_for_model() -> list[dict[str, Any]]:
    """Same tools in the OpenAI/Ollama tool schema shape (not MCP shape)."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"],
            },
        }
        for t in TOOL_DEFINITIONS
    ]


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------


class ToolError(Exception):
    """A tool-level failure surfaced to the caller as an MCP tool error."""


def _load_source() -> str:
    """Read the out-of-band source file provided by the trusted client."""
    raw_path = os.environ.get(SOURCE_FILE_ENV)
    if not raw_path:
        raise ToolError(
            "No source was supplied to the analyzer bridge; nothing was analyzed."
        )
    path = Path(raw_path)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ToolError("The supplied source file is no longer available.") from exc
    except OSError as exc:
        raise ToolError(f"Could not read the supplied source file: {exc}") from exc
    if len(data) > MAX_SOURCE_BYTES:
        raise ToolError(
            f"Source exceeds the {MAX_SOURCE_BYTES} byte limit for the bridge."
        )
    return data.decode("utf-8", errors="replace")


def _status_payload() -> dict[str, Any]:
    infos = slither_adapter.get_tools_info()
    return {
        "tools": [
            {
                "name": i.name,
                "status": i.status,
                "available": i.available,
                "version": i.version,
                "detail": i.detail,
            }
            for i in infos
        ],
        "network": "none (offline, local-only)",
    }


def _finding_brief(f: Any) -> dict[str, Any]:
    return {
        "check": f.check,
        "severity": f.severity,
        "impact": f.impact,
        "confidence": f.confidence,
        "file": f.file,
        "start_line": f.start_line,
        "end_line": f.end_line,
        "title": f.title,
    }


def _run_slither_payload() -> dict[str, Any]:
    """Delegate to the existing adapter (never re-implement the runner)."""
    source = _load_source()
    resp = asyncio.run(slither_adapter.run_slither(source))
    findings = [_finding_brief(f) for f in resp.findings]
    truncated = len(findings) > MAX_FINDINGS_RETURNED
    return {
        "tool": resp.tool,
        "tool_version": resp.tool_version,
        "status": resp.status,
        "summary": resp.summary,
        "finding_count": resp.finding_count,
        "findings": findings[:MAX_FINDINGS_RETURNED],
        "findings_truncated": truncated,
        "notice": resp.notice,
    }


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a tool call. Only allowlisted names are ever run.

    ``arguments`` are validated and ignored: neither tool takes arguments, and
    the model must not be able to influence what is analyzed.
    """
    if arguments:
        # Defensive: reject any attempt to smuggle code/paths/commands in.
        raise ToolError(f"{name} takes no arguments; received extra input.")
    if name == "analyzer_status":
        return _status_payload()
    if name == "run_slither":
        return _run_slither_payload()
    raise ToolError(f"Unknown tool: {name}")


# --------------------------------------------------------------------------
# JSON-RPC / MCP plumbing (transport framing lives in ``mcp_protocol``)
# --------------------------------------------------------------------------


def _handle_initialize(req_id: Any) -> None:
    _result(
        req_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Local, offline code-review workbench. 'run_slither' analyzes the "
                "current source under review; it never accepts code or paths. "
                "Findings are evidence and must not be altered or re-attributed."
            ),
        },
    )


def _handle_tools_call(req_id: Any, params: dict[str, Any]) -> None:
    name = str(params.get("name") or "")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        payload = _call_tool(name, arguments)
    except ToolError as exc:
        _result(
            req_id,
            {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            },
        )
        return
    except Exception as exc:  # pragma: no cover - unexpected tool failure
        _result(
            req_id,
            {
                "content": [
                    {"type": "text", "text": f"Tool '{name}' failed: {exc}"}
                ],
                "isError": True,
            },
        )
        return
    _result(req_id, _text_content(payload))


def _handle_resources_read(req_id: Any, params: dict[str, Any]) -> None:
    uri = str(params.get("uri") or "")
    prefix = "auditor://knowledge/"
    if not uri.startswith(prefix):
        _error(req_id, -32602, f"Unknown resource: {uri}")
        return
    mid = uri[len(prefix) :]
    if mid not in knowledge.MODULES:
        _error(req_id, -32602, f"Unknown resource: {uri}")
        return
    try:
        body = knowledge.load_body(mid)
    except knowledge.KnowledgeLoadError as exc:
        _error(req_id, -32603, str(exc))
        return
    _result(
        req_id,
        {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/markdown",
                    "text": body,
                }
            ]
        },
    )


def handle_message(msg: dict[str, Any]) -> None:
    """Handle one decoded JSON-RPC message (request or notification)."""
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if not isinstance(params, dict):
        params = {}

    # Notifications (no id) never get a response.
    if method == "notifications/initialized":
        return
    if req_id is None:
        # Any other notification: ignore quietly (protocol allows it).
        return

    if method == "initialize":
        _handle_initialize(req_id)
    elif method == "ping":
        _result(req_id, {})
    elif method in ("server/discover", "discover"):
        # Dual-era friendliness: answer the modern discovery probe too.
        _result(
            req_id,
            {
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
            },
        )
    elif method == "tools/list":
        _result(req_id, {"tools": TOOL_DEFINITIONS})
    elif method == "tools/call":
        _handle_tools_call(req_id, params)
    elif method == "resources/list":
        _result(req_id, {"resources": resource_definitions()})
    elif method == "resources/read":
        _handle_resources_read(req_id, params)
    else:
        _error(req_id, -32601, f"Method not found: {method}")


def main() -> None:
    """Read newline-delimited JSON-RPC from stdin until EOF."""
    serve_stdio(handle_message, name=SERVER_NAME)


if __name__ == "__main__":
    main()
