"""Tests for the local Slither analyzer integration (Phase 3).

Mix of:
- offline unit tests for the JSON normalizer and output sanitizer;
- offline mocked failure-path tests (unavailable / timeout / process error);
- live integration tests against the real local Slither + solc (skipped
  automatically if the canonical install is missing);
- API tests through FastAPI's TestClient.

Run:  py tests/test_slither_integration.py
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

from backend import slither_adapter as sa  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402

RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_PATH = Path("N:/Auditor/tools/slither/samples/ReentrancyVault.sol")
try:
    VULN = SAMPLE_PATH.read_text(encoding="utf-8")
except Exception:
    VULN = (
        "// SPDX-License-Identifier: MIT\n"
        "pragma solidity 0.8.37;\n"
        "contract V { mapping(address=>uint) b; function w() external {"
        " (bool ok,)=msg.sender.call{value:1}(\"\"); require(ok); b[msg.sender]-=1; } }\n"
    )

CLEAN = (
    "// SPDX-License-Identifier: MIT\n"
    "pragma solidity 0.8.37;\n"
    "contract Clean { uint256 public x; function set(uint256 v) external { x = v; } }\n"
)
BROKEN = "pragma solidity ^0.8.20;\ncontract Broken { function f( external { } }\n"

# A minimal Slither JSON document (shape verified against Slither 0.11.6).
SYNTHETIC = {
    "success": True,
    "error": None,
    "results": {
        "detectors": [
            {
                "id": "deadbeef",
                "check": "reentrancy-eth",
                "impact": "High",
                "confidence": "Medium",
                "description": (
                    "Reentrancy in V.withdraw(uint256) (Submitted.sol#5-7):\n"
                    "\tExternal calls:\n"
                    "\t- msg.sender.call{value: amount}() (Submitted.sol#6)\n"
                ),
                "reference": (
                    "https://github.com/crytic/slither/wiki/"
                    "Detector-Documentation#reentrancy-vulnerabilities-1"
                ),
                "elements": [
                    {
                        "type": "function",
                        "name": "withdraw(uint256)",
                        "source_mapping": {
                            "filename_relative": "Submitted.sol",
                            "filename_absolute": "C:/tmp/auditor/Submitted.sol",
                            "lines": [5, 6, 7],
                            "starting_column": 5,
                            "ending_column": 6,
                        },
                    },
                    {
                        "type": "node",
                        "name": "(ok,None) = msg.sender.call{value: amount}()",
                        "source_mapping": {
                            "filename_relative": "Submitted.sol",
                            "filename_absolute": "C:/tmp/auditor/Submitted.sol",
                            "lines": [6],
                            "starting_column": 9,
                            "ending_column": 57,
                        },
                    },
                ],
            }
        ]
    },
}


# ---------------------------------------------------------------------------
# Offline unit tests
# ---------------------------------------------------------------------------


def test_parser() -> None:
    findings = sa.parse_slither_json(SYNTHETIC, "0.11.6")
    check("parser: one finding", len(findings) == 1, f"got {len(findings)}")
    f = findings[0]
    check("parser: check id", f.check == "reentrancy-eth", f.check)
    check("parser: kind=tool", f.kind == "tool", f.kind)
    check("parser: status=confirmed", f.status == "confirmed", f.status)
    check("parser: normalized severity", f.severity == "high", f.severity)
    check("parser: original impact preserved", f.impact == "High", f.impact)
    check("parser: original confidence preserved", f.confidence == "Medium", f.confidence)
    check("parser: primary span", (f.start_line, f.end_line) == (5, 7), f"{f.start_line}-{f.end_line}")
    check("parser: file is basename", f.file == "Submitted.sol", f.file)
    check("parser: reference kept", bool(f.reference and f.reference.startswith("http")))
    check("parser: locations collected", len(f.locations) == 2, str(len(f.locations)))
    check("parser: no absolute path in file", ":" not in f.file and "/" not in f.file)
    check("parser: evidence present", bool(f.evidence), str(f.evidence))

    # success=False -> ValueError
    try:
        sa.parse_slither_json({"success": False, "error": "boom", "results": {}}, "0.11.6")
        check("parser: success=false raises", False)
    except ValueError:
        check("parser: success=false raises", True)

    # empty detectors -> no findings
    empty = sa.parse_slither_json({"success": True, "results": {"detectors": []}}, "0.11.6")
    check("parser: empty detectors -> []", empty == [])


def test_sanitizer() -> None:
    text = (
        "'N:\\Auditor\\tools\\solc\\solc.exe --version' running\n"
        "C:\\Users\\NIRUPAM\\AppData\\Local\\Temp\\auditor-work\\slither-x\\Submitted.sol\n"
        "File \"N:\\Auditor\\tools\\slither\\venv\\Lib\\site-packages\\x.py\", line 1\n"
        "SECRET_TOKEN=shouldnotappear\n"
    )
    clean, truncated = sa.sanitize_output(text, r"C:\Users\NIRUPAM\AppData\Local\Temp\auditor-work\slither-x")
    check("sanitize: no N:\\Auditor leak", "N:\\Auditor" not in clean)
    check("sanitize: no C:\\Users leak", "C:\\Users" not in clean)
    check("sanitize: workdir replaced", "<work>" in clean)
    check("sanitize: not truncated for small text", truncated is False)


# ---------------------------------------------------------------------------
# Offline mocked failure paths
# ---------------------------------------------------------------------------


def test_unavailable() -> None:
    with mock.patch.object(settings, "slither_exe", "N:/Auditor/tools/slither/does-not-exist.exe"):
        r = asyncio.run(sa.run_slither(VULN))
    check("unavailable: status", r.status == "unavailable", r.status)
    check("unavailable: no findings", r.finding_count == 0)
    check("unavailable: source preserved label", r.source_name == "Submitted.sol")


def test_timeout() -> None:
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b"",
            "stderr": b"",
            "truncated": False,
            "timed_out": True,
            "returncode": None,
        }

    with mock.patch.object(sa, "_execute", fake_execute):
        r = asyncio.run(sa.run_slither(VULN))
    check("timeout: status", r.status == "timeout", r.status)
    check("timeout: no findings", r.finding_count == 0)


def test_process_error() -> None:
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b"totally unexpected failure without compile markers",
            "stderr": b"",
            "truncated": False,
            "timed_out": False,
            "returncode": -1,
        }

    with mock.patch.object(sa, "_execute", fake_execute):
        r = asyncio.run(sa.run_slither(VULN))
    check("process error: status", r.status == "error", r.status)
    check("process error: no findings", r.finding_count == 0)


# ---------------------------------------------------------------------------
# Live integration (skipped if the canonical install is missing)
# ---------------------------------------------------------------------------

LIVE = Path(settings.slither_exe).exists() and Path(settings.solc_exe).exists()


def test_live() -> None:
    if not LIVE:
        print("[SKIP] live Slither tests — canonical install not found")
        return

    r = asyncio.run(sa.run_slither(VULN))
    checks = {f.check for f in r.findings}
    check("live vuln: status ok", r.status == "ok", r.status)
    check("live vuln: has findings", r.finding_count >= 1, str(r.finding_count))
    check("live vuln: reentrancy-eth detected", "reentrancy-eth" in checks, str(checks))
    check("live vuln: tool_version reported", bool(r.tool_version), str(r.tool_version))
    check("live vuln: all kind=tool", all(f.kind == "tool" for f in r.findings))
    check("live vuln: all confirmed", all(f.status == "confirmed" for f in r.findings))
    check("live vuln: files are basenames", all("/" not in f.file and "\\" not in f.file for f in r.findings))
    check(
        "live vuln: raw output sanitized",
        not any(p in (r.raw_output or "") for p in ("N:\\Auditor", "C:\\Users", "tools/slither", "tools\\slither")),
    )

    rc = asyncio.run(sa.run_slither(CLEAN))
    check("live clean: no_findings", rc.status == "no_findings", f"{rc.status} ({rc.finding_count})")
    check("live clean: summary says not safe", "not mean the contract is safe" in rc.summary.lower() or "not a complete" in rc.summary.lower())

    rb = asyncio.run(sa.run_slither(BROKEN))
    check("live broken: compiler_error", rb.status == "compiler_error", rb.status)
    check("live broken: no findings", rb.finding_count == 0)

    # Line-ending regression: the MCP bridge hands the source to the adapter as a
    # string, and a Windows text-mode write used to turn CRLF into CRCRLF, which
    # doubled every reported line number. LF and CRLF input must agree exactly.
    v_crlf = asyncio.run(sa.run_slither(VULN.replace("\r\n", "\n").replace("\n", "\r\n")))
    lf_locs = sorted((f.check, f.start_line, f.end_line) for f in r.findings)
    crlf_locs = sorted((f.check, f.start_line, f.end_line) for f in v_crlf.findings)
    check("live vuln: CRLF source reports same locations as LF", lf_locs == crlf_locs,
          f"{lf_locs} vs {crlf_locs}")


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_api() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    # The app now records the "current analysis" for the read-only MCP server
    # after each run. Point that at a throwaway file so this suite never
    # clobbers the real current-analysis state.
    state_tmp = Path(tempfile.gettempdir()) / "auditor-test-api-current-analysis.json"

    # health exposes real slither/solc availability
    r = client.get("/api/health")
    body = r.json()
    tools = body.get("tools", {})
    check("api health: 200", r.status_code == 200, str(r.status_code))
    check("api health: slither available", tools.get("slither") == "available", str(tools.get("slither")))
    check("api health: solc available", tools.get("solc") == "available", str(tools.get("solc")))
    check("api health: solhint integrated (available or unavailable, never not_integrated)",
          tools.get("solhint") in ("available", "unavailable"), str(tools.get("solhint")))
    check("api health: only slither/solc/solhint integrated",
          all(v == "not_integrated" for k, v in tools.items() if k not in ("slither", "solc", "solhint")), str(tools))
    details = {d["name"]: d for d in body.get("tool_details", [])}
    check("api health: tool_details present", "slither" in details and "solc" in details)
    if "slither" in details:
        check("api health: slither version", bool(details["slither"].get("version")), str(details["slither"]))

    # empty source -> 422 (min_length); whitespace -> 400
    check("api slither: empty -> 422", client.post("/api/slither", json={"source": ""}).status_code == 422)
    check("api slither: whitespace -> 400", client.post("/api/slither", json={"source": "   "}).status_code == 400)

    if LIVE:
        with mock.patch.object(settings, "mcp_state_file", str(state_tmp)):
            r = client.post("/api/slither", json={"source": VULN})
            b = r.json()
            check("api slither: vuln -> 200", r.status_code == 200, str(r.status_code))
            check("api slither: status ok", b.get("status") == "ok", str(b.get("status")))
            check("api slither: findings present", b.get("finding_count", 0) >= 1)
            check("api slither: finding kind=tool", all(f.get("kind") == "tool" for f in b.get("findings", [])))
            check("api slither: notice caveat", "not a complete" in (b.get("notice", "").lower()))

            r = client.post("/api/slither", json={"source": CLEAN})
            check("api slither: clean -> no_findings", r.json().get("status") == "no_findings", str(r.json().get("status")))

            r = client.post("/api/slither", json={"source": BROKEN})
            check("api slither: broken -> compiler_error", r.json().get("status") == "compiler_error", str(r.json().get("status")))
    else:
        print("[SKIP] live API slither checks — canonical install not found")


def main() -> int:
    test_parser()
    test_sanitizer()
    test_unavailable()
    test_timeout()
    test_process_error()
    test_live()
    test_api()

    print()
    passed = sum(RESULTS)
    print(f"{passed}/{len(RESULTS)} checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
