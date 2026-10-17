"""Reusable stdio JSON-RPC 2.0 plumbing for the local MCP servers.

Two local MCP servers live in this project and both speak the same
newline-delimited JSON-RPC 2.0 dialect over stdio:

- ``backend/mcp_server.py``  — the approval-gated *bridge* (tools
  ``analyzer_status`` + ``run_slither``), spawned per approved call.
- ``backend/mcp_auditor.py`` — the long-lived *read-only* outward server
  (analyzer status + current findings), spawned by an MCP client.

This module holds only the transport framing and the stdin read loop so neither
server re-implements them.

Rules kept from the bridge:

- Only MCP data is written to stdout; all diagnostics go to stderr, because a
  stray stdout line corrupts the protocol.
- Notifications (no ``id``) never receive a response.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

# The legacy ``initialize`` handshake revision implemented by both servers. Our
# own client speaks the same revision, so no negotiation surprises.
PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC error codes used by both servers.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def send(obj: dict[str, Any]) -> None:
    """Write one JSON-RPC message to stdout (protocol data only)."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def result(req_id: Any, result: dict[str, Any]) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def error(req_id: Any, code: int, message: str) -> None:
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def text_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a structured tool payload as an MCP tool result.

    ``structuredContent`` is the machine-readable half; the ``text`` half is the
    same JSON so text-only clients still work.
    """
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": False,
    }


def serve_stdio(handler: Callable[[dict[str, Any]], None], *, name: str) -> None:
    """Read newline-delimited JSON-RPC from stdin until EOF.

    ``handler`` receives one decoded message per line and is responsible for
    writing any response. Malformed lines and handler exceptions are reported as
    JSON-RPC errors rather than crashing the loop.
    """
    print(f"[{name}] started (stdio, offline)", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            error(None, PARSE_ERROR, "Parse error")
            continue
        if not isinstance(msg, dict):
            error(None, INVALID_REQUEST, "Invalid request")
            continue
        try:
            handler(msg)
        except Exception as exc:  # pragma: no cover - safety net
            print(f"[{name}] handler error: {exc}", file=sys.stderr, flush=True)
            error(msg.get("id"), INTERNAL_ERROR, f"Internal error: {exc}")
    print(f"[{name}] stdin closed, exiting", file=sys.stderr, flush=True)


__all__ = [
    "PROTOCOL_VERSION",
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    "send",
    "result",
    "error",
    "text_content",
    "serve_stdio",
]
