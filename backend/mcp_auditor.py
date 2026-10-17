"""Minimal, local **read-only** MCP server for the Solidity Auditor.

Unlike ``backend/mcp_server.py`` (the *internal*, approval-gated bridge that can
run Slither), this server is the app's **outward** MCP surface. It is meant to
be started by an MCP client (e.g. a desktop assistant or the OpenCode session
you are reading this in) and it can only *read* two things:

- ``analyzer_status``  — which local analyzers are available and their versions,
  using the app's existing status data (``slither_adapter.get_tools_info``).
- ``audit_findings``   — the **current** analysis's deterministic Slither
  findings, using the app's existing normalized result data recorded by
  ``backend/analysis_store.py``. A clear "no results available" response is
  returned when no analysis has been run yet.

Resources (read-only, JSON):

- ``auditor://status/analyzers`` — same data as the ``analyzer_status`` tool.
- ``auditor://findings/current`` — same data as the ``audit_findings`` tool.

Boundaries (deliberately enforced here)
---------------------------------------
- **Read-only.** There is no run-analysis, file-write, code-edit, deploy,
  wallet, signing, or transaction capability. No tool accepts arguments, so a
  client can never inject code, a path, or a command. ``_call_tool`` dispatches
  only the two allowlisted readers below.
- **No model text.** Findings are labeled as deterministic analyzer output and
  the store never contains Qwen text, so model prose can never be mistaken for
  analyzer output.
- **No leaks.** No environment variables, secrets, local filesystem paths, raw
  tool output (``raw_output``), or unrelated machine data are exposed.
- **Local-only.** stdio transport; no socket, no hosted service, no network.

Extension point (not implemented here)
--------------------------------------
A *future*, explicitly user-requested "propose code changes" capability could be
added as a **separate** tool that returns a draft for human review. It must not
write to or overwrite the submitted contract, and any draft must be clearly
marked as a suggestion and checked against applicable standards/analyzers before
being described as reviewed. This module exposes nothing of the sort yet.
"""

from __future__ import annotations

import json
from typing import Any

from . import analysis_store, slither_adapter
from .mcp_protocol import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    error as _error,
    result as _result,
    serve_stdio,
    text_content as _text_content,
)

SERVER_NAME = "auditor-mcp-readonly"
SERVER_VERSION = "0.1.0"

# The canonical tool surface. Both readers take no arguments.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "analyzer_status",
        "description": (
            "Report which local static analyzers are available in this offline "
            "workbench (Slither and the solc compiler) and their versions. "
            "Read-only: takes no arguments and runs no analysis."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "audit_findings",
        "description": (
            "Return the CURRENT audit's deterministic Slither findings "
            "(detector, severity, confidence, location, evidence) from the most "
            "recent analysis run in the Auditor app. Read-only: takes no "
            "arguments. Returns a clear no-results response when no analysis has "
            "been run. AI/model explanations are NOT included in this output."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = tuple(t["name"] for t in TOOL_DEFINITIONS)

# Explicit allowlist — anything not in here is refused. Kept separate so the
# read-only invariant is easy to assert in tests.
READ_ONLY_TOOL_NAMES = TOOL_NAMES

_URI_STATUS = "auditor://status/analyzers"
_URI_FINDINGS = "auditor://findings/current"

RESOURCE_DEFINITIONS: list[dict[str, Any]] = [
    {
        "uri": _URI_STATUS,
        "name": "Analyzer status",
        "description": "Read-only availability and versions of the local analyzers.",
        "mimeType": "application/json",
    },
    {
        "uri": _URI_FINDINGS,
        "name": "Current audit findings",
        "description": (
            "Read-only deterministic Slither findings from the current analysis "
            "(no model/AI text)."
        ),
        "mimeType": "application/json",
    },
]


class ToolError(Exception):
    """A tool-level failure surfaced to the caller as an MCP tool error."""


# --------------------------------------------------------------------------
# Read-only payloads
# --------------------------------------------------------------------------


def _status_payload() -> dict[str, Any]:
    """Available analyzers + versions from the app's existing status data.

    Only name/status/available/version are exposed — never install paths or
    human detail strings. Version probing is read-only (no analysis runs).
    """
    infos = slither_adapter.get_tools_info()
    return {
        "tools": [
            {
                "name": i.name,
                "status": i.status,
                "available": i.available,
                "version": i.version,
            }
            for i in infos
        ],
        "network": "none (offline, local-only)",
        "note": "Availability/version only. This server never runs an analyzer.",
    }


def _findings_payload() -> dict[str, Any]:
    """The current analysis's deterministic Slither findings, or a clear empty state."""
    record = analysis_store.load_current_analysis()
    if record is None:
        return {
            "available": False,
            "message": (
                "No results available. No analysis has been run in the Auditor "
                "app yet, so there are no current findings to show. Run an "
                "analysis (directly or via the approved MCP flow), then query "
                "again."
            ),
            "finding_count": 0,
            "findings": [],
        }
    return {
        "available": True,
        "provenance": (
            "Deterministic Slither output (normalized). AI/model explanations "
            "are NOT included."
        ),
        "recorded_at": record.get("recorded_at"),
        "tool": record.get("tool"),
        "tool_version": record.get("tool_version"),
        "status": record.get("status"),
        "summary": record.get("summary"),
        "source_name": record.get("source_name"),
        "finding_count": record.get("finding_count"),
        "findings_truncated": record.get("findings_truncated", False),
        "findings": record.get("findings") or [],
        "notice": record.get("notice"),
    }


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a read-only tool call.

    ``arguments`` are validated and ignored: no tool takes arguments, and a
    client must never be able to influence what is read or inject code/paths.
    """
    if arguments:
        raise ToolError(f"{name} takes no arguments; received extra input.")
    if name == "analyzer_status":
        return _status_payload()
    if name == "audit_findings":
        return _findings_payload()
    # Note: there is intentionally no run/write/edit branch here.
    raise ToolError(
        f"Unknown or unsupported tool: {name!r}. This server is read-only and "
        f"exposes only {', '.join(TOOL_NAMES)}."
    )


# --------------------------------------------------------------------------
# JSON-RPC / MCP handling
# --------------------------------------------------------------------------


def _handle_initialize(req_id: Any) -> None:
    _result(
        req_id,
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "Read-only view of the local Solidity Auditor. "
                "'analyzer_status' reports local analyzer availability; "
                "'audit_findings' returns the current analysis's deterministic "
                "Slither findings (no model text). This server cannot run "
                "analyzers or modify code."
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
            {"content": [{"type": "text", "text": str(exc)}], "isError": True},
        )
        return
    except Exception as exc:  # pragma: no cover - unexpected tool failure
        _result(
            req_id,
            {"content": [{"type": "text", "text": f"Tool '{name}' failed: {exc}"}], "isError": True},
        )
        return
    _result(req_id, _text_content(payload))


def _handle_resources_read(req_id: Any, params: dict[str, Any]) -> None:
    uri = str(params.get("uri") or "")
    if uri == _URI_STATUS:
        payload = _status_payload()
    elif uri == _URI_FINDINGS:
        payload = _findings_payload()
    else:
        _error(req_id, INVALID_PARAMS, f"Unknown resource: {uri}")
        return
    _result(
        req_id,
        {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(payload, ensure_ascii=False),
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
        _result(req_id, {"resources": RESOURCE_DEFINITIONS})
    elif method == "resources/read":
        _handle_resources_read(req_id, params)
    else:
        _error(req_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def main() -> None:
    """Read newline-delimited JSON-RPC from stdin until EOF."""
    serve_stdio(handle_message, name=SERVER_NAME)


if __name__ == "__main__":
    main()
