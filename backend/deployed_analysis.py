"""Phase 6.3 — analyze the *retrieved* verified source with the existing pipeline.

This module closes the Phase 6 MVP: after Phase 6.2 retrieves a deployed
contract's **publicly verified source** (read-only, consent-gated), the user may
explicitly run the **existing** local analyzer on that retrieved source and have
local Qwen **explain** the findings — exactly as pasted source is treated.

Hard constraints (``plan.md`` Phase 6.3):

* **No auto-run, ever.** Nothing here runs from a lookup completing, an address
  change, a network change, or a result being loaded. The route that calls
  :func:`analyze_retrieved_source` is only reached from an explicit user click
  (the deployed panel's "Analyze" button), mirroring the pasted-code analyzer
  buttons. This module never runs the model.
* **Association only with the current verified lookup.** The caller must resend
  the *exact* sources returned by the lookup plus its ``address`` and
  ``chain_id``. The address/chain are re-validated offline here, so an analysis
  can only ever be produced for a supported Ethereum network and a syntactically
  valid address.
* **Never imply unverified/incomplete scope was analyzed.** Analysis is refused
  (with a plain reason) when there is no retrieved source, the source was
  truncated by the size cap (incomplete), or the verified source is not a single
  Solidity file. In particular, a **multi-file** verified contract is *not*
  analyzed in this phase and is never presented as a partial run: the existing
  ``slither_adapter`` analyzes exactly one Solidity source unit (it writes one
  file and compiles it), so it cannot safely compile a multi-file project.
  Multi-file analysis is marked **unsupported in this phase** instead of
  silently analyzing one file. A single-file analysis proceeds **only** when the
  verified source truly contains exactly one file.
* **Deterministic findings only.** The analyzer responses are returned unchanged
  (the same normalized ``SlitherResponse`` / ``SolhintResponse`` as a paste).
  ``kind:"tool"`` findings are authoritative; Qwen is never invoked here and can
  never create or alter a finding, its severity, confidence, or location. Qwen
  explanation stays a separate, clearly-labelled surface in the frontend.

This module adds **no** analyzer, **no** dependency, and **no** proxy/UUPS,
drafting, wallet, signing, transaction, deployment, or contract-modification
capability. It performs **no** outbound I/O of its own.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import deployed, slither_adapter, solhint_adapter
from .schemas import SlitherResponse, SolhintResponse, ToolRunSummary

# Machine-readable refusal codes (set only when ``allowed`` is False).
REASON_INVALID_ADDRESS = "invalid_address"
REASON_UNSUPPORTED_CHAIN = "unsupported_chain"
REASON_UNSUPPORTED_LANGUAGE = "unsupported_language"
REASON_NO_VERIFIED_SOURCE = "no_verified_source"
REASON_INCOMPLETE_SOURCE = "incomplete_source"
REASON_MULTI_FILE_UNSUPPORTED = "multi_file_unsupported"
REASON_EMPTY_SOURCE = "empty_source"

REASON_CODES = (
    REASON_INVALID_ADDRESS,
    REASON_UNSUPPORTED_CHAIN,
    REASON_UNSUPPORTED_LANGUAGE,
    REASON_NO_VERIFIED_SOURCE,
    REASON_INCOMPLETE_SOURCE,
    REASON_MULTI_FILE_UNSUPPORTED,
    REASON_EMPTY_SOURCE,
)

#: The single language the local analyzer can process. Sourcify also verifies
#: Vyper contracts, which Slither (Solidity-only) cannot analyze.
_SUPPORTED_LANGUAGE = "solidity"

#: Short capability statement shown/handed back so the user always sees what the
#: local analyzers can and cannot do on retrieved source.
CAPABILITY_NOTE = (
    "The local analyzers run on a single Solidity source unit exactly as pasted "
    "source: Slither (0.11.6) via the local solc for symbolic analysis, and "
    "Solhint (6.2.5) for linting. They cannot compile a multi-file project or a "
    "non-Solidity contract, so those are not analyzed here."
)


def multi_file_reason(file_count: int) -> str:
    """Plain, honest refusal for a multi-file verified contract.

    Deliberately does **not** offer to analyze "just one" file, because that
    would present a partial run without full-contract coverage.
    """
    n = file_count if file_count > 0 else 2
    return (
        f"This verified contract has {n} source files. The existing local "
        "analyzer analyzes a single Solidity source file and cannot safely "
        "compile a multi-file project, so multi-file analysis is unsupported in "
        "this phase and was NOT run. No file was analyzed and no partial result "
        "is presented. (Nothing was fabricated.)"
    )


def incomplete_reason() -> str:
    return (
        "The retrieved source is incomplete (it was truncated by the size cap), "
        "so it was NOT analyzed. Re-run the lookup only if the provider returns "
        "the full source."
    )


def _first_source(sources: list[Any]) -> Any | None:
    """Return the sole source file dict when there is exactly one, else ``None``."""
    if not isinstance(sources, list) or len(sources) != 1:
        return None
    item = sources[0]
    if isinstance(item, dict):
        return item
    # Tolerate a pydantic model (DeployedSourceFile) from the route layer.
    return {"path": getattr(item, "path", ""), "content": getattr(item, "content", "")}


def _content_of(source: Any) -> str:
    if isinstance(source, dict):
        content = source.get("content")
    else:
        content = getattr(source, "content", None)
    return content if isinstance(content, str) else ""


def _path_of(source: Any) -> str:
    if isinstance(source, dict):
        path = source.get("path")
    else:
        path = getattr(source, "path", None)
    return str(path) if path else "Retrieved.sol"


async def analyze_retrieved_source(
    address: str,
    chain_id: int | None,
    sources: list[Any],
    *,
    sources_truncated: bool = False,
    language: str | None = None,
    runner: Callable[[str], Awaitable[SlitherResponse]] | None = None,
    solhint_runner: Callable[[str], Awaitable[SolhintResponse]] | None = None,
) -> dict[str, Any]:
    """Gate + run the existing analyzers on the *current* retrieved verified source.

    Returns a JSON-friendly enrichment envelope. ``allowed`` is True only when
    the address/chain are valid, the language is Solidity, the source is a single
    non-empty (non-truncated) file; otherwise it is False with a plain ``reason``
    and **no analyzer is called**. When allowed, ``analyzer`` holds the unchanged
    :class:`SlitherResponse` and ``solhint`` the unchanged
    :class:`SolhintResponse` (the same shapes as pasted source), each attributed
    to its own tool and never merged.

    ``runner`` / ``solhint_runner`` are injection seams for tests; production
    uses :func:`backend.slither_adapter.run_slither` and
    :func:`backend.solhint_adapter.run_solhint`.
    """
    validation = deployed.validate_address(address)
    file_count = len(sources) if isinstance(sources, list) else 0

    result: dict[str, Any] = {
        "allowed": False,
        "reason_code": None,
        "reason": None,
        "address": validation.get("normalized") or (address or "").strip(),
        "checksum": validation.get("checksum"),
        "chain_id": chain_id,
        "chain_name": _chain_name(chain_id),
        "source_file_count": file_count,
        "source_path": None,
        "language": language,
        "limitation": CAPABILITY_NOTE,
        "analyzer": None,
        "solhint": None,
    }

    # 1) Address must be a valid 20-byte EVM address (offline).
    if not validation["valid"]:
        result["reason_code"] = REASON_INVALID_ADDRESS
        result["reason"] = validation["message"]
        return result

    # 2) Supported Ethereum networks only (mainnet + Sourcify-supported testnets).
    if chain_id not in deployed.SUPPORTED_CHAIN_IDS:
        result["reason_code"] = REASON_UNSUPPORTED_CHAIN
        result["reason"] = (
            "Select a supported Ethereum network before analyzing: "
            f"{_supported_networks_text()}. Non-Ethereum networks are not available."
        )
        return result

    # 3) Solidity only (Sourcify also verifies Vyper, which Slither cannot read).
    if language and str(language).strip().lower() != _SUPPORTED_LANGUAGE:
        result["reason_code"] = REASON_UNSUPPORTED_LANGUAGE
        result["reason"] = (
            f"The retrieved source is {language}, not Solidity. The local "
            "analyzer is Solidity-only, so it was NOT analyzed."
        )
        return result

    # 4) There must be verified source to analyze.
    if file_count == 0:
        result["reason_code"] = REASON_NO_VERIFIED_SOURCE
        result["reason"] = (
            "No verified source is loaded for this contract, so there is nothing "
            "to analyze. Retrieve a verified contract's source first."
        )
        return result

    # 5) Incomplete (truncated) source is never analyzed — that would imply the
    #    whole file was checked when part of it is missing.
    if sources_truncated:
        result["reason_code"] = REASON_INCOMPLETE_SOURCE
        result["reason"] = incomplete_reason()
        return result

    # 6) Multi-file verified source is unsupported in this phase (the existing
    #    adapter analyzes one source unit); do NOT silently analyze one file.
    if file_count != 1:
        result["reason_code"] = REASON_MULTI_FILE_UNSUPPORTED
        result["reason"] = multi_file_reason(file_count)
        return result

    single = _first_source(sources)
    content = _content_of(single)
    path = _path_of(single)
    result["source_path"] = path
    result["source_file_count"] = 1

    # 7) A single but empty file has nothing to analyze.
    if not content.strip():
        result["reason_code"] = REASON_EMPTY_SOURCE
        result["reason"] = (
            "The single verified source file is empty, so there is nothing to "
            "analyze. Nothing was run."
        )
        return result

    # 8) Only now run the existing analyzers — the same calls as the pasted-code
    #    path, each on the same single retrieved source, and each attributed to
    #    its own tool (never merged).
    run = runner or slither_adapter.run_slither
    analyzer = await run(content)
    result["allowed"] = True
    result["analyzer"] = analyzer

    analyzers: list[Any] = [
        ToolRunSummary(
            tool=analyzer.tool,
            tool_version=analyzer.tool_version,
            status=analyzer.status,
            summary=analyzer.summary,
            finding_count=analyzer.finding_count,
            findings=analyzer.findings,
            duration_ms=analyzer.duration_ms,
            detail=analyzer.detail,
            raw_output=analyzer.raw_output,
            raw_output_truncated=analyzer.raw_output_truncated,
        )
    ]

    # Solhint runs alongside Slither on the same source, kept separate.
    try:
        run_sol = solhint_runner or solhint_adapter.run_solhint
        sol_resp = await run_sol(content)
        result["solhint"] = sol_resp  # separate, attributed run (never merged into `analyzer`)
        analyzers.append(
            ToolRunSummary(
                tool=sol_resp.tool,
                tool_version=sol_resp.tool_version,
                status=sol_resp.status,
                summary=sol_resp.summary,
                finding_count=sol_resp.finding_count,
                findings=sol_resp.findings,
                duration_ms=sol_resp.duration_ms,
                detail=sol_resp.detail,
                raw_output=sol_resp.raw_output,
                raw_output_truncated=sol_resp.raw_output_truncated,
            )
        )
    except Exception:
        pass

    result["analyzers"] = analyzers
    return result


def _chain_name(chain_id: int | None) -> str | None:
    for chain in deployed.CHAIN_OPTIONS:
        if chain["chain_id"] == chain_id:
            return str(chain["name"])
    return None


def _supported_networks_text() -> str:
    return "; ".join(
        f"{chain['name']} (chain ID {chain['chain_id']})"
        for chain in deployed.CHAIN_OPTIONS
    )


__all__ = [
    "CAPABILITY_NOTE",
    "REASON_CODES",
    "REASON_EMPTY_SOURCE",
    "REASON_INCOMPLETE_SOURCE",
    "REASON_INVALID_ADDRESS",
    "REASON_MULTI_FILE_UNSUPPORTED",
    "REASON_NO_VERIFIED_SOURCE",
    "REASON_UNSUPPORTED_CHAIN",
    "REASON_UNSUPPORTED_LANGUAGE",
    "analyze_retrieved_source",
    "incomplete_reason",
    "multi_file_reason",
]
