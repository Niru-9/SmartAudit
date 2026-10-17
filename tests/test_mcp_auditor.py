"""Phase 6 tests: minimal, local, **read-only** MCP server for the Auditor.

Covers ``backend/mcp_auditor.py`` (the outward read-only MCP surface) and the
``backend/analysis_store.py`` record it reads. Two groups:

1. Offline: the read-only surface (no run/write/exec capability, no argument
   intake), the empty state, current-analysis-only behaviour, and leak
   invariants (no raw output / paths / model text).
2. Live-ish: a real stdio round-trip against ``py -m backend.mcp_auditor`` (the
   actual entry point), plus an optional guarded analyzer-status probe.

Run:  py tests/test_mcp_auditor.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from backend import analysis_store, mcp_auditor, ollama_client, slither_adapter  # noqa: E402
from backend import mcp_client  # noqa: E402
from backend.config import REPO_ROOT, settings  # noqa: E402
from backend.main import app  # noqa: E402
from backend.schemas import SlitherResponse, ToolFinding, ToolLocation  # noqa: E402

SRC = "pragma solidity ^0.8.20; contract A { function f() external {} }"
results: list[bool] = []
_TEMP_DIRS: list[Path] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _temp_state() -> Path:
    d = Path(tempfile.mkdtemp(prefix="auditor-mcp-auditor-test-"))
    _TEMP_DIRS.append(d)
    return d / "current-analysis.json"


def _finding(
    check_name: str,
    *,
    severity: str = "high",
    impact: str = "High",
    confidence: str = "Medium",
    start: int = 10,
    end: int = 12,
    file: str = "Submitted.sol",
) -> ToolFinding:
    return ToolFinding(
        id=f"{check_name}:{start}-{end}",
        tool="slither",
        tool_version="9.9.9",
        check=check_name,
        severity=severity,  # type: ignore[arg-type]
        impact=impact,
        confidence=confidence,
        title=f"{check_name} title",
        description=f"{check_name} description",
        reference="https://example.invalid/ref",
        file=file,
        start_line=start,
        end_line=end,
        locations=[ToolLocation(file=file, start_line=start, end_line=end)],
        evidence="someElement",
    )


def _response(findings: list[ToolFinding], *, status: str = "ok") -> SlitherResponse:
    return SlitherResponse(
        tool="slither",
        tool_version="9.9.9",
        status=status,  # type: ignore[arg-type]
        summary=f"Slither reported {len(findings)} finding(s) for this run.",
        finding_count=len(findings),
        findings=findings,
        source_name="Submitted.sol",
        duration_ms=5,
        notice="Deterministic analyzer output; not a complete audit.",
        # Deliberately poison these fields to prove they never reach the surface.
        detail="LEAK-DETAIL C:\\Users\\secret\\diag.txt",
        raw_output="LEAK-RAW N:\\Auditor\\tools\\slither\\venv",
        raw_output_truncated=True,
    )


_WINDOWS_PATH = re.compile(r"[A-Za-z]:\\\\")  # JSON-escaped drive path, e.g. "C:\\Users"
_POSIX_ABS = re.compile(r"/(?:Users|home|tmp|etc|var|opt|root|mnt|media)/")
_CANONICAL_FINDING_KEYS = {
    "id",
    "kind",
    "status",
    "tool",
    "tool_version",
    "check",
    "severity",
    "impact",
    "confidence",
    "title",
    "description",
    "reference",
    "file",
    "start_line",
    "end_line",
    "locations",
    "evidence",
}


# --------------------------------------------------------------------------
# 1. Read-only surface invariants
# --------------------------------------------------------------------------


def test_surface_is_read_only() -> None:
    check(
        "read-only server exposes exactly the two readers",
        mcp_auditor.TOOL_NAMES == ("analyzer_status", "audit_findings"),
        str(mcp_auditor.TOOL_NAMES),
    )
    forbidden = ("run", "write", "edit", "deploy", "wallet", "sign", "tx", "transaction")
    for name in mcp_auditor.TOOL_NAMES:
        check(
            f"tool '{name}' name has no run/write/exec verb",
            not any(v in name.lower() for v in forbidden),
            name,
        )
    for t in mcp_auditor.TOOL_DEFINITIONS:
        params = t["inputSchema"]
        check(
            f"tool '{t['name']}' accepts no arguments",
            params.get("properties") == {} and params.get("additionalProperties") is False,
            json.dumps(params),
        )
    check(
        "read-only allowlist matches the tool list",
        mcp_auditor.READ_ONLY_TOOL_NAMES == mcp_auditor.TOOL_NAMES,
    )
    # No run capability exists at all.
    for bad in ("run_slither", "write_file", "edit_code", "deploy"):
        try:
            mcp_auditor._call_tool(bad, {})
            rejected = False
        except mcp_auditor.ToolError:
            rejected = True
        check(f"tool '{bad}' is refused (no such capability)", rejected)
    # Arguments are refused even for a valid tool name.
    try:
        mcp_auditor._call_tool("analyzer_status", {"source": "contract Evil {}"})
        refused = False
    except mcp_auditor.ToolError:
        refused = True
    check("valid tool refuses smuggled arguments (no code/path injection)", refused)
    check(
        "resources are the two read-only URIs",
        [r["uri"] for r in mcp_auditor.RESOURCE_DEFINITIONS]
        == ["auditor://status/analyzers", "auditor://findings/current"],
    )


# --------------------------------------------------------------------------
# 2. Empty state + current-analysis-only
# --------------------------------------------------------------------------


def test_empty_state() -> None:
    tmp = _temp_state()
    check("state file starts absent", not tmp.exists())
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        payload = mcp_auditor._call_tool("audit_findings", {})
    check("empty state -> available false", payload.get("available") is False)
    check(
        "empty state -> clear 'no results available' message",
        "no results" in str(payload.get("message", "")).lower(),
        str(payload.get("message")),
    )
    check("empty state -> empty findings list", payload.get("findings") == [])


def test_current_findings_only() -> None:
    tmp = _temp_state()
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        analysis_store.record_analysis(_response([_finding("reentrancy-eth")]))
        first = mcp_auditor._call_tool("audit_findings", {})
        check("recorded analysis -> available true", first.get("available") is True)
        check("recorded analysis -> tool provenance", first.get("tool") == "slither")
        check("recorded analysis -> version surfaced", first.get("tool_version") == "9.9.9")
        checks1 = {f["check"] for f in first["findings"]}
        check("recorded analysis -> finding present", checks1 == {"reentrancy-eth"}, str(checks1))
        check("recorded analysis -> recorded_at present", bool(first.get("recorded_at")))

        # A newer analysis replaces the old one (current = latest, no history).
        analysis_store.record_analysis(_response([_finding("suicidal", start=29, end=31)]))
        second = mcp_auditor._call_tool("audit_findings", {})
        checks2 = {f["check"] for f in second["findings"]}
        check(
            "new analysis replaces the previous one (current only)",
            checks2 == {"suicidal"},
            str(checks2),
        )
        check("old finding is gone from the surface", "reentrancy-eth" not in checks2)


def test_no_leaks_and_no_model_text() -> None:
    tmp = _temp_state()
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        # Caller supplies an absolute path; the store must reduce it to a basename.
        analysis_store.record_analysis(
            _response([_finding("reentrancy-eth", file="C:\\Users\\secret\\Submitted.sol")])
        )
        payload = mcp_auditor._call_tool("audit_findings", {})
    blob = json.dumps(payload)
    check("no raw_output in the surface", "raw_output" not in blob)
    check("no detail field in the surface", '"detail"' not in blob)
    check(
        "no absolute filesystem path in the surface",
        not _WINDOWS_PATH.search(blob) and not _POSIX_ABS.search(blob),
        blob[:120],
    )
    check("no poisoned leak strings in the surface", "LEAK-" not in blob)
    for f in payload["findings"]:
        check(
            f"finding '{f.get('check')}' only has canonical keys",
            set(f.keys()) == _CANONICAL_FINDING_KEYS,
            str(sorted(set(f.keys()) - _CANONICAL_FINDING_KEYS)),
        )
        check(
            "finding file is a neutral basename (no directory)",
            "/" not in f["file"] and "\\" not in f["file"],
            f["file"],
        )
    check(
        "provenance labels findings as deterministic analyzer output",
        "analysis" in payload.get("provenance", "") or "deterministic" in payload.get("provenance", "").lower(),
        str(payload.get("provenance")),
    )
    check(
        "provenance states model explanations are not included",
        "not included" in payload.get("provenance", "").lower(),
        str(payload.get("provenance")),
    )


def test_bridge_payload_records_canonically() -> None:
    tmp = _temp_state()
    brief = {
        "tool": "slither",
        "tool_version": "0.11.6",
        "status": "ok",
        "summary": "Slither reported 1 finding(s) for this run.",
        "finding_count": 1,
        "findings": [
            {
                "check": "reentrancy-eth",
                "severity": "high",
                "impact": "High",
                "confidence": "Medium",
                "file": "Submitted.sol",
                "start_line": 20,
                "end_line": 26,
                "title": "Reentrancy in withdraw()",
            }
        ],
        "findings_truncated": False,
        "notice": "Deterministic analyzer output; not a complete audit.",
    }
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        analysis_store.record_tool_payload(brief)
        payload = mcp_auditor._call_tool("audit_findings", {})
    f = payload["findings"][0]
    check("bridge payload recorded with canonical keys", set(f.keys()) == _CANONICAL_FINDING_KEYS)
    check("bridge payload keeps detector + location", (f["check"], f["start_line"], f["end_line"]) == ("reentrancy-eth", 20, 26))
    check("bridge payload is not falsely given a description", f["description"] == "")


# --------------------------------------------------------------------------
# 3. API still records the current analysis (both paths)
# --------------------------------------------------------------------------


def test_api_records_current_analysis() -> None:
    tmp = _temp_state()
    client = TestClient(app, raise_server_exceptions=False)

    async def fake_direct(_source: str) -> SlitherResponse:
        return _response([_finding("reentrancy-eth")])

    with mock.patch.object(settings, "mcp_state_file", str(tmp)), \
         mock.patch.object(slither_adapter, "run_slither", fake_direct):
        r = client.post("/api/slither", json={"source": SRC})
        check("direct /api/slither still 200", r.status_code == 200, f"got {r.status_code}")
        loaded = analysis_store.load_current_analysis()
        check("direct path records current analysis", bool(loaded))
        check(
            "direct path record has the finding",
            loaded and loaded["findings"][0]["check"] == "reentrancy-eth",
        )

    async def fake_run_once(_source: str) -> dict:
        return {
            "tool": "slither",
            "tool_version": "0.11.6",
            "status": "ok",
            "summary": "Slither reported 1 finding(s) for this run.",
            "finding_count": 1,
            "findings": [
                {
                    "check": "suicidal",
                    "severity": "high",
                    "impact": "High",
                    "confidence": "High",
                    "file": "Submitted.sol",
                    "start_line": 29,
                    "end_line": 31,
                    "title": "Unprotected selfdestruct",
                }
            ],
            "findings_truncated": False,
            "notice": "Deterministic analyzer output; not a complete audit.",
        }

    with mock.patch.object(settings, "mcp_state_file", str(tmp)), \
         mock.patch.object(mcp_client, "run_slither_once", fake_run_once), \
         mock.patch.object(ollama_client, "chat", return_value=("grounded", "mock:1", 3)):
        r2 = client.post(
            "/api/tools/run",
            json={"source": SRC, "question": "run it", "tool": "run_slither"},
        )
        check("approved /api/tools/run still 200", r2.status_code == 200, f"got {r2.status_code}")
        loaded2 = analysis_store.load_current_analysis()
        check(
            "approved MCP path updates the current analysis",
            loaded2 and loaded2["findings"][0]["check"] == "suicidal",
        )


# --------------------------------------------------------------------------
# 4. Real stdio round-trip against the entry point
# --------------------------------------------------------------------------


def _run_stdio(state_file: Path, requests: list[dict]) -> list[dict]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env[settings.model_config.get("env_prefix", "AUDITOR_") + "MCP_STATE_FILE"] = str(state_file)
    payload = "".join(json.dumps(m) + "\n" for m in requests)
    proc = subprocess.run(
        [sys.executable, "-m", "backend.mcp_auditor"],
        input=payload,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=90,
    )
    out: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def test_stdio_roundtrip() -> None:
    tmp = _temp_state()
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "audit_findings", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "run_slither", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "resources/read",
            "params": {"uri": "auditor://findings/current"},
        },
    ]
    responses = {r.get("id"): r for r in _run_stdio(tmp, reqs)}

    init = responses.get(1, {}).get("result", {})
    check(
        "stdio initialize advertises the read-only server name",
        init.get("serverInfo", {}).get("name") == mcp_auditor.SERVER_NAME,
        str(init.get("serverInfo")),
    )
    check(
        "stdio initialize advertises tools + resources",
        set((init.get("capabilities") or {}).keys()) >= {"tools", "resources"},
    )

    tools = responses.get(2, {}).get("result", {}).get("tools", [])
    check(
        "stdio tools/list = the two readers only",
        [t["name"] for t in tools] == ["analyzer_status", "audit_findings"],
        str([t["name"] for t in tools]),
    )

    uris = [r["uri"] for r in responses.get(3, {}).get("result", {}).get("resources", [])]
    check(
        "stdio resources/list = the two read-only URIs",
        uris == ["auditor://status/analyzers", "auditor://findings/current"],
        str(uris),
    )

    find_res = responses.get(4, {}).get("result", {})
    sc = find_res.get("structuredContent") or {}
    check("stdio audit_findings empty state -> available false", sc.get("available") is False)

    run_res = responses.get(5, {}).get("result", {})
    check(
        "stdio tools/call run_slither is refused (no run capability over the wire)",
        run_res.get("isError") is True,
        str(run_res)[:120],
    )

    read_res = responses.get(6, {}).get("result", {})
    contents = read_res.get("contents") or []
    check("stdio resources/read returns JSON contents", bool(contents) and contents[0].get("mimeType") == "application/json")
    if contents:
        body = json.loads(contents[0]["text"])
        check("stdio resources/read empty state -> available false", body.get("available") is False)


def test_stdio_serves_recorded_findings() -> None:
    tmp = _temp_state()
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        analysis_store.record_analysis(_response([_finding("low-level-calls", severity="informational")]))
    responses = {
        r.get("id"): r
        for r in _run_stdio(
            tmp,
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "audit_findings", "arguments": {}},
                }
            ],
        )
    }
    sc = responses.get(1, {}).get("result", {}).get("structuredContent") or {}
    checks = {f["check"] for f in sc.get("findings", [])}
    check(
        "stdio audit_findings returns the current recorded findings",
        sc.get("available") is True and checks == {"low-level-calls"},
        str(checks),
    )


# --------------------------------------------------------------------------
# 5. Optional live probe + app-intact smoke
# --------------------------------------------------------------------------


def test_live_analyzer_status() -> None:
    tmp = _temp_state()
    with mock.patch.object(settings, "mcp_state_file", str(tmp)):
        payload = mcp_auditor._call_tool("analyzer_status", {})
    names = {t["name"]: t for t in payload.get("tools", [])}
    check("analyzer_status reports slither", "slither" in names)
    check("analyzer_status reports solc", "solc" in names)
    # No install paths / detail strings leaked.
    check("analyzer_status exposes no install paths", "tools\\" not in json.dumps(payload).lower())

    from pathlib import Path as _P

    if (_P(settings.slither_exe).exists() and _P(settings.solc_exe).exists()):
        check("live slither is available with a version", names["slither"]["available"] and bool(names["slither"]["version"]), str(names["slither"]))
    else:
        print("[SKIP] live analyzer availability -- canonical install not found")


def test_app_still_intact() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/health")
    check("/api/health still 200", r.status_code == 200, f"got {r.status_code}")
    body = r.json()
    check("health still reports tool status", "slither" in (body.get("tools") or {}))
    check("health still exposes the internal MCP bridge", (body.get("mcp") or {}).get("server") == "auditor-mcp")


def main() -> int:
    try:
        test_surface_is_read_only()
        test_empty_state()
        test_current_findings_only()
        test_no_leaks_and_no_model_text()
        test_bridge_payload_records_canonically()
        test_api_records_current_analysis()
        test_stdio_roundtrip()
        test_stdio_serves_recorded_findings()
        test_live_analyzer_status()
        test_app_still_intact()
    finally:
        for d in _TEMP_DIRS:
            shutil.rmtree(d, ignore_errors=True)

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
