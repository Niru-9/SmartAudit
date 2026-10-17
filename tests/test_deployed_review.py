"""Focused offline tests for Phase 6.1 deployed-contract review groundwork.

Covers: EIP-55 address validation (including the official EIP-55 vectors),
default-off consent, the unselected-by-default network list, and the invariant
that this phase performs NO outbound network I/O and adds NO contract-fetch
endpoint.

No model, no analyzer, no blockchain. Run:  py tests/test_deployed_review.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest import mock

# Make the repo root importable when run as `py tests/test_deployed_review.py`.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend import deployed  # noqa: E402
from backend.main import app  # noqa: E402

# Official EIP-55 test vectors (from EIP-55).
EIP55_VECTORS = (
    "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
    "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
    "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
)
LOWER = "0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed"
UPPER = "0x5AAEB6053F3E94C9B9A09F33669435E7EF1BEAED"

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def main() -> int:
    client = TestClient(app, raise_server_exceptions=False)

    # --- 1. Keccak-256 is the *original* Keccak (EIP-55 base) ---------------
    check(
        "keccak256('') matches the known Keccak-256 digest",
        deployed.keccak256(b"").hex()
        == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470",
    )

    # --- 2. EIP-55 checksum vectors ----------------------------------------
    for vec in EIP55_VECTORS:
        check(f"EIP-55 checksum round-trips {vec[:10]}...", deployed.eip55_checksum(vec) == vec)

    # --- 3. validate_address: accepting valid hex, any case ----------------
    low = deployed.validate_address(LOWER)
    check("lowercase address accepted", low["valid"] is True, low["reason"])
    check("lowercase -> ok_lowercase", low["reason"] == deployed.REASON_OK_LOWERCASE)
    check("lowercase normalized", low["normalized"] == LOWER)
    check("lowercase exposes EIP-55 form", low["checksum"] == EIP55_VECTORS[0])
    check("lowercase is not flagged as checksummed input", low["checksum_provided"] is False and low["checksum_ok"] is None)

    up = deployed.validate_address(UPPER)
    check("uppercase address accepted (not rejected for lacking a checksum)", up["valid"] is True, up["reason"])
    check("uppercase -> ok_uppercase", up["reason"] == deployed.REASON_OK_UPPERCASE)
    check("uppercase normalized to lowercase form", up["normalized"] == LOWER)

    good = deployed.validate_address(EIP55_VECTORS[0])
    check("correctly checksummed mixed-case accepted", good["valid"] is True and good["checksum_ok"] is True, good["reason"])
    check("checksummed reason", good["reason"] == deployed.REASON_OK_CHECKSUMMED)

    # Mixed-case with a wrong checksum: still a syntactically valid address,
    # but flagged (never silently corrected).
    bad = deployed.validate_address("0x5AAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")
    check("wrong mixed-case checksum flagged (not rejected as invalid)", bad["valid"] is True and bad["checksum_ok"] is False, bad["reason"])
    check("wrong checksum reason", bad["reason"] == deployed.REASON_CHECKSUM_MISMATCH)
    check("wrong checksum keeps the canonical EIP-55 form", bad["checksum"] == EIP55_VECTORS[0])

    # --- 4. validate_address: rejecting malformed input --------------------
    check("too-short address rejected", deployed.validate_address("0x1234")["valid"] is False)
    check("too-long address rejected", deployed.validate_address("0x" + "a" * 41)["valid"] is False)
    check("non-hex characters rejected", deployed.validate_address("0x" + "z" * 40)["valid"] is False)
    check("empty input rejected", deployed.validate_address("   ")["valid"] is False)
    check("empty input reason", deployed.validate_address("   ")["reason"] == deployed.REASON_EMPTY)
    check("prefix is optional (bare 40 hex accepted)", deployed.validate_address(LOWER[2:])["valid"] is True)

    # --- 5. Chain list is Ethereum-only and unselected by default ----------
    # Ethereum mainnet + the Ethereum testnets Sourcify's official chain list
    # marks supported (checked 2026-10-17; see backend/deployed.py).
    EXPECTED = [1, 11155111, 17000, 560048]
    NON_ETHEREUM_OR_DEPRECATED = [8453, 42161, 10, 137, 56, 43114, 5, 3, 4, 42]
    chains = deployed.CHAIN_OPTIONS
    ids = [c["chain_id"] for c in chains]
    check("chain list is mainnet + supported Ethereum testnets", ids == EXPECTED, str(ids))
    check("chain ids are unique", len(ids) == len(set(ids)))
    check("every chain has a name and 0x-free positive id", all(c.get("name") and isinstance(c["chain_id"], int) and c["chain_id"] > 0 for c in chains))
    check("Ethereum mainnet is not a testnet", chains[0]["chain_id"] == 1 and not chains[0].get("is_testnet"))
    check("the remaining offered networks are flagged testnets", all(c.get("is_testnet") for c in chains[1:]))
    check("no non-Ethereum or deprecated network is offered", not any(c["chain_id"] in NON_ETHEREUM_OR_DEPRECATED for c in chains), str(ids))
    check("SUPPORTED_CHAIN_IDS matches the offered chains", deployed.SUPPORTED_CHAIN_IDS == tuple(EXPECTED))

    # --- 6. Config endpoint is offline, read-only, defaults unset ----------
    r = client.get("/api/deployed/config")
    check("GET /api/deployed/config -> 200", r.status_code == 200, str(r.status_code))
    cfg = r.json() if r.status_code == 200 else {}
    check("config: lookup_available is True (Phase 6.2 provider wired)", cfg.get("lookup_available") is True)
    check("config: no default chain selected", cfg.get("default_chain_id") is None)
    check("config: exactly the supported Ethereum networks are offered", isinstance(cfg.get("chains"), list) and [c["chain_id"] for c in cfg["chains"]] == EXPECTED)
    check("config: consent text present", bool(cfg.get("consent_text")))
    check("config: read-only notice present", "read-only" in (cfg.get("read_only_notice") or "").lower())

    # --- 7. Validate endpoint: local only, never performs a lookup ---------
    r = client.post("/api/deployed/validate-address", json={"address": LOWER})
    check("POST validate (valid) -> 200", r.status_code == 200, str(r.status_code))
    body = r.json() if r.status_code == 200 else {}
    check("validate: address reported valid", body.get("valid") is True)
    check("validate: lookup_performed is False", body.get("lookup_performed") is False)
    check("validate: lookup_available reflects Phase 6.2", body.get("lookup_available") is True)

    r = client.post("/api/deployed/validate-address", json={"address": "0xnot-an-address"})
    check("POST validate (invalid) -> 200 with valid=false", r.status_code == 200 and r.json().get("valid") is False)

    # Consent is echoed as UI state only; even with consent=True no lookup runs.
    r = client.post(
        "/api/deployed/validate-address",
        json={"address": LOWER, "chain_id": 1, "consent": True},
    )
    body = r.json() if r.status_code == 200 else {}
    check("consent=True still performs no lookup", body.get("lookup_performed") is False)
    check("consent is echoed back as recorded state", body.get("consent") is True)

    # --- 8. Frontend: consent defaults OFF, network stays unselected -------
    index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

    m = re.search(r"<input[^>]*id=\"deployedConsent\"[^>]*>", index_html)
    check("frontend: consent checkbox exists", m is not None)
    if m:
        tag = m.group(0).lower()
        check("frontend: consent has type=checkbox", "checkbox" in tag)
        check("frontend: consent is NOT checked by default", "checked" not in tag, tag)
    check("frontend: chain select stays unselected by default", 'id="chainSelect"' in index_html and '<option value="" selected>' in index_html)
    check("frontend: read-only statement present", "Read-only." in index_html)
    check("frontend: states it will not sign/send/deploy/modify", "never signs, sends transactions, deploys, or modifies a contract" in index_html)

    # No hard-coded external endpoints (a proxy for "no explorer/RPC baked in").
    check("frontend app.js has no hard-coded http(s) URL", "http://" not in app_js and "https://" not in app_js)
    check("frontend index.html has no hard-coded http(s) URL", "http://" not in index_html.replace("/api/", "") and "https://" not in index_html)

    # The app actually serves the panel + wiring.
    home = client.get("/")
    check("GET / serves the deployed panel", home.status_code == 200 and 'id="deployedConsent"' in home.text, str(home.status_code))
    js = client.get("/app.js")
    check("GET /app.js serves the new wiring", js.status_code == 200 and "initDeployedPanel" in js.text, str(js.status_code))

    # --- 9. No-outbound-network invariant --------------------------------
    deployed_src = (ROOT / "backend" / "deployed.py").read_text(encoding="utf-8")
    for forbidden in ("import httpx", "import requests", "import urllib", "import socket", "import http.client", "web3"):
        check(f"backend/deployed.py does not use {forbidden!r}", forbidden not in deployed_src)

    import httpx

    class _NetworkUsed(AssertionError):
        pass

    def _boom(*_args, **_kwargs):
        raise _NetworkUsed("outbound HTTP call attempted during Phase 6.1")

    # The app's only outbound HTTP client is httpx (Ollama). TestClient is a
    # synchronous httpx.Client, so patching AsyncClient.send blocks any real
    # outbound model/network call without disturbing the test harness. If either
    # new endpoint tried to call out, it would raise and return a non-200.
    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r1 = client.get("/api/deployed/config")
        r2 = client.post("/api/deployed/validate-address", json={"address": EIP55_VECTORS[1]})
    check("config makes no outbound HTTP call", r1.status_code == 200, str(r1.status_code))
    check("validate makes no outbound HTTP call", r2.status_code == 200, str(r2.status_code))

    # --- 9. The deployed surface is a fixed, known allowlist ---------------
    # Phase 6.1 added the offline config/validation routes; Phase 6.2 added the
    # single consent-gated, read-only lookup; Phase 6.3 added an explicit,
    # analysis-only route (it never fetches — the client posts the source). Pin
    # the exact set so no *other* contract-fetch/resolve/source route can appear
    # unnoticed.
    paths = {getattr(route, "path", "") for route in app.routes}
    deployed_paths = {p for p in paths if p.startswith("/api/deployed/")}
    check(
        "deployed routes are exactly the expected allowlist",
        deployed_paths
        == {
            "/api/deployed/config",
            "/api/deployed/validate-address",
            "/api/deployed/lookup",
            "/api/deployed/analyze-source",
        },
        str(sorted(deployed_paths)),
    )
    check(
        "the consent-gated lookup and the analysis route are present",
        "/api/deployed/lookup" in paths and "/api/deployed/analyze-source" in paths,
    )
    # No contract source is fetched outside the consent-gated lookup: the
    # analysis route must never fetch (it analyzes sources posted by the client).
    unexpected = {
        p
        for p in paths
        if ("fetch" in p.lower() or "resolve" in p.lower() or "source" in p.lower())
        and p not in {"/api/deployed/lookup", "/api/deployed/analyze-source"}
    }
    check("no unexpected contract-fetch/source route added", not unexpected, str(sorted(unexpected)))

    # --- 10. The offline provider stub is deterministic and non-networked --
    import asyncio

    raised = False
    try:
        asyncio.run(deployed.OfflineStubProvider().resolve(1, LOWER))
    except deployed.LookupNotAvailable:
        raised = True
    check("offline provider stub raises LookupNotAvailable (no I/O)", raised)

    print()
    if all(results):
        print("ALL DEPLOYED-REVIEW TESTS PASSED")
        return 0
    print(f"{results.count(False)} TEST(S) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
