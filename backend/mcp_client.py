"""Minimal, dependency-free MCP (Model Context Protocol) *client* over stdio.

This is the trusted half of the bridge. It spawns ``backend.mcp_server`` as a
subprocess and speaks newline-delimited JSON-RPC 2.0 over its stdin/stdout.
It is used by the backend only after the user explicitly approves a proposed
tool call — the model can never spawn this, and it can never supply the source.

Design notes
------------
- Uses only the standard library (``asyncio`` subprocesses). No third-party MCP
  SDK is required; see ``research.md`` for why the official SDK is not used.
- The source under review is handed to the server **out-of-band** via a temp
  file whose path is passed in the ``AUDITOR_MCP_SOURCE_FILE`` environment
  variable. The model never sees or supplies it.
- Every call is bounded by a wall-clock timeout; the subprocess is always
  terminated and the temp file always removed.
- ``run_slither_once`` is the high-level convenience used by the API: it does
  the whole spawn → initialize → tools/call → teardown cycle in one shot.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, settings
from . import mcp_server

# Bounded per-call timeout. Slither itself is bounded separately by
# settings.analyzer_timeout inside the adapter; this is the outer ceiling for
# process startup + handshake + the tool call.
_HANDSHAKE_TIMEOUT = 30.0
_CALL_TIMEOUT = float(settings.analyzer_timeout) + 60.0

_CREATE_NO_WINDOW = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0)


class McpClientError(Exception):
    """Raised when the MCP bridge cannot complete a request."""


@dataclass
class McpTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpResource:
    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""


class McpClient:
    """A short-lived stdio MCP client session.

    Usage::

        async with McpClient(source=src) as client:
            await client.initialize()
            result = await client.call_tool("run_slither")
    """

    def __init__(self, *, source: str | None = None, call_timeout: float | None = None):
        self._source = source
        self._call_timeout = call_timeout or _CALL_TIMEOUT
        self._proc: asyncio.subprocess.Process | None = None
        self._source_dir: Path | None = None
        self._source_file: Path | None = None
        self._next_id = 0
        self.server_info: dict[str, Any] = {}
        self.protocol_version: str | None = None

    # -- lifecycle ---------------------------------------------------------

    async def __aenter__(self) -> "McpClient":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        # Out-of-band source delivery (never via the model).
        if self._source is not None:
            self._source_dir = Path(
                tempfile.mkdtemp(prefix="auditor-mcp-", dir=str(self._work_root()))
            )
            self._source_file = self._source_dir / "Submitted.sol"
            # Byte-exact handoff: never translate newlines. A text-mode write on
            # Windows would rewrite LF as CRLF, and the adapter's own write would
            # then double every reported line number.
            self._source_file.write_bytes(self._source.encode("utf-8"))
            env[mcp_server.SOURCE_FILE_ENV] = str(self._source_file)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "backend.mcp_server",
                cwd=str(REPO_ROOT),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as exc:  # pragma: no cover - spawn failure
            self._cleanup_source()
            raise McpClientError(f"Could not start the MCP server: {exc}") from exc

    def _work_root(self) -> Path:
        root = Path(settings.work_dir)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            root = Path(tempfile.gettempdir())
        return root

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass
        self._cleanup_source()

    def _cleanup_source(self) -> None:
        if self._source_dir is not None:
            shutil.rmtree(self._source_dir, ignore_errors=True)
        self._source_dir = None
        self._source_file = None

    # -- protocol ----------------------------------------------------------

    async def _read_message(self, timeout: float) -> dict[str, Any]:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise McpClientError("Timed out waiting for the MCP server.")
            try:
                raw = await asyncio.wait_for(stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError as exc:
                raise McpClientError("Timed out waiting for the MCP server.") from exc
            if not raw:
                raise McpClientError("The MCP server closed its output unexpectedly.")
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                # Not valid protocol data; skip it rather than crash.
                continue

    async def _request(
        self, method: str, params: dict[str, Any] | None, timeout: float
    ) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None:
            raise McpClientError("MCP client is not running.")
        self._next_id += 1
        req_id = self._next_id
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            message["params"] = params
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
        except Exception as exc:
            raise McpClientError(f"Could not write to the MCP server: {exc}") from exc

        resp = await self._read_message(timeout)
        if resp.get("id") != req_id:
            # Out-of-order notifications can arrive first; tolerate a single
            # mismatch by reading until we see our id (bounded by timeout).
            while resp.get("id") != req_id:
                resp = await self._read_message(timeout)
        if "error" in resp:
            err = resp["error"] or {}
            raise McpClientError(
                f"MCP error {err.get('code')}: {err.get('message')}"
            )
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpClientError("MCP client is not running.")
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._proc.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        )
        await self._proc.stdin.drain()

    async def initialize(self) -> dict[str, Any]:
        result = await self._request(
            "initialize",
            {
                "protocolVersion": mcp_server.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "auditor-backend", "version": "0.3.0"},
            },
            _HANDSHAKE_TIMEOUT,
        )
        self.server_info = result.get("serverInfo") or {}
        self.protocol_version = result.get("protocolVersion")
        try:
            await self._notify("notifications/initialized")
        except Exception:
            pass
        return result

    async def list_tools(self) -> list[McpTool]:
        result = await self._request("tools/list", {}, _HANDSHAKE_TIMEOUT)
        tools = []
        for t in result.get("tools", []) or []:
            if not isinstance(t, dict):
                continue
            tools.append(
                McpTool(
                    name=str(t.get("name") or ""),
                    description=str(t.get("description") or ""),
                    input_schema=t.get("inputSchema") or {},
                )
            )
        return tools

    async def list_resources(self) -> list[McpResource]:
        result = await self._request("resources/list", {}, _HANDSHAKE_TIMEOUT)
        out = []
        for r in result.get("resources", []) or []:
            if not isinstance(r, dict):
                continue
            out.append(
                McpResource(
                    uri=str(r.get("uri") or ""),
                    name=str(r.get("name") or ""),
                    description=str(r.get("description") or ""),
                    mime_type=str(r.get("mimeType") or ""),
                )
            )
        return out

    async def read_resource(self, uri: str) -> str:
        result = await self._request(
            "resources/read", {"uri": uri}, _HANDSHAKE_TIMEOUT
        )
        contents = result.get("contents") or []
        if contents and isinstance(contents[0], dict):
            return str(contents[0].get("text") or "")
        return ""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Call a tool and return its structured payload.

        Raises McpClientError on protocol failure or when the tool reports an
        error (``isError`` true).
        """
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            self._call_timeout,
        )
        if result.get("isError"):
            text = ""
            for item in result.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = str(item.get("text") or "")
                    break
            raise McpClientError(text or f"Tool '{name}' reported an error.")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        # Fallback: parse the text content as JSON.
        for item in result.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    parsed = json.loads(item.get("text") or "")
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue
        return {}


# --------------------------------------------------------------------------
# High-level helpers used by the API
# --------------------------------------------------------------------------


async def call_analyzer_status() -> dict[str, Any]:
    """Spawn the server, call analyzer_status, tear down. Bounded + cleaned up."""
    async with McpClient() as client:
        await client.initialize()
        return await client.call_tool("analyzer_status")


async def run_slither_once(source: str) -> dict[str, Any]:
    """Approve-and-run path: analyze `source` via the MCP bridge.

    The source is delivered out-of-band to the server; the model never supplies
    it. The existing ``slither_adapter.run_slither`` does the actual work.
    """
    async with McpClient(source=source) as client:
        await client.initialize()
        return await client.call_tool("run_slither")


async def describe_server() -> dict[str, Any]:
    """Live capability probe: spawn the server and list its tools + resources."""
    async with McpClient() as client:
        init = await client.initialize()
        tools = await client.list_tools()
        resources = await client.list_resources()
        return {
            "server_info": client.server_info,
            "protocol_version": client.protocol_version,
            "tools": [
                {"name": t.name, "description": t.description} for t in tools
            ],
            "resources": [
                {
                    "uri": r.uri,
                    "name": r.name,
                    "description": r.description,
                    "mime_type": r.mime_type,
                }
                for r in resources
            ],
            "raw_init": init,
        }


__all__ = [
    "McpClient",
    "McpClientError",
    "McpTool",
    "McpResource",
    "call_analyzer_status",
    "run_slither_once",
    "describe_server",
]
