"""Focused offline tests for Phase 6.2 deployed-contract verified-source lookup.

Covers, with no real network:
  * Ethereum-network selection: mainnet + the Sourcify-supported Ethereum
    testnets (Sepolia/Holesky/Hoodi), with non-Ethereum chains (Base, Arbitrum,
    ...) and deprecated testnets (Goerli, ...) refused;
  * explicit selection (no default chain) and correct chain-ID forwarding;
  * consent/default behaviour (unchecked default; no lookup without consent);
  * provider success, unverified/not-found, timeout, rate-limit, error, too-large;
  * the invariants that the lookup endpoint performs NO outbound call without
    explicit consent, and that no provider key ever leaks.

The real provider is exercised through ``httpx.MockTransport`` (no sockets);
the app route is exercised through the in-process ``TestClient``.

Run:  py tests/test_deployed_lookup.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import deployed, deployed_source  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.main import app  # noqa: E402

ADDR = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
ADDR_LOWER = ADDR.lower()
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
# Sépolia verified contract from Sourcify's own API docs example (read-only).
SEPOLIA = "0x2738d13E81e30bC615766A0410e7cF199FD59A83"

# Ethereum mainnet + the Ethereum testnets Sourcify's official chain list
# marks ``supported: true`` (checked 2026-10-17; see backend/deployed.py).
EXPECTED_CHAINS = [1, 11155111, 17000, 560048]
# Non-Ethereum networks (Sourcify may support them, but this app must not offer
# them) plus a deprecated Ethereum testnet Sourcify no longer supports.
REJECTED_CHAINS = [8453, 42161, 10, 137, 56, 43114, 5, 3, 4]

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail else ''}")


def verified_body() -> dict:
    return {
        "match": "exact_match",
        "creationMatch": "exact_match",
        "runtimeMatch": "exact_match",
        "chainId": "1",
        "address": WETH,
        "verifiedAt": "2024-08-08T13:28:37Z",
        "matchId": "1605500",
        "sources": {"WETH9.sol": {"content": "// SPDX-License-Identifier: MIT\ncontract WETH9 {}\n"}},
        "compilation": {
            "language": "Solidity",
            "compiler": "solc",
            "compilerVersion": "0.4.19+commit.c4cbbb05",
            "name": "WETH9",
            "fullyQualifiedName": "WETH9.sol:WETH9",
        },
        "abi": [{"type": "function", "name": "deposit"}],
        "metadata": {"compiler": {"version": "0.4.19"}},
        "deployment": {"blockNumber": "4719568"},
    }


def make_provider(handler) -> deployed_source.SourcifyProvider:
    return deployed_source.SourcifyProvider(transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def main() -> int:
    client = TestClient(app, raise_server_exceptions=False)

    # --- 1. Ethereum networks: mainnet + Sourcify-supported testnets ------
    check(
        "chain options == mainnet + supported Ethereum testnets",
        [c["chain_id"] for c in deployed.CHAIN_OPTIONS] == EXPECTED_CHAINS,
        str([c["chain_id"] for c in deployed.CHAIN_OPTIONS]),
    )
    check("SUPPORTED_CHAIN_IDS matches the chain options", deployed.SUPPORTED_CHAIN_IDS == tuple(EXPECTED_CHAINS))
    check("mainnet is offered and not flagged as a testnet", deployed.CHAIN_OPTIONS[0]["chain_id"] == 1 and deployed.CHAIN_OPTIONS[0]["is_testnet"] is False)
    check(
        "every other offered network is a flagged Ethereum testnet",
        all(c["is_testnet"] is True for c in deployed.CHAIN_OPTIONS[1:]) and len(deployed.CHAIN_OPTIONS) == 4,
    )
    check(
        "no non-Ethereum or deprecated network is offered",
        not any(c["chain_id"] in REJECTED_CHAINS for c in deployed.CHAIN_OPTIONS),
        str([c["chain_id"] for c in deployed.CHAIN_OPTIONS]),
    )
    check(
        "every offered network has a human-readable name",
        all(c.get("name") for c in deployed.CHAIN_OPTIONS),
    )
    cfg = client.get("/api/deployed/config").json()
    check("config offers exactly the supported Ethereum networks", [c["chain_id"] for c in cfg["chains"]] == EXPECTED_CHAINS)
    check("config lookup_available is True", cfg["lookup_available"] is True)
    check("config default chain is unset (no silent default)", cfg["default_chain_id"] is None)

    # --- 2. Consent/default behaviour (no provider call without consent) ---
    calls: list[tuple[int, str]] = []

    class _RecordingProvider:
        name = "recording"

        async def resolve(self, chain_id, address):
            calls.append((chain_id, address))
            return {"status": deployed_source.STATUS_VERIFIED, "http_status": 200, "data": verified_body(), "detail": None}

    # No consent -> refusal, and the provider is never called.
    calls.clear()
    r = run(deployed_source.lookup_verified_source(ADDR, 1, False, provider=_RecordingProvider()))
    check("no consent -> consent_required", r["status"] == deployed_source.STATUS_CONSENT_REQUIRED, r["status"])
    check("no consent -> lookup_performed False", r["lookup_performed"] is False)
    check("no consent -> provider not called", calls == [])

    # Invalid address -> refusal before any provider call.
    calls.clear()
    r = run(deployed_source.lookup_verified_source("0x1234", 1, True, provider=_RecordingProvider()))
    check("invalid address -> invalid_address", r["status"] == deployed_source.STATUS_INVALID_ADDRESS, r["status"])
    check("invalid address -> provider not called", calls == [])

    # Non-Ethereum / deprecated chains -> refusal before any provider call.
    for bad_chain, label in ((8453, "Base"), (42161, "Arbitrum"), (5, "Goerli")):
        calls.clear()
        r = run(deployed_source.lookup_verified_source(ADDR, bad_chain, True, provider=_RecordingProvider()))
        check(f"unsupported chain {bad_chain} ({label}) -> unsupported_chain", r["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r["status"])
        check(f"unsupported chain {bad_chain} ({label}) -> provider not called", calls == [])

    # The refusal names the supported networks instead of silently switching.
    r = run(deployed_source.lookup_verified_source(ADDR, 42161, True, provider=_RecordingProvider()))
    check("unsupported-chain message lists supported networks", "Sepolia" in r["message"] and "Ethereum Mainnet" in r["message"], r["message"])

    # Consent + valid + each supported chain -> provider called once with that ID.
    for chain_id in EXPECTED_CHAINS:
        calls.clear()
        r = run(deployed_source.lookup_verified_source(ADDR, chain_id, True, provider=_RecordingProvider()))
        check(f"consent+valid+chain {chain_id} -> provider called once", calls == [(chain_id, ADDR_LOWER)], str(calls))
        check(f"consent+valid+chain {chain_id} -> verified", r["status"] == deployed_source.STATUS_VERIFIED, r["status"])
        check(f"consent+valid+chain {chain_id} -> chain echoed", r["chain_id"] == chain_id and r["chain_name"] == deployed_source.chain_name(chain_id))
    check("verified -> lookup_performed True", r["lookup_performed"] is True)

    # --- 3. Provider success via a real (mock-transport) Sourcify call -----
    seen: dict = {}

    def ok_handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json=verified_body())

    r = run(deployed_source.lookup_verified_source(WETH, 1, True, provider=make_provider(ok_handler)))
    check("provider success -> verified", r["status"] == deployed_source.STATUS_VERIFIED, r["status"])
    check("provider success -> verified flag", r["verified"] is True)
    check("provider success -> match reported", r["match"] == "exact_match")
    check("provider success -> verified_at", r["verified_at"] == "2024-08-08T13:28:37Z")
    check("provider success -> contract name", r["contract_name"] == "WETH9")
    check("provider success -> compiler version", r["compiler_version"] == "0.4.19+commit.c4cbbb05")
    check("provider success -> one source file", len(r["sources"]) == 1 and r["sources"][0]["path"] == "WETH9.sol")
    check("provider success -> source content present", "contract WETH9" in r["sources"][0]["content"])
    check("provider success -> abi entries", r["abi_entries"] == 1)
    check("provider success -> metadata present", r["metadata_present"] is True)
    check("provider success -> deployment block", r["deployment_block"] == "4719568")
    check("provider success -> chain name", r["chain_name"] == "Ethereum Mainnet")
    check("provider success -> message says not audited", "NOT audited" in r["message"])
    check("provider success -> address is checksummed EIP-55", r["checksum"] == WETH)
    check("request targets Sourcify /v2/contract/1/<addr>", "/v2/contract/1/" in seen.get("url", ""), seen.get("url", ""))
    check("request uses a bounded field selector incl. sources", "sources" in (seen.get("fields") or ""))

    # Each supported chain ID is forwarded into the Sourcify request path.
    for chain_id in EXPECTED_CHAINS:
        seen.clear()
        r = run(deployed_source.lookup_verified_source(SEPOLIA, chain_id, True, provider=make_provider(ok_handler)))
        check(
            f"chain {chain_id} is used in the Sourcify request path",
            f"/v2/contract/{chain_id}/" in seen.get("url", ""),
            seen.get("url", ""),
        )
        check(
            f"chain {chain_id} response carries the human-readable name",
            r["status"] == deployed_source.STATUS_VERIFIED and r["chain_name"] == deployed_source.chain_name(chain_id),
            str(r.get("chain_name")),
        )

    # --- 4. Unverified / not-found ----------------------------------------
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"match": None, "creationMatch": None, "runtimeMatch": None})

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(not_found_handler)))
    check("404 -> not_verified", r["status"] == deployed_source.STATUS_NOT_VERIFIED, r["status"])
    check("404 -> no source fabricated", r["sources"] == [] and r["verified"] is False)
    check("404 -> honest message", "No verified source" in r["message"], r["message"])

    def null_match_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"match": None, "chainId": "1", "address": ADDR})

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(null_match_handler)))
    check("200 with null match -> not_verified", r["status"] == deployed_source.STATUS_NOT_VERIFIED, r["status"])

    # --- 5. Rate limit / timeout / error / too-large ----------------------
    def rate_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(rate_handler)))
    check("429 -> rate_limited", r["status"] == deployed_source.STATUS_RATE_LIMITED, r["status"])
    check("429 -> message mentions rate limit", "rate limit" in r["message"].lower(), r["message"])

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(timeout_handler)))
    check("timeout -> timeout status", r["status"] == deployed_source.STATUS_TIMEOUT, r["status"])
    check("timeout -> no source", r["sources"] == [])

    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(error_handler)))
    check("500 -> provider_error", r["status"] == deployed_source.STATUS_PROVIDER_ERROR, r["status"])

    def badjson_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json{")

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(badjson_handler)))
    check("200 non-JSON -> provider_error", r["status"] == deployed_source.STATUS_PROVIDER_ERROR, r["status"])

    def unsupported_chain_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"customCode": "unsupported_chain", "message": "Chain 1 not found"})

    r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(unsupported_chain_handler)))
    check("400 unsupported_chain -> unsupported_chain", r["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r["status"])

    # Too large: shrink the response cap so the mock body exceeds it.
    original_cap = settings.deployed_max_response_bytes
    settings.deployed_max_response_bytes = 16
    try:
        r = run(deployed_source.lookup_verified_source(ADDR, 1, True, provider=make_provider(ok_handler)))
    finally:
        settings.deployed_max_response_bytes = original_cap
    check("oversized response -> too_large", r["status"] == deployed_source.STATUS_TOO_LARGE, r["status"])
    check("oversized response -> no source", r["sources"] == [])

    # Source cap: shrink the returned-source budget; content is truncated, not lost-safe.
    original_src = settings.deployed_max_source_bytes
    settings.deployed_max_source_bytes = 10
    try:
        r = run(deployed_source.lookup_verified_source(WETH, 1, True, provider=make_provider(ok_handler)))
    finally:
        settings.deployed_max_source_bytes = original_src
    check("source budget -> truncation flagged", r["sources_truncated"] is True)
    check("source budget -> content bounded", len(r["sources"][0]["content"]) == 10)

    # --- 6. Secret handling: key is sent but never logged/returned ---------
    captured: dict = {}

    def key_handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=verified_body())

    original_key = settings.sourcify_api_key
    settings.sourcify_api_key = "SECRET-TEST-KEY-987"
    try:
        r = run(deployed_source.lookup_verified_source(WETH, 1, True, provider=make_provider(key_handler)))
        summary = str(__import__("backend.config", fromlist=["_env_summary"])._env_summary())
        blob = json.dumps(r)
    finally:
        settings.sourcify_api_key = original_key
    check("optional key is sent as a bearer header", captured.get("auth") == "Bearer SECRET-TEST-KEY-987")
    check("key never appears in the lookup result", "SECRET-TEST-KEY-987" not in blob)
    check("key never appears in the env summary", "SECRET-TEST-KEY-987" not in summary)
    check("env summary only marks the key set/unset", "sourcify_api_key=set" in summary)

    # --- 7. Route: no outbound call without consent ------------------------
    def _boom(*_args, **_kwargs):
        raise AssertionError("outbound HTTP call attempted without consent")

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": ADDR, "chain_id": 1, "consent": False})
    check("route (no consent) -> 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("route (no consent) -> consent_required", body["status"] == deployed_source.STATUS_CONSENT_REQUIRED, body["status"])
    check("route (no consent) -> lookup_performed False", body["lookup_performed"] is False)

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": "0xzz", "chain_id": 1, "consent": True})
    check("route (invalid address) -> no outbound call", r.json()["status"] == deployed_source.STATUS_INVALID_ADDRESS)

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": ADDR, "chain_id": 8453, "consent": True})
    check("route (Base 8453) -> unsupported_chain, no outbound call", r.json()["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r.json()["status"])

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": ADDR, "chain_id": 42161, "consent": True})
    check("route (Arbitrum 42161) -> unsupported_chain, no outbound call", r.json()["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r.json()["status"])

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": ADDR, "chain_id": 5, "consent": True})
    check("route (Goerli 5, deprecated) -> unsupported_chain, no outbound call", r.json()["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r.json()["status"])

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.post("/api/deployed/lookup", json={"address": ADDR, "consent": True})
    check("route (no chain selected) -> unsupported_chain, no outbound call", r.json()["status"] == deployed_source.STATUS_UNSUPPORTED_CHAIN, r.json()["status"])

    with mock.patch.object(httpx.AsyncClient, "send", _boom):
        r = client.get("/api/deployed/config")
        r2 = client.post("/api/deployed/validate-address", json={"address": ADDR})
    check("config still makes no outbound call", r.status_code == 200)
    check("validate still makes no outbound call", r2.status_code == 200)

    # --- 8. Route: consent + valid address DOES reach the (mocked) provider -
    class _TestProvider(deployed_source.SourcifyProvider):
        def __init__(self) -> None:  # noqa: D107
            super().__init__(transport=httpx.MockTransport(ok_handler))

    original_provider = deployed_source.SourcifyProvider
    deployed_source.SourcifyProvider = _TestProvider  # type: ignore[misc]
    try:
        r = client.post("/api/deployed/lookup", json={"address": WETH, "chain_id": 1, "consent": True})
        sepolia_res = client.post("/api/deployed/lookup", json={"address": SEPOLIA, "chain_id": 11155111, "consent": True})
        holesky_res = client.post("/api/deployed/lookup", json={"address": SEPOLIA, "chain_id": 17000, "consent": True})
    finally:
        deployed_source.SourcifyProvider = original_provider  # type: ignore[misc]
    check("route (consent+valid) -> 200", r.status_code == 200, str(r.status_code))
    body = r.json()
    check("route (consent+valid) -> verified", body["status"] == deployed_source.STATUS_VERIFIED, body["status"])
    check("route (consent+valid) -> lookup_performed True", body["lookup_performed"] is True)
    check("route (consent+valid) -> source returned", len(body["sources"]) == 1)
    check("route response has provider provenance", body["provider"] == "sourcify" and body["chain_id"] == 1)
    check("route response never carries a key field", not any("key" in k.lower() for k in body))

    sepolia_body = sepolia_res.json()
    check(
        "route forwards Sepolia (11155111) and returns its name",
        sepolia_res.status_code == 200
        and sepolia_body["status"] == deployed_source.STATUS_VERIFIED
        and sepolia_body["chain_id"] == 11155111
        and sepolia_body["chain_name"] == "Ethereum Sepolia Testnet",
        str(sepolia_body.get("chain_name")),
    )
    holesky_body = holesky_res.json()
    check(
        "route forwards Holesky (17000) and returns its name",
        holesky_res.status_code == 200
        and holesky_body["status"] == deployed_source.STATUS_VERIFIED
        and holesky_body["chain_id"] == 17000
        and holesky_body["chain_name"] == "Ethereum Holesky Testnet",
        str(holesky_body.get("chain_name")),
    )

    # --- 9. Frontend: explicit action only, consent off by default ---------
    index_html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    check("frontend has the explicit Retrieve button", 'id="deployedLookupBtn"' in index_html)
    check("frontend lookup button starts disabled", 'id="deployedLookupBtn" type="button" disabled' in index_html)
    check("frontend consent checkbox is not checked", 'id="deployedConsent" />' in index_html and 'id="deployedConsent" checked' not in index_html)
    chain_select = re.search(r'<select id="chainSelect".*?</select>', index_html, re.S)
    check(
        "frontend has exactly one static <option> in the chain select (the placeholder)",
        chain_select is not None
        and chain_select.group(0).count("<option") == 1
        and '<option value="" selected>' in chain_select.group(0),
    )
    check("frontend hard-codes no chain ids into options", 'value="8453"' not in index_html and 'value="42161"' not in index_html and 'value="11155111"' not in index_html)
    check("frontend loads supported chain ids from config, not a hard-coded list", "supportedChainIds = new Set(" in app_js and "chainId === '1'" not in app_js)
    check("frontend gates the button on the config-supported chain set", "supportedChainIds.has(deployedState.chainId)" in app_js)
    check("frontend keeps the network unselected (never auto-selects)", "select.value = ''" in app_js)
    check("frontend wires the explicit click handler", "lookupBtn.addEventListener('click', runDeployedLookup)" in app_js)
    check("frontend has no hard-coded http(s) URL", "http://" not in app_js and "https://" not in app_js)

    print()
    if all(results):
        print("ALL DEPLOYED-LOOKUP TESTS PASSED")
        return 0
    print(f"{results.count(False)} TEST(S) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
