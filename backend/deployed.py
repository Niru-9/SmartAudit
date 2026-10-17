"""Offline groundwork for the *read-only* deployed-contract review.

Decision-independent foundation for the "review a deployed contract address,
read-only" extension (``plan.md`` Phase 6.x; evidence in ``research.md`` §16).
This module itself performs **no outbound I/O of any kind**:

* it defines the configurable chain list (:data:`CHAIN_OPTIONS`) —
  **Ethereum mainnet (chain ID 1) plus the Ethereum testnets that Sourcify
  officially supports** (Sepolia 11155111, Holesky 17000, Hoodi 560048;
  official list checked 2026-10-17). Ethereum networks only — Base, Arbitrum
  and every other non-Ethereum network are excluded, and deprecated testnets
  Sourcify no longer supports (Goerli, Ropsten, Rinkeby) are not offered;
* it validates and normalizes an EVM address (20 bytes of hex) per EIP-55;
* it exposes the exact **consent** wording and a ``lookup_available`` flag;
* it marks the :class:`SourceProvider` seam (with an offline stub). Phase 6.2's
  real, consent-gated provider (Sourcify) lives in
  :mod:`backend.deployed_source` — the network call is made *there*, never in
  this module.

It never reads a blockchain, calls an explorer/RPC, runs an analyzer, fetches a
contract, or touches MCP. Address validation is entirely offline.

EIP-55 note: the checksum is Keccak-256 (the original Keccak — *not* the NIST
``SHA3-256`` that ``hashlib`` provides), so a tiny, dependency-free Keccak-256
is implemented below. ``requirements.txt`` declares no crypto/keccak library
and this project prefers no new dependency for a single checksum, so the
algorithm is inlined rather than pulling a package in. Lowercase and uppercase
addresses carry no checksum information and are accepted as-is; only a
mixed-case address is checked (a mismatch is *flagged*, never silently
"corrected").
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

# --------------------------------------------------------------------------
# Dependency-free Keccak-256 (EIP-55 checksum base)
# --------------------------------------------------------------------------

_MASK64 = 0xFFFFFFFFFFFFFFFF

# 24 round constants for Keccak-f[1600].
_KECCAK_ROUND_CONSTANTS: tuple[int, ...] = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# rho rotation offsets, flattened as r[x + 5*y] (x varies fastest).
_KECCAK_ROTATIONS: tuple[int, ...] = (
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
)

_KECCAK_LANES = 25
# 1088-bit rate => 136 bytes for Keccak-256.
_KECCAK_RATE_BYTES = 136


def _rotl64(value: int, shift: int) -> int:
    value &= _MASK64
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK64


def _keccak_f1600(state: list[int]) -> None:
    """Apply Keccak-f[1600] to ``state`` (25 lanes) in place."""
    for rc in _KECCAK_ROUND_CONSTANTS:
        # theta
        c = [
            state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20]
            for x in range(5)
        ]
        d = [c[(x - 1) % 5] ^ _rotl64(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]

        # rho + pi
        b = [0] * _KECCAK_LANES
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl64(
                    state[x + 5 * y], _KECCAK_ROTATIONS[x + 5 * y]
                )

        # chi
        for x in range(5):
            for y in range(5):
                idx = x + 5 * y
                not_next = (~b[((x + 1) % 5) + 5 * y]) & _MASK64
                state[idx] = (
                    b[idx] ^ (not_next & b[((x + 2) % 5) + 5 * y])
                ) & _MASK64

        # iota
        state[0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Return the 32-byte Keccak-256 digest of ``data`` (original Keccak)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("keccak256 expects a bytes-like object")
    rate = _KECCAK_RATE_BYTES
    state = [0] * _KECCAK_LANES

    # Keccak pad10*1 with domain byte 0x01 (NOT SHA3's 0x06); final byte 0x80.
    padded = bytearray(bytes(data))
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0x00)
    padded[-1] ^= 0x80

    for offset in range(0, len(padded), rate):
        block = padded[offset:offset + rate]
        for lane in range(rate // 8):
            state[lane] ^= int.from_bytes(block[lane * 8:lane * 8 + 8], "little")
        _keccak_f1600(state)

    # 32-byte output = the first four lanes (little-endian).
    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


# --------------------------------------------------------------------------
# Chains — Ethereum mainnet + Sourcify-supported Ethereum testnets ONLY
# --------------------------------------------------------------------------
# Network decision (user, 2026-10-17 follow-up to Phase 6.2): offer
# **Ethereum mainnet and provider-supported Ethereum testnets** and exclude
# every non-Ethereum network (Base 8453, Arbitrum 42161, OP, Polygon, ...).
# Membership is validated against Sourcify's official chain list at
# implementation time — NOT copied from memory:
#   * Supported Chains doc:  https://docs.sourcify.dev/docs/chains/
#   * official chain list:   GET https://sourcify.dev/server/chains
#     (mirrored in the OpenAPI spec
#     https://sourcify.dev/server/api-docs/swagger.json)
#   * both checked 2026-10-17: chain 1, 11155111, 17000 and 560048 have
#     ``supported: true``. Goerli (5), Ropsten (3) and Rinkeby (4) are still
#     listed but ``supported: false`` (deprecated), so they are NOT offered;
#     neither are non-Ethereum networks even when Sourcify supports them.
# ``research.md`` §16.10 Q2 ("preferred first network") remains resolved to
# Ethereum mainnet, now with Ethereum testnets alongside. The UI reads this
# list from ``GET /api/deployed/config``; edit here to change it. Chain IDs
# follow EIP-155. Selection stays **unselected** until the user picks one —
# ``get_config()["default_chain_id"]`` is always ``None`` (no silent default).
ETHEREUM_CHAIN_ID = 1
ETHEREUM_SEPOLIA_CHAIN_ID = 11155111
ETHEREUM_HOLESKY_CHAIN_ID = 17000
ETHEREUM_HOODI_CHAIN_ID = 560048

SUPPORTED_CHAIN_IDS: tuple[int, ...] = (
    ETHEREUM_CHAIN_ID,
    ETHEREUM_SEPOLIA_CHAIN_ID,
    ETHEREUM_HOLESKY_CHAIN_ID,
    ETHEREUM_HOODI_CHAIN_ID,
)

CHAIN_OPTIONS: tuple[dict[str, Any], ...] = (
    {"chain_id": ETHEREUM_CHAIN_ID, "name": "Ethereum Mainnet", "is_testnet": False},
    {"chain_id": ETHEREUM_SEPOLIA_CHAIN_ID, "name": "Ethereum Sepolia Testnet", "is_testnet": True},
    {"chain_id": ETHEREUM_HOLESKY_CHAIN_ID, "name": "Ethereum Holesky Testnet", "is_testnet": True},
    {"chain_id": ETHEREUM_HOODI_CHAIN_ID, "name": "Ethereum Hoodi Testnet", "is_testnet": True},
)

PHASE = "6.3"

# Phase 6.2 implements the consent-gated, read-only provider (Sourcify), so a
# lookup is now available — but only behind an explicit user action + consent.
# Phase 6.3 adds an explicit action to analyze the *retrieved* single-file
# verified source with the existing local analyzer (still never automatic).
LOOKUP_AVAILABLE = True

CONSENT_TEXT = (
    "I consent to a single read-only lookup of this contract's publicly "
    "verified source (via Sourcify). Read-only: no wallet, key, signature, "
    "transaction, deployment, or on-chain write is involved."
)

READ_ONLY_NOTICE = (
    "Read-only. Lookup retrieves only a contract's PUBLIC verified source and "
    "basic metadata from Sourcify; it never signs, sends transactions, deploys, "
    "or modifies a contract, and it does not connect a wallet. Nothing is "
    "fetched until you click Retrieve & review with consent ticked, and the "
    "lookup itself runs no analyzer or model. A separate, explicit action can "
    "analyze a single complete Solidity source file with the local analyzer "
    "(multi-file, incomplete, and non-Solidity sources are not analyzed)."
)


def get_config() -> dict[str, Any]:
    """Return the Phase 6.x deployed-review configuration (no I/O)."""
    return {
        "phase": PHASE,
        "lookup_available": LOOKUP_AVAILABLE,
        "chains": [dict(chain) for chain in CHAIN_OPTIONS],
        "default_chain_id": None,  # selection stays unset until the user picks
        "consent_text": CONSENT_TEXT,
        "read_only_notice": READ_ONLY_NOTICE,
    }


# --------------------------------------------------------------------------
# EIP-55 address validation / normalization (offline)
# --------------------------------------------------------------------------

_HEX_40 = re.compile(r"\A[0-9a-fA-F]{40}\Z")

# Machine-readable reason codes.
REASON_OK_CHECKSUMMED = "ok_checksummed"
REASON_OK_LOWERCASE = "ok_lowercase"
REASON_OK_UPPERCASE = "ok_uppercase"
REASON_CHECKSUM_MISMATCH = "checksum_mismatch"
REASON_EMPTY = "empty"
REASON_BAD_LENGTH = "bad_length"
REASON_BAD_HEX = "bad_hex"


def eip55_checksum(address: str) -> str:
    """Return the EIP-55 mixed-case form of ``address``.

    ``address`` may be any 20-byte hex form with or without a ``0x`` prefix
    and in any case. Raises :class:`ValueError` if it is not 20 bytes of hex.
    """
    digits = _strip_and_check(address)
    lower = digits.lower()
    digest = keccak256(lower.encode("ascii")).hex()
    out: list[str] = []
    for i, ch in enumerate(lower):
        if ch in "0123456789":
            out.append(ch)
        elif int(digest[i], 16) >= 8:
            out.append(ch.upper())
        else:
            out.append(ch.lower())
    return "0x" + "".join(out)


def _strip_and_check(raw: str) -> str:
    """Return the 40 lowercase-agnostic hex digits of ``raw`` or raise."""
    text = (raw or "").strip()
    if text[:2].lower() == "0x":
        text = text[2:]
    if len(text) != 40:
        raise ValueError("not a 20-byte (40-hex-digit) EVM address")
    if not _HEX_40.match(text):
        raise ValueError("address contains non-hexadecimal characters")
    return text


def validate_address(raw: str) -> dict[str, Any]:
    """Validate/normalize an EVM address, offline. Never raises for bad input.

    Returns a JSON-friendly dict:

    * ``valid`` — the input is a syntactically valid 20-byte hex address;
    * ``reason`` — a machine code (see ``REASON_*``);
    * ``message`` — a short, human-readable explanation;
    * ``normalized`` — ``0x`` + lowercase, when ``valid``;
    * ``checksum`` — the EIP-55 form, when ``valid``;
    * ``checksum_provided`` / ``checksum_ok`` / ``is_mixed_case`` — checksum
      detail. ``checksum_ok`` is ``None`` when no mixed-case checksum was
      supplied.
    """
    text = "" if raw is None else str(raw)
    stripped = text.strip()

    if not stripped:
        return _result(text, False, REASON_EMPTY, "Enter a contract address.")

    if stripped[:2].lower() == "0x":
        body = stripped[2:]
    else:
        body = stripped

    if len(body) != 40:
        return _result(
            text,
            False,
            REASON_BAD_LENGTH,
            "Not a 20-byte EVM address: expected 40 hex characters "
            "(optionally after a 0x prefix).",
        )

    if not _HEX_40.match(body):
        return _result(
            text,
            False,
            REASON_BAD_HEX,
            "Not a valid EVM address: contains non-hexadecimal characters.",
        )

    is_mixed_case = body.lower() != body and body.upper() != body
    normalized = "0x" + body.lower()
    checksum = eip55_checksum(body)

    if is_mixed_case:
        matches = "0x" + body == checksum
        if matches:
            return _result(
                text,
                True,
                REASON_OK_CHECKSUMMED,
                "Valid address with a correct EIP-55 checksum.",
                normalized=normalized,
                checksum=checksum,
                checksum_provided=True,
                checksum_ok=True,
                is_mixed_case=True,
            )
        return _result(
            text,
            True,
            REASON_CHECKSUM_MISMATCH,
            "This is a valid 20-byte address, but its mixed-case EIP-55 "
            "checksum does not match. Re-check the address; do not assume the "
            "inferred capitalization.",
            normalized=normalized,
            checksum=checksum,
            checksum_provided=True,
            checksum_ok=False,
            is_mixed_case=True,
        )

    if body.isupper():
        return _result(
            text,
            True,
            REASON_OK_UPPERCASE,
            "Valid 20-byte address supplied in uppercase; no EIP-55 checksum "
            "is present.",
            normalized=normalized,
            checksum=checksum,
            checksum_provided=False,
            checksum_ok=None,
            is_mixed_case=False,
        )

    return _result(
        text,
        True,
        REASON_OK_LOWERCASE,
        "Valid 20-byte address supplied in lowercase; no EIP-55 checksum is "
        "present.",
        normalized=normalized,
        checksum=checksum,
        checksum_provided=False,
        checksum_ok=None,
        is_mixed_case=False,
    )


def _result(
    text: str,
    valid: bool,
    reason: str,
    message: str,
    *,
    normalized: str | None = None,
    checksum: str | None = None,
    checksum_provided: bool = False,
    checksum_ok: bool | None = None,
    is_mixed_case: bool = False,
) -> dict[str, Any]:
    return {
        "input": text,
        "valid": valid,
        "reason": reason,
        "message": message,
        "normalized": normalized,
        "checksum": checksum,
        "checksum_provided": checksum_provided,
        "checksum_ok": checksum_ok,
        "is_mixed_case": is_mixed_case,
    }


# --------------------------------------------------------------------------
# Provider seam — the real, consent-gated provider lives in
# `backend.deployed_source.py`; the offline stub below is kept as a
# deterministic, network-free default for tests and disabled-lookup paths.
# --------------------------------------------------------------------------


class LookupNotAvailable(RuntimeError):
    """Raised when a source lookup is attempted while it is unavailable."""


@runtime_checkable
class SourceProvider(Protocol):
    """Read-only seam for address -> public source resolution.

    Implementations must be read-only and must only be called behind the
    user's explicit, per-use consent. The real implementation is
    :class:`backend.deployed_source.SourcifyProvider`; the offline stub below
    marks the seam and is used where no network is wanted.
    """

    name: str

    async def resolve(self, chain_id: int, address: str) -> dict[str, Any]:
        ...  # pragma: no cover - protocol only


class OfflineStubProvider:
    """Deterministic, network-free placeholder used to mark the seam.

    It performs no I/O: any call raises :class:`LookupNotAvailable`. The real,
    consent-gated provider is :class:`backend.deployed_source.SourcifyProvider`;
    this stub remains the offline default for tests.
    """

    name = "offline-stub"

    async def resolve(self, chain_id: int, address: str) -> dict[str, Any]:
        raise LookupNotAvailable(
            "This is the offline stub provider — no network request was made. "
            "The real read-only lookup is backend.deployed_source.SourcifyProvider."
        )


__all__ = [
    "CHAIN_OPTIONS",
    "CONSENT_TEXT",
    "ETHEREUM_CHAIN_ID",
    "ETHEREUM_HOLESKY_CHAIN_ID",
    "ETHEREUM_HOODI_CHAIN_ID",
    "ETHEREUM_SEPOLIA_CHAIN_ID",
    "LOOKUP_AVAILABLE",
    "PHASE",
    "READ_ONLY_NOTICE",
    "REASON_BAD_HEX",
    "REASON_BAD_LENGTH",
    "REASON_CHECKSUM_MISMATCH",
    "REASON_EMPTY",
    "REASON_OK_CHECKSUMMED",
    "REASON_OK_LOWERCASE",
    "REASON_OK_UPPERCASE",
    "SUPPORTED_CHAIN_IDS",
    "LookupNotAvailable",
    "OfflineStubProvider",
    "SourceProvider",
    "eip55_checksum",
    "get_config",
    "keccak256",
    "validate_address",
]
