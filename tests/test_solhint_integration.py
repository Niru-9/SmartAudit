"""Tests for the local Solhint linter integration (added 2026-10-17).

Mix of:
- offline unit tests for the JSON normalizer, parse-error detection, and the
  output sanitizer (no subprocess);
- offline mocked failure-path tests (unavailable / timeout / process error);
- live integration tests against the real local Solhint + Node (skipped
  automatically if the canonical install is missing);
- API tests through FastAPI's TestClient (/api/health, /api/solhint,
  /api/analyze, /api/export-report);
- static frontend checks (Solhint section wired, no hard-coded URLs).

Run:  py tests/test_solhint_integration.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from backend import solhint_adapter as sh  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402

RESULTS: list[bool] = []
ROOT = Path(__file__).resolve().parent.parent


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VULN = (
    "// SPDX-License-Identifier: MIT\n"
    "pragma solidity 0.8.37;\n"
    "contract V {\n"
    "    mapping(address => uint) b;\n"
    "    function w() external {\n"
    "        (bool ok,) = msg.sender.call{value: 1}(\"\");\n"
    "        require(ok);\n"
    "        b[msg.sender] -= 1;\n"
    "    }\n"
    "}\n"
)

CLEAN = (
    "// SPDX-License-Identifier: MIT\n"
    "pragma solidity 0.8.37;\n"
    "contract Clean { uint256 public x; function set(uint256 v) external { x = v; } }\n"
)

BROKEN = "pragma solidity ^0.8.20;\ncontract Broken { function f( external { } }\n"

# A minimal Solhint JSON report: a problem array plus the trailing conclusion
# element Solhint appends (shape verified against Solhint 6.2.5).
SYNTHETIC = [
    {
        "line": 3,
        "column": 1,
        "severity": "Warning",
        "message": "Compiler version ^0.8.20 does not satisfy the ^0.8.0 semver requirement",
        "ruleId": "compiler-version",
        "filePath": "C:\\Users\\NIRUPAM\\AppData\\Local\\Temp\\auditor-work\\solhint-abc\\Submitted.sol",
    },
    {
        "line": 4,
        "column": 5,
        "severity": "Error",
        "message": "Code contains empty blocks",
        "ruleId": "no-empty-blocks",
        "filePath": "C:\\Users\\NIRUPAM\\AppData\\Local\\Temp\\auditor-work\\solhint-abc\\Submitted.sol",
    },
    {"conclusion": "2 problems (1 error, 1 warning)"},
]


# ---------------------------------------------------------------------------
# Offline unit tests
# ---------------------------------------------------------------------------


def test_parser() -> None:
    findings = sh.parse_solhint_json(SYNTHETIC, "6.2.5")
    check("parser: two findings (conclusion ignored)", len(findings) == 2, f"got {len(findings)}")
    f0, f1 = findings[0], findings[1]
    check("parser: kind=tool", all(f.kind == "tool" for f in findings))
    check("parser: status=confirmed", all(f.status == "confirmed" for f in findings))
    check("parser: tool=solhint", all(f.tool == "solhint" for f in findings))
    check("parser: tool_version", all(f.tool_version == "6.2.5" for f in findings))
    check("parser: rule id kept", f0.check == "compiler-version", f0.check)
    check("parser: warning -> low", f0.severity == "low", f0.severity)
    check("parser: error -> medium", f1.severity == "medium", f1.severity)
    check("parser: original impact preserved", f0.impact == "Warning" and f1.impact == "Error")
    check("parser: confidence n/a", f0.confidence == "n/a", f0.confidence)
    check("parser: line/column", (f0.start_line, f0.end_line) == (3, 3), str(f0.start_line))
    check("parser: file is basename only", f0.file == "Submitted.sol" and ":" not in f0.file, f0.file)
    check("parser: id stable", f0.id == "compiler-version:3:1", f0.id)
    check("parser: locations collected", len(f0.locations) == 1)

    # Entries without a ruleId are never findings.
    only_conclusion = sh.parse_solhint_json([{"conclusion": "0 problems"}], "6.2.5")
    check("parser: conclusion-only -> []", only_conclusion == [])

    # Empty report array -> []
    check("parser: empty -> []", sh.parse_solhint_json([], "6.2.5") == [])

    # Non-array raises
    try:
        sh.parse_solhint_json({"ruleId": "x"}, "6.2.5")
        check("parser: non-array raises", False)
    except ValueError:
        check("parser: non-array raises", True)


def test_parse_error_detection() -> None:
    # A report consisting only of the conclusion-like parse error must be
    # detected as a parse failure (never reported as a clean run).
    report = [
        {"line": 1, "column": 1, "severity": "Error", "message": "Parse error: ..."},
        {"conclusion": "1 problem (1 error, 0 warnings)"},
    ]
    msg = sh._report_parse_error_message(report)
    check("parse-error: detected", bool(msg and "parse error" in msg.lower()), str(msg))
    check("parse-error: clean report not flagged", sh._report_parse_error_message(SYNTHETIC) is None)


def test_sanitizer() -> None:
    work = r"C:\Users\NIRUPAM\AppData\Local\Temp\auditor-work\solhint-x"
    text = (
        "'N:\\Auditor\\tools\\solhint\\node_modules\\solhint\\solhint.js' running\n"
        "C:\\Program Files\\nodejs\\node.exe\n"
        f"{work}\\Submitted.sol\n"
        "File \"N:\\Auditor\\tools\\solhint\\node_modules\\solhint\\cli.js\", line 1\n"
    )
    out, truncated = sh.sanitize_output(text, work)
    check("sanitizer: no tools path", "N:\\Auditor\\tools" not in out and "solhint\\node_modules" not in out)
    check("sanitizer: no node install path", "Program Files" not in out)
    check("sanitizer: workdir replaced", "<work>" in out, out)
    check("sanitizer: not truncated for small text", truncated is False)


def test_argv_template() -> None:
    argv = sh.solhint_argv_static()
    check("argv: node first", argv[0] == str(settings.node_exe), argv[0])
    check("argv: solhint.js second", argv[1] == str(settings.solhint_js), argv[1])
    check("argv: json formatter", "-f" in argv and argv[argv.index("-f") + 1] == "json")
    check("argv: explicit --config", "--config" in argv)
    check("argv: --disc (no update check)", "--disc" in argv)
    check("argv: no source text in argv", not any("pragma" in a for a in argv))


def test_tool_info() -> None:
    info = sh.get_tool_info()
    check("tool_info: name", info.name == "solhint", info.name)
    try:
        sh.get_tool_info("nope")
        check("tool_info: unknown name raises", False)
    except ValueError:
        check("tool_info: unknown name raises", True)


def test_unavailable() -> None:
    with mock.patch.object(settings, "node_exe", "C:/definitely/not/here/node.exe"):
        r = asyncio.run(sh.run_solhint(VULN))
    check("unavailable: status", r.status == "unavailable", r.status)
    check("unavailable: no findings", r.finding_count == 0)
    check("unavailable: tool=solhint", r.tool == "solhint")

    with mock.patch.object(settings, "solhint_js", "C:/definitely/not/here/solhint.js"):
        r2 = asyncio.run(sh.run_solhint(VULN))
    check("unavailable (missing solhint.js): status", r2.status == "unavailable", r2.status)


def test_timeout() -> None:
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b"",
            "stderr": b"",
            "truncated": False,
            "timed_out": True,
            "returncode": None,
        }

    with mock.patch.object(sh, "_execute", fake_execute):
        r = asyncio.run(sh.run_solhint(VULN))
    check("timeout: status", r.status == "timeout", r.status)
    check("timeout: no findings", r.finding_count == 0)


def test_process_error() -> None:
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b"totally unexpected failure without parse markers",
            "stderr": b"",
            "truncated": False,
            "timed_out": False,
            "returncode": -1,
        }

    with mock.patch.object(sh, "_execute", fake_execute):
        r = asyncio.run(sh.run_solhint(VULN))
    check("process error: status", r.status == "error", r.status)
    check("process error: no findings", r.finding_count == 0)


def test_no_findings_offline() -> None:
    """A clean report must be reported as a run with no findings, and must never
    be presented as a safety statement."""
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b'[{"conclusion":"0 problems (0 errors, 0 warnings)"}]',
            "stderr": b"",
            "truncated": False,
            "timed_out": False,
            "returncode": 0,
        }

    with mock.patch.object(sh, "_execute", fake_execute):
        r = asyncio.run(sh.run_solhint(CLEAN))
    check("no findings: status", r.status == "no_findings", r.status)
    check("no findings: count zero", r.finding_count == 0)
    check("no findings: summary says not safe", "not mean the contract is safe" in r.summary.lower(), r.summary)


def test_parse_failure_from_report() -> None:
    async def fake_execute(argv, cwd, timeout, limit):  # noqa: ANN001
        return {
            "stdout": b'[{"line":1,"column":1,"severity":"Error","message":"Parse error: expected }"},{"conclusion":"1 problem"}]',
            "stderr": b"",
            "truncated": False,
            "timed_out": False,
            "returncode": 1,
        }

    with mock.patch.object(sh, "_execute", fake_execute):
        r = asyncio.run(sh.run_solhint(BROKEN))
    check("parse failure: compiler_error", r.status == "compiler_error", r.status)
    check("parse failure: no findings", r.finding_count == 0)


# ---------------------------------------------------------------------------
# Live integration (skipped if the canonical install is missing)
# ---------------------------------------------------------------------------

LIVE = (
    Path(settings.node_exe).exists()
    and Path(settings.solhint_js).exists()
    and Path(settings.solhint_config).exists()
)


def test_live() -> None:
    if not LIVE:
        print("[SKIP] live Solhint tests — canonical install not found")
        return

    r = asyncio.run(sh.run_solhint(VULN))
    check("live: status ok", r.status == "ok", r.status)
    check("live: has findings", r.finding_count >= 1, str(r.finding_count))
    check("live: tool=solhint", r.tool == "solhint")
    check("live: tool_version reported", bool(r.tool_version), str(r.tool_version))
    check("live: all kind=tool", all(f.kind == "tool" for f in r.findings))
    check("live: all tool=solhint", all(f.tool == "solhint" for f in r.findings))
    check("live: all confirmed", all(f.status == "confirmed" for f in r.findings))
    check("live: severities mapped", all(f.severity in ("low", "medium") for f in r.findings))
    check("live: files are basenames", all("/" not in f.file and "\\" not in f.file for f in r.findings))
    check(
        "live: raw output sanitized",
        not any(
            p in (r.raw_output or "")
            for p in ("N:\\Auditor", "C:\\Users", "Program Files", "tools/solhint", "tools\\solhint")
        ),
    )
    check("live: notice caveat", "not a complete" in r.notice.lower())

    rc = asyncio.run(sh.run_solhint(CLEAN))
    check("live clean-ish: honest status", rc.status in ("ok", "no_findings"), rc.status)
    if rc.status == "no_findings":
        check("live clean: summary says not safe", "not mean the contract is safe" in rc.summary.lower())

    rb = asyncio.run(sh.run_solhint(BROKEN))
    check("live broken: compiler_error", rb.status == "compiler_error", rb.status)
    check("live broken: no findings", rb.finding_count == 0)

    # Line/column regression: LF and CRLF input must agree exactly.
    v_crlf = asyncio.run(sh.run_solhint(VULN.replace("\n", "\r\n")))
    lf_locs = sorted((f.check, f.start_line) for f in r.findings)
    crlf_locs = sorted((f.check, f.start_line) for f in v_crlf.findings)
    check("live: CRLF source reports same locations as LF", lf_locs == crlf_locs, f"{lf_locs} vs {crlf_locs}")


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


def test_api() -> None:
    client = TestClient(app, raise_server_exceptions=False)

    r = client.get("/api/health")
    body = r.json()
    tools = body.get("tools", {})
    check("api health: 200", r.status_code == 200, str(r.status_code))
    check(
        "api health: solhint status present",
        tools.get("solhint") in ("available", "unavailable"),
        str(tools.get("solhint")),
    )
    details = {d["name"]: d for d in body.get("tool_details", [])}
    check("api health: solhint in tool_details", "solhint" in details)
    if "solhint" in details and details["solhint"].get("status") == "available":
        check("api health: solhint version", bool(details["solhint"].get("version")), str(details["solhint"]))

    # Validation
    check("api solhint: empty -> 422", client.post("/api/solhint", json={"source": ""}).status_code == 422)
    check("api solhint: whitespace -> 400", client.post("/api/solhint", json={"source": "   "}).status_code == 400)
    check("api analyze: empty -> 422", client.post("/api/analyze", json={"source": ""}).status_code == 422)

    if not LIVE:
        print("[SKIP] live API Solhint checks — canonical install not found")
        return

    r = client.post("/api/solhint", json={"source": VULN})
    b = r.json()
    check("api solhint: vuln -> 200", r.status_code == 200, str(r.status_code))
    check("api solhint: status ok", b.get("status") == "ok", str(b.get("status")))
    check("api solhint: tool=solhint", b.get("tool") == "solhint", str(b.get("tool")))
    check("api solhint: findings present", b.get("finding_count", 0) >= 1)
    check("api solhint: finding kind=tool", all(f.get("kind") == "tool" for f in b.get("findings", [])))
    check("api solhint: findings tool=solhint", all(f.get("tool") == "solhint" for f in b.get("findings", [])))

    # Combined run: both tools report separately, each attributed to itself.
    r = client.post("/api/analyze", json={"source": VULN})
    ab = r.json()
    runs = {t.get("tool"): t for t in ab.get("tools", [])}
    check("api analyze: 200", r.status_code == 200, str(r.status_code))
    check("api analyze: slither run present", "slither" in runs)
    check("api analyze: solhint run present", "solhint" in runs)
    if "slither" in runs and "solhint" in runs:
        check("api analyze: runs stay separated", runs["slither"]["tool"] != runs["solhint"]["tool"])
        check(
            "api analyze: solhint findings attributed to solhint",
            all(f.get("tool") == "solhint" for f in runs["solhint"].get("findings", [])),
        )
        check(
            "api analyze: slither findings attributed to slither",
            all(f.get("tool") == "slither" for f in runs["slither"].get("findings", [])),
        )

    # Restricting the run marks the unrequested tool as skipped (never implied).
    r = client.post("/api/analyze", json={"source": VULN, "tools": ["slither"]})
    sb = {t.get("tool"): t for t in r.json().get("tools", [])}
    check("api analyze: solhint skipped when not requested", sb.get("solhint", {}).get("status") == "skipped",
          str(sb.get("solhint", {}).get("status")))
    check("api analyze: skipped tool has no findings", not sb.get("solhint", {}).get("findings"))


def test_report_export() -> None:
    """The report groups by tool, states per-tool status, and keeps the separate
    Qwen review clearly labeled — while the legacy flat shape still works."""
    client = TestClient(app, raise_server_exceptions=False)

    finding = {
        "id": "compiler-version:3:1",
        "kind": "tool",
        "status": "confirmed",
        "tool": "solhint",
        "tool_version": "6.2.5",
        "check": "compiler-version",
        "severity": "low",
        "impact": "Warning",
        "confidence": "n/a",
        "title": "Compiler version",
        "description": "Compiler version does not satisfy the semver requirement",
        "file": "Submitted.sol",
        "start_line": 3,
        "end_line": 3,
    }
    body = {
        "source": VULN,
        "findings": [],
        "explanations": {"compiler-version:3:1": "Qwen explanation text."},
        "tool_runs": [
            {"tool": "slither", "status": "unavailable", "summary": "Slither not available locally."},
            {"tool": "solhint", "tool_version": "6.2.5", "status": "ok", "findings": [finding]},
        ],
        "qwen_review": "A separate review of the source.",
        "qwen_model": "qwen2.5-coder",
        "scope": "pasted source (submitted in the UI)",
    }
    r = client.post("/api/export-report", json=body)
    check("report: 200", r.status_code == 200, str(r.status_code))
    report = r.json().get("report", "")
    check("report: per-tool summary section", "## ANALYZER RUN SUMMARY" in report)
    check("report: deterministic findings header", "## STATIC ANALYSIS FINDINGS (DETERMINISTIC)" in report)
    check("report: slither status shown", "slither: status `unavailable`" in report, report[:400])
    check("report: solhint status shown", "solhint 6.2.5: status `ok`" in report)
    check("report: finding attributed to solhint", "**Tool:** solhint" in report)
    check("report: provenance/scope", "## SOURCE PROVENANCE" in report and "pasted source" in report)
    check("report: separate qwen review section", "## LOCAL QWEN REVIEW" in report and "A separate review" in report)
    check("report: explanation section separate", "## AI-GENERATED EXPLANATIONS (SEPARATE SECTION)" in report and "Qwen explanation text." in report)
    check("report: limitations + human review", "## REPORT LIMITATIONS" in report and "human review" in report.lower())
    check("report: no absolute local paths", "N:\\Auditor" not in report and "C:\\Users" not in report)

    # Legacy flat shape still produces the original section headers.
    legacy = client.post(
        "/api/export-report",
        json={"source": CLEAN, "findings": [], "explanations": {}},
    )
    lrep = legacy.json().get("report", "")
    check("report (legacy): deterministic header", "## STATIC ANALYSIS FINDINGS" in lrep)
    check("report (legacy): explanations header", "## AI-GENERATED EXPLANATIONS" in lrep)


# ---------------------------------------------------------------------------
# Frontend static checks
# ---------------------------------------------------------------------------


def test_frontend_static() -> None:
    index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    # Solhint section wired in the UI.
    for token in (
        'id="solhintBtn"',
        'id="solhintStatus"',
        'id="solhintSummary"',
        'id="solhintFindings"',
        'id="solhintRawWrap"',
        'id="solhintRaw"',
    ):
        check(f"frontend has {token}", token in index_html)

    # Solhint run handler exists and posts to the right endpoint only on click.
    check("frontend posts to /api/solhint", "fetch('/api/solhint'" in app_js)
    check("frontend has renderSolhint", "function renderSolhint(data)" in app_js)
    check("frontend renders per-tool sections", "function renderToolRun(prefix, data)" in app_js)
    check("solhint button wired to a click handler", "solhintBtn.addEventListener('click'" in app_js)
    check("solhint is fetched from exactly one call site (no auto-run)", app_js.count("/api/solhint") == 1)

    # Export carries per-tool runs + the separate Qwen review.
    check("export sends tool_runs", "tool_runs," in app_js or "tool_runs:" in app_js)
    check("export sends qwen_review", "qwen_review:" in app_js)
    check("export marks a not-run tool as skipped", "status: 'skipped'" in app_js)

    # No hard-coded URLs (same-origin only).
    check("app.js has no literal http(s) URL", "http://" not in app_js and "https://" not in app_js)
    check("index.html has no literal http(s) URL", "http://" not in index_html and "https://" not in index_html)


def main() -> int:
    test_parser()
    test_parse_error_detection()
    test_sanitizer()
    test_argv_template()
    test_tool_info()
    test_unavailable()
    test_timeout()
    test_process_error()
    test_no_findings_offline()
    test_parse_failure_from_report()
    test_live()
    test_api()
    test_report_export()
    test_frontend_static()

    print()
    passed = sum(RESULTS)
    print(f"{passed}/{len(RESULTS)} checks passed")
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
