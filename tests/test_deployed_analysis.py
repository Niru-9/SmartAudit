"""Focused offline tests for Phase 6.3 — analyze the *retrieved* verified source.

Covers, with no real network:
  * source-to-analysis association: the analysis is tied to the exact sources of
    the current verified lookup (address/chain echoed, file content forwarded
    verbatim to the analyzer);
  * verified-source gating: unverified/unavailable/empty source is never run;
  * approval / no-auto-run: the analyzer is reached only from the explicit
    action (proven by the frontend wiring + the backend never running a lookup);
  * multi-file handling: multi-file verified source is refused as *unsupported*
    and NOT analyzed (never a partial run), non-Solidity and truncated
    (incomplete) source are refused too;
  * stale-result clearing: the frontend clears/invalidates retrieved-source
    analysis on address change, network change, and a new lookup;
  * the invariants that the analysis endpoint performs NO outbound call and
    never invokes the model.

The analyzer is replaced by an in-test recorder (no subprocess); one live smoke
check runs the *real* local adapter on a trivial single-file contract when the
canonical Slither install is present. The app route is exercised through the
in-process ``TestClient``.

Run:  py tests/test_deployed_analysis.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import deployed, deployed_analysis, slither_adapter, solhint_adapter  # noqa: E402
from backend.main import app  # noqa: E402
from backend.schemas import SlitherResponse, SolhintResponse  # noqa: E402

ADDR = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
TRIVIAL = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.20;\ncontract A { function f() external {} }\n"

SINGLE = [{"path": "contracts/A.sol", "chars": len(TRIVIAL), "content": TRIVIAL}]
MULTI = [
    {"path": "contracts/A.sol", "chars": 12, "content": "contract A {}"},
    {"path": "contracts/B.sol", "chars": 12, "content": "contract B {}"},
]

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def run(coro):
    return asyncio.run(coro)


def fake_response(status: str = "no_findings", findings=None) -> SlitherResponse:
    findings = findings or []
    return SlitherResponse(
        tool="slither",
        tool_version="0.11.6",
        status=status,
        summary=f"summary:{status}",
        finding_count=len(findings),
        findings=findings,
        source_name="Submitted.sol",
        duration_ms=11,
        notice="Deterministic analyzer output; a clean run is not a safety guarantee.",
    )


class Recorder:
    """Async analyzer stand-in that records the exact source it was handed."""

    def __init__(self, status: str = "no_findings") -> None:
        self.calls: list[str] = []
        self._status = status

    async def __call__(self, source: str) -> SlitherResponse:
        self.calls.append(source)
        return fake_response(self._status)


def fake_solhint_response(status: str = "no_findings") -> SolhintResponse:
    return SolhintResponse(
        tool="solhint",
        tool_version="6.2.5",
        status=status,
        summary=f"solhint summary:{status}",
        finding_count=0,
        findings=[],
        source_name="Submitted.sol",
        duration_ms=7,
        notice="Deterministic linter output; a clean run is not a safety guarantee.",
    )


class SolhintRecorder:
    """Async Solhint stand-in (kept separate so tests never spawn the linter)."""

    def __init__(self, status: str = "no_findings") -> None:
        self.calls: list[str] = []
        self._status = status

    async def __call__(self, source: str) -> SolhintResponse:
        self.calls.append(source)
        return fake_solhint_response(self._status)


def main() -> int:
    client = TestClient(app, raise_server_exceptions=False)

    # --- 1. Association + gating (direct module) --------------------------
    rec = Recorder()
    solrec = SolhintRecorder()
    good = run(
        deployed_analysis.analyze_retrieved_source(
            ADDR, deployed.ETHEREUM_CHAIN_ID, SINGLE, language="Solidity", runner=rec, solhint_runner=solrec
        )
    )
    check("single complete Solidity file is allowed", good["allowed"] is True)
    check("analyzer received the EXACT retrieved source content", rec.calls == [TRIVIAL])
    check("Solhint received the SAME retrieved source content", solrec.calls == [TRIVIAL])
    check(
        "analysis echoes the address (normalized + EIP-55) and chain",
        good["address"] == ADDR.lower()
        and good["checksum"] == ADDR
        and good["chain_id"] == deployed.ETHEREUM_CHAIN_ID
        and good["chain_name"] == "Ethereum Mainnet",
    )
    check("analysis reports the analyzed file path + single-file count", good["source_path"] == "contracts/A.sol" and good["source_file_count"] == 1)
    check("analyzer response is returned unchanged (same SlitherResponse)", isinstance(good["analyzer"], SlitherResponse) and good["analyzer"].status == "no_findings")
    check(
        "Solhint result travels separately and is attributed to solhint",
        isinstance(good["solhint"], SolhintResponse) and good["solhint"].tool == "solhint",
    )
    check(
        "per-tool analyzers carry both tools, never merged",
        [a.tool for a in good["analyzers"]] == ["slither", "solhint"],
        str([a.tool for a in good["analyzers"]]),
    )

    # Association: a different retrieved source is forwarded verbatim.
    rec2 = Recorder()
    other = [{"path": "contracts/C.sol", "chars": 20, "content": "pragma solidity ^0.8.4; contract C {}"}]
    run(deployed_analysis.analyze_retrieved_source(ADDR, 11155111, other, runner=rec2, solhint_runner=SolhintRecorder()))
    check("a different lookup's source is the one analyzed (association)", rec2.calls == [other[0]["content"]])

    # Refusals: the analyzer must never run for these.
    def refused(**kwargs) -> tuple[dict, Recorder]:
        r = Recorder()
        out = run(deployed_analysis.analyze_retrieved_source(runner=r, **kwargs))
        return out, r

    out, r = refused(address="not-an-address", chain_id=1, sources=SINGLE)
    check("invalid address is refused with a reason and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_INVALID_ADDRESS and r.calls == [])

    out, r = refused(address=ADDR, chain_id=8453, sources=SINGLE)
    check("non-Ethereum chain is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_UNSUPPORTED_CHAIN and r.calls == [])

    out, r = refused(address=ADDR, chain_id=None, sources=SINGLE)
    check("missing chain is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_UNSUPPORTED_CHAIN and r.calls == [])

    out, r = refused(address=ADDR, chain_id=1, sources=[])
    check("no verified source is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_NO_VERIFIED_SOURCE and r.calls == [])

    out, r = refused(address=ADDR, chain_id=1, sources=SINGLE, sources_truncated=True)
    check("truncated/incomplete source is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_INCOMPLETE_SOURCE and r.calls == [])

    out, r = refused(address=ADDR, chain_id=1, sources=SINGLE, language="Vyper")
    check("non-Solidity source is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_UNSUPPORTED_LANGUAGE and r.calls == [])

    out, r = refused(address=ADDR, chain_id=1, sources=[{"path": "A.sol", "content": "   \n"}])
    check("empty single file is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_EMPTY_SOURCE and r.calls == [])

    out, r = refused(address=ADDR, chain_id=1, sources=MULTI)
    check("multi-file verified source is refused and NOT analyzed", out["allowed"] is False and out["reason_code"] == deployed_analysis.REASON_MULTI_FILE_UNSUPPORTED and r.calls == [])
    check(
        "multi-file refusal states unsupported / not run / no partial run",
        "unsupported" in (out["reason"] or "").lower()
        and "not run" in (out["reason"] or "").lower()
        and "no file was analyzed" in (out["reason"] or "").lower(),
        out["reason"] or "",
    )
    check("multi-file is never presented as a single-file partial run", "single Solidity source file" in (out["reason"] or ""))

    # A refused run exposes no analyzer at all.
    check("refused analysis exposes no analyzer payload", out["analyzer"] is None)
    check("refused analysis exposes no solhint payload", out["solhint"] is None)

    # Honest status passthrough (compiler_error etc. still 'allowed' but honest).
    rec3 = Recorder(status="compiler_error")
    solrec3 = SolhintRecorder(status="compiler_error")
    out = run(deployed_analysis.analyze_retrieved_source(ADDR, 1, SINGLE, runner=rec3, solhint_runner=solrec3))
    check("analyzer status is passed through honestly (no false success)", out["allowed"] is True and out["analyzer"].status == "compiler_error")
    check("solhint status is passed through honestly too", out["solhint"] is not None and out["solhint"].status == "compiler_error")

    # --- 2. Route through the app (offline, analyzer mocked) --------------
    async def _route_refused(**kwargs):
        return Recorder()

    def post(payload):
        return client.post("/api/deployed/analyze-source", json=payload)

    base = {"address": ADDR, "chain_id": 1, "sources": SINGLE, "sources_truncated": False, "language": "Solidity"}

    rec4 = Recorder()
    sol4 = SolhintRecorder()
    with mock.patch.object(slither_adapter, "run_slither", new=rec4), mock.patch.object(solhint_adapter, "run_solhint", new=sol4):
        r = post(base)
    body = r.json()
    check("route: single file -> 200 and allowed", r.status_code == 200 and body["allowed"] is True)
    check("route: analyzer ran exactly once on the posted source", rec4.calls == [TRIVIAL])
    check("route: solhint ran exactly once on the posted source", sol4.calls == [TRIVIAL])
    check("route: response carries the normalized analyzer payload", body["analyzer"]["tool"] == "slither" and body["analyzer"]["status"] == "no_findings")
    check("route: response carries a separate solhint payload", body["solhint"]["tool"] == "solhint" and body["solhint"]["status"] == "no_findings")

    rec5 = Recorder()
    sol5 = SolhintRecorder()
    with mock.patch.object(slither_adapter, "run_slither", new=rec5), mock.patch.object(solhint_adapter, "run_solhint", new=sol5):
        r = post({**base, "sources": MULTI})
    body = r.json()
    check("route: multi-file -> refused with no analyzer call", r.status_code == 200 and body["allowed"] is False and body["reason_code"] == deployed_analysis.REASON_MULTI_FILE_UNSUPPORTED and rec5.calls == [])
    check("route: multi-file refusal runs neither tool", body["solhint"] is None and sol5.calls == [])

    rec6 = Recorder()
    with mock.patch.object(slither_adapter, "run_slither", new=rec6):
        r = post({**base, "chain_id": 8453})
    check("route: unsupported chain -> refused with no analyzer call", r.status_code == 200 and r.json()["allowed"] is False and rec6.calls == [])

    rec7 = Recorder()
    with mock.patch.object(slither_adapter, "run_slither", new=rec7):
        r = post({**base, "address": "0xzz"})
    check("route: invalid address -> refused with no analyzer call", r.status_code == 200 and r.json()["allowed"] is False and rec7.calls == [])

    # --- 3. The analysis endpoint performs NO outbound network I/O --------
    async def _boom(self, request, *a, **k):  # pragma: no cover - must never run
        raise AssertionError("analyze-source must not perform outbound HTTP; source must be posted from the client")

    rec8 = Recorder()
    sol8 = SolhintRecorder()
    with mock.patch.object(httpx.AsyncClient, "send", _boom), mock.patch.object(slither_adapter, "run_slither", new=rec8), mock.patch.object(solhint_adapter, "run_solhint", new=sol8):
        r = post(base)
    check("analysis performs no lookup/HTTP of its own (client posts the source)", r.status_code == 200 and r.json()["allowed"] is True and rec8.calls == [TRIVIAL])

    # --- 3b. Report-export regression (shared finding flow, still offline) --
    finding = {
        "id": "slither:reentrancy-eth:1",
        "tool": "slither",
        "tool_version": "0.11.6",
        "check": "reentrancy-eth",
        "severity": "high",
        "impact": "High",
        "confidence": "Medium",
        "title": "Reentrancy in A.f()",
        "description": "external call before state update",
        "file": "Submitted.sol",
        "start_line": 3,
    }
    export_payload = {
        "source": TRIVIAL,
        "findings": [finding],
        "explanations": {"slither:reentrancy-eth:1": "Qwen explanation text."},
    }
    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/export-report", json=export_payload)
    body = r.json()
    check("export-report still returns 200 (offline)", r.status_code == 200)
    check(
        "export keeps tool findings separate from AI explanations",
        "## STATIC ANALYSIS FINDINGS (DETERMINISTIC)" in body["report"]
        and "## AI-GENERATED EXPLANATIONS (SEPARATE SECTION)" in body["report"]
        and "Qwen explanation text." in body["report"],
    )
    check("export filename is the expected markdown name", re.match(r"^auditor-report-\d{8}-\d{6}\.md$", body["filename"]) is not None, body["filename"])
    check("export report flags it as an automated aid needing human review", "human review" in body["report"].lower())

    # --- 4. Backend never invokes the model or a network client -----------
    src = (ROOT / "backend" / "deployed_analysis.py").read_text(encoding="utf-8")
    check(
        "analysis module does not import the model / provider / http client",
        "ollama_client" not in src and "deployed_source" not in src and "httpx" not in src,
    )
    check("capability note states the single-file limit", "single" in deployed_analysis.CAPABILITY_NOTE.lower())
    check("backend multi-file reason is explicit and honest", "unsupported" in deployed_analysis.multi_file_reason(3).lower() and "no file was analyzed" in deployed_analysis.multi_file_reason(3).lower())

    # --- 5. Frontend: explicit action only, stale results cleared ---------
    index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    check("frontend has the explicit analyze button", 'id="deployedAnalyzeBtn"' in index_html)
    check("frontend analyze button starts disabled", re.search(r'id="deployedAnalyzeBtn" type="button" disabled', index_html) is not None)
    check("frontend analyze button is wired only to runDeployedAnalysis", "analyzeBtn.addEventListener('click', runDeployedAnalysis)" in app_js)
    check("runDeployedAnalysis appears exactly twice (definition + button wiring), never auto-called", app_js.count("runDeployedAnalysis") == 2)
    check("analyze action POSTs to the dedicated endpoint", "/api/deployed/analyze-source" in app_js)
    check("analysis is tied to the current lookup state", "deployedState.lookup" in app_js and "deployedAnalysisEligibility" in app_js)
    check("analysis is gated on verified + single + non-truncated + Solidity", "sources_truncated" in app_js and "files.length > 1" in app_js and "!== 'solidity'" in app_js)

    check("frontend states the multi-file limitation plainly", "multi-file analysis is unsupported in this phase" in app_js and "no partial run is presented" in app_js.lower())
    check("frontend never silently analyzes one file of a multi-file contract", "for (const f of files)" not in app_js and "analyze one file" not in app_js.lower())

    # Stale-result clearing: wiped on new lookup, address change, network change,
    # and when a fresh result replaces the previous one.
    check("clearDeployedAnalysis is called from all four invalidation points", app_js.count("clearDeployedAnalysis();") == 4)
    check("address/network change marks the lookup stale (disables analysis)", app_js.count("deployedState.lookupStale = true") == 2)
    check("a new lookup clears the previous analysis", "deployedState.lookup = null;" in app_js)
    check("a fresh result clears the previous analysis", "deployedState.lookup = data;" in app_js and "clearDeployedAnalysis();" in app_js)

    # Explain flow reuses the existing endpoint and only explains findings.
    check("frontend exposes a Qwen explain action for retrieved findings", 'id="explainDeployedFindingBtn"' in index_html and "explainDeployedBtn.addEventListener('click', explainDeployedFinding)" in app_js)
    check("explain reuses the existing /api/explain-finding endpoint", "/api/explain-finding" in app_js)
    check("explain sends the retrieved single-file source", "lk.sources[0].content" in app_js)
    check("deployed panel advertises phase 6.3", 'id="deployedPhaseBadge" class="badge">phase 6.3' in index_html)
    check("frontend has no hard-coded http(s) URL", "http://" not in app_js and "https://" not in app_js)

    # --- 6. Live smoke: the real local adapter on a trivial single file ---
    info = slither_adapter.get_tool_info("slither")
    if info.available:
        live = run(deployed_analysis.analyze_retrieved_source(ADDR, 11155111, SINGLE, language="Solidity"))
        check("live: real adapter analyzes a single-file retrieved source", live["allowed"] is True and isinstance(live["analyzer"], SlitherResponse))
        check(
            "live: real adapter reports an honest known status",
            live["analyzer"].status in {"ok", "no_findings", "compiler_error", "timeout", "unavailable", "error"},
            live["analyzer"].status,
        )
        check(
            "live: real Solhint result is separate and attributed to solhint",
            isinstance(live["solhint"], SolhintResponse) and live["solhint"].tool == "solhint",
            str(getattr(live["solhint"], "status", None)),
        )
    else:
        check("live adapter smoke skipped (Slither unavailable locally)", True)

    print()
    if all(results):
        print("ALL DEPLOYED-ANALYSIS TESTS PASSED")
        return 0
    print(f"{results.count(False)} TEST(S) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
