"""Phase 6.2 — consent-gated, read-only retrieval of verified contract source.

This is the *only* module in the project that performs an outbound request for a
deployed-contract lookup. It implements the :class:`backend.deployed.SourceProvider`
seam with a single, allowlisted provider — **Sourcify** — whose reads require no
API key (``https://sourcify.dev/server/v2/contract/{chainId}/{address}``; docs at
:data:`PROVIDER_DOCS`, checked 2026-10-17).

Hard constraints (``plan.md`` Phase 6.2; threat model ``research.md`` §16.9):

* **Read-only, public data only.** It fetches a contract's *publicly verified*
  source and basic metadata. It never connects a wallet, signs, sends a
  transaction, deploys, or modifies anything.
* **Explicit consent required.** :func:`lookup_verified_source` performs **no**
  network call unless ``consent is True`` *and* the chain is one of the
  supported Ethereum networks *and* the address is a valid 20-byte EVM address.
  Nothing here is triggered by typing, a toggle, page load, or a model.
* **Ethereum networks only — mainnet + provider-supported testnets.** Only the
  chains in :data:`backend.deployed.SUPPORTED_CHAIN_IDS` are accepted (as of
  2026-10-17: mainnet **1**, Sepolia **11155111**, Holesky **17000**, Hoodi
  **560048** — the Ethereum chains Sourcify's official list marks
  supported). Base/Arbitrum/other non-Ethereum networks and deprecated
  Ethereum testnets (Goerli, Ropsten, Rinkeby — ``supported: false``) are
  refused **before** any request, and the UI never silently defaults to a
  chain (``research.md`` §16.10 Q2 resolved 2026-10-17, testnets added by the
  2026-10-17 network decision).
* **Allowlisted URL, bounded size/time.** The base URL is fixed configuration
  (never user-supplied), the request is time-boxed
  (``settings.deployed_lookup_timeout``), and the response is read with a byte
  cap (``settings.deployed_max_response_bytes``).
* **No secret leakage.** An *optional* provider key is read only from the
  server-side environment (``settings.sourcify_api_key``); it is never logged and
  never included in any response. Sourcify reads do not require a key, so it is
  normally unset.
* **Honest status.** The exact provider outcome is returned as a machine status
  plus a human message. Unverified / not-found / rate-limited / timed-out / error
  outcomes are reported plainly and never fabricated into source.

This module does **not** run Slither, Qwen, or any analyzer, and does no
proxy/UUPS detection — those are later phases. The retrieved source is presented
for human review, not audited here.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from . import deployed
from .config import settings

# --------------------------------------------------------------------------
# Provider identity / provenance
# --------------------------------------------------------------------------

#: Allowlisted provider name (single provider in this phase).
PROVIDER_NAME = "sourcify"
PROVIDER_DOCS = "https://docs.sourcify.dev/docs/api/"

#: Bounded field selector. Deliberately excludes bytecode and the full standard
#: JSON input/output so the response stays small (Sourcify's ``fields=all`` is
#: the most expensive request on the endpoint; docs checked 2026-10-17).
_FIELDS = (
    "matchId,creationMatch,runtimeMatch,verifiedAt,abi,metadata,"
    "sources,compilation,deployment.blockNumber"
)

# Machine-readable lookup status codes.
STATUS_VERIFIED = "verified"
STATUS_NOT_VERIFIED = "not_verified"
STATUS_INVALID_ADDRESS = "invalid_address"
STATUS_UNSUPPORTED_CHAIN = "unsupported_chain"
STATUS_CONSENT_REQUIRED = "consent_required"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_TIMEOUT = "timeout"
STATUS_PROVIDER_ERROR = "provider_error"
STATUS_TOO_LARGE = "too_large"

STATUS_CODES = (
    STATUS_VERIFIED,
    STATUS_NOT_VERIFIED,
    STATUS_INVALID_ADDRESS,
    STATUS_UNSUPPORTED_CHAIN,
    STATUS_CONSENT_REQUIRED,
    STATUS_RATE_LIMITED,
    STATUS_TIMEOUT,
    STATUS_PROVIDER_ERROR,
    STATUS_TOO_LARGE,
)

_USER_AGENT = "auditor-workbench/0.4 (+local read-only verified-source lookup)"


def chain_name(chain_id: int | None) -> str | None:
    """Return the configured display name for ``chain_id`` (or ``None``)."""
    for chain in deployed.CHAIN_OPTIONS:
        if chain["chain_id"] == chain_id:
            return str(chain["name"])
    return None


def supported_networks_text() -> str:
    """Human-readable list of the configured networks (offline, no I/O).

    Used in refusal messages so the user is told exactly which networks are
    available instead of being silently moved to another chain.
    """
    return "; ".join(
        f"{chain['name']} (chain ID {chain['chain_id']})"
        for chain in deployed.CHAIN_OPTIONS
    )


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class SourcifyProvider:
    """Read-only Sourcify v2 provider (see module docstring for constraints).

    ``transport`` is an injection seam for tests (``httpx.MockTransport``); it
    is never set in production and does not change the request shape.
    """

    name = PROVIDER_NAME

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        """Request headers. The optional key is added but never logged/returned."""
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        key = (settings.sourcify_api_key or "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _url(self, chain_id: int, normalized_address: str) -> str:
        base = settings.sourcify_base_url.rstrip("/")
        return f"{base}/v2/contract/{chain_id}/{normalized_address}"

    async def resolve(self, chain_id: int, address: str) -> dict[str, Any]:
        """Fetch verified source/metadata for ``address`` on ``chain_id``.

        Returns an envelope ``{status, http_status, data, detail}``. Expected
        failures (unverified, rate limit, timeout, error, too large) are returned
        as statuses — this never raises for a provider-side condition. It still
        refuses anything other than a supported chain / valid address *before*
        making a request.
        """
        if chain_id not in deployed.SUPPORTED_CHAIN_IDS:
            return {
                "status": STATUS_UNSUPPORTED_CHAIN,
                "http_status": None,
                "data": None,
                "detail": (
                    f"Chain {chain_id} is not supported. Ethereum mainnet and "
                    "Sourcify-supported Ethereum testnets only "
                    f"({supported_networks_text()})."
                ),
            }

        validation = deployed.validate_address(address)
        if not validation["valid"]:
            return {
                "status": STATUS_INVALID_ADDRESS,
                "http_status": None,
                "data": None,
                "detail": validation["message"],
            }

        url = self._url(chain_id, validation["normalized"])
        timeout = settings.deployed_lookup_timeout
        max_bytes = settings.deployed_max_response_bytes

        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                async with client.stream(
                    "GET", url, params={"fields": _FIELDS}, headers=self._headers()
                ) as response:
                    http_status = response.status_code
                    buffer = bytearray()
                    async for chunk in response.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > max_bytes:
                            return {
                                "status": STATUS_TOO_LARGE,
                                "http_status": http_status,
                                "data": None,
                                "detail": (
                                    "Provider response exceeded the "
                                    f"{max_bytes}-byte cap; nothing was returned."
                                ),
                            }
                    raw = bytes(buffer)
        except httpx.TimeoutException:
            return {
                "status": STATUS_TIMEOUT,
                "http_status": None,
                "data": None,
                "detail": f"Provider did not respond within {timeout:g}s.",
            }
        except httpx.HTTPError as exc:
            return {
                "status": STATUS_PROVIDER_ERROR,
                "http_status": None,
                "data": None,
                "detail": f"Provider request failed ({type(exc).__name__}).",
            }

        return self._map_response(http_status, raw)

    @staticmethod
    def _map_response(http_status: int, raw: bytes) -> dict[str, Any]:
        """Map a provider HTTP response to an envelope (no network here)."""
        if http_status == 404:
            return {
                "status": STATUS_NOT_VERIFIED,
                "http_status": 404,
                "data": None,
                "detail": "No verified source is registered for this address.",
            }
        if http_status == 429:
            return {
                "status": STATUS_RATE_LIMITED,
                "http_status": 429,
                "data": None,
                "detail": "Provider rate limit reached (HTTP 429).",
            }
        if http_status == 400:
            code = _error_code(raw)
            if code == "unsupported_chain":
                return {
                    "status": STATUS_UNSUPPORTED_CHAIN,
                    "http_status": 400,
                    "data": None,
                    "detail": _error_message(raw)
                    or "Provider does not support that chain.",
                }
            return {
                "status": STATUS_PROVIDER_ERROR,
                "http_status": 400,
                "data": None,
                "detail": _error_message(raw) or "Provider rejected the request.",
            }
        if http_status != 200:
            return {
                "status": STATUS_PROVIDER_ERROR,
                "http_status": http_status,
                "data": None,
                "detail": f"Provider returned HTTP {http_status}.",
            }

        try:
            data = json.loads(raw)
        except ValueError:
            return {
                "status": STATUS_PROVIDER_ERROR,
                "http_status": 200,
                "data": None,
                "detail": "Provider returned a non-JSON body.",
            }
        if not isinstance(data, dict):
            return {
                "status": STATUS_PROVIDER_ERROR,
                "http_status": 200,
                "data": None,
                "detail": "Provider returned an unexpected JSON shape.",
            }
        # A verified record carries a non-null match status.
        if not (data.get("match") or data.get("creationMatch") or data.get("runtimeMatch")):
            return {
                "status": STATUS_NOT_VERIFIED,
                "http_status": 200,
                "data": data,
                "detail": "Provider returned no match (not verified).",
            }
        return {"status": STATUS_VERIFIED, "http_status": 200, "data": data, "detail": None}


def _error_code(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if isinstance(payload, dict):
        code = payload.get("customCode") or payload.get("code")
        return str(code) if code else None
    return None


def _error_message(raw: bytes) -> str | None:
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if isinstance(payload, dict):
        message = payload.get("message")
        return str(message) if message else None
    return None


# --------------------------------------------------------------------------
# Orchestration used by the API route
# --------------------------------------------------------------------------


def _empty_result(address: str, chain_id: int | None, consent: bool) -> dict[str, Any]:
    """The response scaffold. ``lookup_performed`` is set True only on a real call."""
    validation = deployed.validate_address(address)
    return {
        "status": STATUS_PROVIDER_ERROR,
        "lookup_performed": False,
        "consent": bool(consent),
        "chain_id": chain_id,
        "chain_name": chain_name(chain_id),
        "address": (validation.get("normalized") or (address or "").strip()),
        "checksum": validation.get("checksum"),
        "provider": PROVIDER_NAME,
        "provider_docs": PROVIDER_DOCS,
        "verified": False,
        "match": None,
        "creation_match": None,
        "runtime_match": None,
        "verified_at": None,
        "match_id": None,
        "contract_name": None,
        "compiler_version": None,
        "language": None,
        "deployment_block": None,
        "sources": [],
        "sources_truncated": False,
        "source_file_count": 0,
        "abi_entries": 0,
        "metadata_present": False,
        "message": "",
        "notice": deployed.READ_ONLY_NOTICE,
    }


def _extract_sources(data: dict[str, Any], cap: int) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(files, truncated)`` for the verified source, bounded by ``cap``.

    ``cap`` bounds the total number of source **characters** returned (Solidity
    sources are ASCII, so this is effectively a byte budget). Content beyond the
    cap is dropped and ``truncated`` is set True; this never raises.
    """
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, dict):
        return [], False

    files: list[dict[str, Any]] = []
    truncated = False
    total = 0
    for path, obj in raw_sources.items():
        if isinstance(obj, dict):
            content = obj.get("content")
        elif isinstance(obj, str):
            content = obj
        else:
            content = None
        content = content if isinstance(content, str) else ""
        remaining = cap - total
        if remaining <= 0:
            content = ""
            truncated = True
        elif len(content) > remaining:
            content = content[:remaining]
            truncated = True
        files.append({"path": str(path), "chars": len(content), "content": content})
        total += len(content)
    return files, truncated


def _verified_message(data: dict[str, Any], chain_label: str | None) -> str:
    match = data.get("match")
    if match == "exact_match":
        head = "Verified (exact bytecode match)"
    else:
        head = "Verified (partial match — metadata/runtime may differ)"
    where = f" on {chain_label}" if chain_label else ""
    return (
        f"{head}{where} via Sourcify. Source retrieved read-only. "
        "This workbench has NOT audited it (no analyzer or model ran)."
    )


async def lookup_verified_source(
    address: str,
    chain_id: int | None,
    consent: bool,
    *,
    provider: Any | None = None,
) -> dict[str, Any]:
    """Consent-gated, read-only verified-source lookup (the route's entry point).

    Performs **no** network call unless ``consent is True``, ``chain_id`` is one
    of the supported Ethereum networks (mainnet + Sourcify-supported testnets),
    and ``address`` is a valid 20-byte hex address. Returns a JSON-friendly
    dict; expected failures become statuses and never raise.
    """
    result = _empty_result(address, chain_id, consent)

    # 1) Address must be valid before anything else.
    validation = deployed.validate_address(address)
    if not validation["valid"]:
        result["status"] = STATUS_INVALID_ADDRESS
        result["message"] = validation["message"]
        return result

    # 2) Supported Ethereum networks only (mainnet + Sourcify-supported
    #    testnets). No silent fallback to mainnet or any other chain.
    if chain_id not in deployed.SUPPORTED_CHAIN_IDS:
        result["status"] = STATUS_UNSUPPORTED_CHAIN
        result["message"] = (
            "Select a supported Ethereum network: "
            f"{supported_networks_text()}. Non-Ethereum networks (e.g. Base, "
            "Arbitrum) and testnets Sourcify no longer supports (e.g. Goerli) "
            "are not available."
        )
        return result

    # 3) Explicit consent is required. No consent → no lookup, ever.
    if not consent:
        result["status"] = STATUS_CONSENT_REQUIRED
        result["message"] = (
            "Explicit consent is required before any lookup. Nothing was retrieved."
        )
        return result

    # 4) Only now do we call the provider (this is the sole outbound call).
    provider = provider or SourcifyProvider()
    result["lookup_performed"] = True
    envelope = await provider.resolve(chain_id, validation["normalized"])
    status = envelope.get("status", STATUS_PROVIDER_ERROR)
    result["status"] = status

    data = envelope.get("data")
    chain_label = chain_name(chain_id)

    if status != STATUS_VERIFIED or not isinstance(data, dict):
        # Honest, plain reporting. Never fabricate source.
        messages = {
            STATUS_NOT_VERIFIED: (
                "No verified source was found on Sourcify for this address. "
                "Nothing was fabricated."
            ),
            STATUS_RATE_LIMITED: (
                "Sourcify rate limit reached (HTTP 429). Try again later."
            ),
            STATUS_TIMEOUT: (
                "The Sourcify lookup timed out. Nothing was retrieved."
            ),
            STATUS_TOO_LARGE: (
                "The Sourcify response exceeded the size cap. Nothing was returned."
            ),
            STATUS_UNSUPPORTED_CHAIN: (
                "Sourcify does not support that chain."
            ),
            STATUS_PROVIDER_ERROR: (
                "Sourcify returned an unexpected response. Nothing was retrieved."
            ),
        }
        result["message"] = messages.get(
            status, "The lookup did not return verified source."
        )
        detail = envelope.get("detail")
        if detail:
            result["message"] = f"{result['message']} ({detail})"
        return result

    # 5) Shape the verified record.
    sources, truncated = _extract_sources(data, settings.deployed_max_source_bytes)
    compilation = data.get("compilation") if isinstance(data.get("compilation"), dict) else {}
    deployment = data.get("deployment") if isinstance(data.get("deployment"), dict) else {}
    abi = data.get("abi")

    result.update(
        {
            "verified": True,
            "match": data.get("match"),
            "creation_match": data.get("creationMatch"),
            "runtime_match": data.get("runtimeMatch"),
            "verified_at": data.get("verifiedAt"),
            "match_id": str(data["matchId"]) if data.get("matchId") is not None else None,
            "contract_name": compilation.get("name") or compilation.get("fullyQualifiedName"),
            "compiler_version": compilation.get("compilerVersion"),
            "language": compilation.get("language"),
            "deployment_block": deployment.get("blockNumber"),
            "sources": sources,
            "sources_truncated": truncated,
            "source_file_count": len(sources),
            "abi_entries": len(abi) if isinstance(abi, list) else 0,
            "metadata_present": isinstance(data.get("metadata"), dict),
            "message": _verified_message(data, chain_label),
        }
    )
    return result


__all__ = [
    "PROVIDER_DOCS",
    "PROVIDER_NAME",
    "STATUS_CODES",
    "STATUS_CONSENT_REQUIRED",
    "STATUS_INVALID_ADDRESS",
    "STATUS_NOT_VERIFIED",
    "STATUS_PROVIDER_ERROR",
    "STATUS_RATE_LIMITED",
    "STATUS_TIMEOUT",
    "STATUS_TOO_LARGE",
    "STATUS_UNSUPPORTED_CHAIN",
    "STATUS_VERIFIED",
    "SourcifyProvider",
    "chain_name",
    "lookup_verified_source",
    "supported_networks_text",
]
