"""Local Solhint analyzer adapter (added 2026-10-17).

Runs the already-installed local **Solhint 6.2.5** (isolated install under
``tools\\solhint\\``, installed with a local npm prefix) against a *copy* of the
submitted Solidity source. Solhint is a pure-JavaScript Solidity **linter**, so
it is launched directly as ``node <solhint.js> <file>`` — there is no wrapper
script and no shell:

    <node.exe> tools\\solhint\\node_modules\\solhint\\solhint.js Submitted.sol ^
        --formatter json --config <bundled solhint-recommended ruleset> ^
        --disc --noPoster

Safety properties (mirrors ``backend/slither_adapter.py``; enforced here, not in
the browser):

- Exactly one executable (the Node runtime) is invoked by absolute path with a
  **fixed argv template**. Nothing from the source, comments, model output, or
  the request is ever passed as an argument or executed.
- The submitted source is written to a fresh temporary file inside a fresh
  isolated temp directory; user-supplied filesystem paths are never analyzed.
- Temp files live under ``settings.work_dir`` (outside ``tools/`` and the app
  source tree) and are always removed, including on failure/timeout.
- Bounded wall-clock time (child is killed on expiry) and bounded output size.
- No network, RPC, or compiler access. ``--disc`` disables Solhint's own
  update check, so a run never makes an outbound request; ``--noPoster`` hides
  its terminal banner. The ruleset is passed explicitly (``--config``) so a run
  never picks up a config file from the working directory.

Solhint is a *linter*: its findings are deterministic but shallow compared to a
symbolic analyzer like Slither, and a clean run is **not** a statement that the
contract is safe. Findings are always attributed to ``tool="solhint"`` and are
never merged into another tool's findings.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from .config import settings
from .schemas import (
    SolhintResponse,
    ToolFinding,
    ToolInfo,
    ToolLocation,
)
# Import from the same module to avoid circular import; function names match
from . import slither_adapter

SUBMITTED_FILENAME = slither_adapter.SUBMITTED_FILENAME
_execute = slither_adapter._execute  # type: ignore[attr-defined]
_first_nonempty_line = slither_adapter._first_nonempty_line
_probe_version = slither_adapter._probe_version
_short_title = slither_adapter._short_title
_sanitize_output = slither_adapter.sanitize_output

TOOL_NAME = "solhint"
_JSON_OUTPUT = "stdout"

# Solhint's own severity strings are only "Error" / "Warning". They are mapped
# to the app's coarse severity scale for display; the ORIGINAL string is kept
# verbatim in `ToolFinding.impact` and the mapping is never presented as a
# security judgement. Solhint reports no confidence, recorded as "n/a".
_SEVERITY_MAP = {
    "error": "medium",
    "warning": "low",
}
_SOLHINT_CONFIDENCE = "n/a"
_RULES_DOCS = "https://protofire.github.io/solhint/docs/rules.html"

# Markers that mean the *submitted source* could not be parsed (Solhint exits
# non-zero with a stack trace instead of a JSON report for an unparsable file).
_PARSE_ERROR_MARKERS = (
    "cannot read properties of null",
    "unexpected token",
    "parse error",
    "parsererror",
    "typeerror",
)

_TOOL_NOTICE = (
    "Solhint is a deterministic Solidity linter. Its findings are partial and "
    "may miss issues; a run with no findings does NOT mean the contract is "
    "safe. This is not a complete security audit."
)


# --------------------------------------------------------------------------
# Version / availability probing
# --------------------------------------------------------------------------


def solhint_argv_static() -> list[str]:
    """The fixed, non-source part of the Solhint argv (exe + flags + ruleset)."""
    return [
        str(settings.node_exe),
        str(settings.solhint_js),
        "-f",
        "json",
        "--config",
        str(settings.solhint_config),
        "--disc",
        "--noPoster",
    ]


def _tool_paths() -> list[str]:
    """Local install directories that must never appear in tool output."""
    paths: list[str] = []
    for value in (settings.node_exe, settings.solhint_js, settings.solhint_config):
        try:
            parent = str(Path(value).resolve().parent)
        except Exception:  # pragma: no cover - defensive
            continue
        if parent and parent not in paths:
            paths.append(parent)
    return paths


def sanitize_output(text: str, work_dir: str | None = None) -> tuple[str, bool]:
    """Redact Solhint/Node install paths, then reuse the shared sanitizer.

    Returns (sanitized_text, truncated). Never returns absolute local paths.
    """
    if not text:
        return "", False
    for path in _tool_paths():
        text = text.replace(path, "<tools>")
        text = text.replace(path.replace("\\", "/"), "<tools>")
    return _sanitize_output(text, work_dir)


def get_tool_info(name: str = TOOL_NAME) -> ToolInfo:
    """Return availability/version for Solhint (no network).

    Defaults to Solhint so it can be called either as ``get_tool_info()`` (the
    shape the health endpoint uses for Slither) or with an explicit name.
    """
    if name != TOOL_NAME:
        raise ValueError(f"unknown tool: {name}")
    missing = [
        label
        for label, value in (
            ("node runtime", settings.node_exe),
            ("solhint\\node_modules\\solhint\\solhint.js", settings.solhint_js),
            ("solhint-recommended ruleset", settings.solhint_config),
        )
        if not Path(value).exists()
    ]
    if missing:
        return ToolInfo(
            name=name,
            status="unavailable",
            available=False,
            detail="Solhint is not available locally (missing "
            + ", ".join(missing)
            + " under tools\\solhint).",
        )
    version = _probe_version(
        str(settings.node_exe),
        [str(settings.solhint_js), "--version"],
        _first_nonempty_line,
    )
    return ToolInfo(
        name=name,
        status="available",
        available=True,
        version=version,
    )


# --------------------------------------------------------------------------
# JSON normalization
# --------------------------------------------------------------------------


def _safe_file_label(filename: str | None) -> str:
    """Reduce any tool-reported filename to a neutral basename."""
    if not filename:
        return SUBMITTED_FILENAME
    name = str(filename).replace("\\", "/").split("/")[-1]
    return name or SUBMITTED_FILENAME


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_problem(problem: dict, tool_version: str | None) -> ToolFinding:
    """Convert one Solhint JSON problem into a normalized ToolFinding."""
    rule = str(problem.get("ruleId") or "unknown")
    raw_severity = str(problem.get("severity") or "Warning")
    severity = _SEVERITY_MAP.get(raw_severity.strip().lower(), "unknown")
    message = str(problem.get("message") or "").strip()
    line = _int_or_none(problem.get("line"))
    column = _int_or_none(problem.get("column"))
    file_label = _safe_file_label(problem.get("filePath") or problem.get("filename"))

    return ToolFinding(
        id=f"{rule}:{line if line is not None else '?'}:{column if column is not None else '?'}",
        kind="tool",
        status="confirmed",
        tool=TOOL_NAME,
        tool_version=tool_version,
        check=rule,
        severity=severity,  # type: ignore[arg-type]
        impact=raw_severity,
        confidence=_SOLHINT_CONFIDENCE,
        title=_short_title(message, rule),
        description=message or _short_title(message, rule),
        reference=_RULES_DOCS,
        file=file_label,
        start_line=line,
        end_line=line,
        locations=[
            ToolLocation(
                file=file_label,
                start_line=line,
                end_line=line,
                start_column=column,
                end_column=column,
            )
        ],
        evidence=None,
    )


def _report_parse_error_message(data) -> str | None:
    """Return Solhint's own parse-error text when the report IS a parse failure.

    For an unparsable Solidity file Solhint still prints a JSON **array**, but
    the entry carries no ``ruleId`` (only a ``message`` such as
    ``Parse error: ...``) followed by the trailing ``conclusion`` object. Such a
    report means *nothing was linted*, so it must never be shown as
    ``no_findings`` (which would imply a clean run).
    """
    if not isinstance(data, list):
        return None
    for item in data:
        if isinstance(item, dict) and item.get("ruleId") is None:
            message = str(item.get("message") or "").strip()
            if message:
                return message
    return None


def parse_solhint_json(data, tool_version: str | None) -> list[ToolFinding]:
    """Normalize Solhint's JSON report (an array) into findings.

    Solhint prints a JSON **array** of problem objects and appends a trailing
    ``{"conclusion": "..."}`` element. Entries without a ``ruleId`` are the
    conclusion/metadata and are ignored rather than turned into findings.

    A report that consists *only* of such entries is a parse failure rather than
    a clean run; the caller detects that with :func:`_report_parse_error_message`
    before it is ever reported as ``no_findings``.

    Raises ValueError if the document is not a Solhint problem array.
    """
    if not isinstance(data, list):
        raise ValueError("Solhint JSON is not an array")
    problems = [
        item
        for item in data
        if isinstance(item, dict) and item.get("ruleId") is not None
    ]
    return [normalize_problem(p, tool_version) for p in problems]


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------


def _make_response(
    *,
    status: str,
    summary: str,
    source_name: str,
    started: float,
    version: str | None,
    findings: list[ToolFinding] | None = None,
    detail: str | None = None,
    raw_output: str | None = None,
    raw_truncated: bool = False,
) -> SolhintResponse:
    findings = findings or []
    return SolhintResponse(
        tool=TOOL_NAME,
        tool_version=version,
        status=status,  # type: ignore[arg-type]
        summary=summary,
        finding_count=len(findings),
        findings=findings,
        source_name=source_name,
        duration_ms=int((time.monotonic() - started) * 1000),
        notice=_TOOL_NOTICE,
        detail=detail,
        raw_output=raw_output,
        raw_output_truncated=raw_truncated,
    )


def _looks_like_parse_error(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _PARSE_ERROR_MARKERS)


async def run_solhint(source: str) -> SolhintResponse:
    """Lint `source` with the local Solhint and return a normalized report.

    Always returns a structured response; never raises for tool-level failure
    (unavailable, unparsable source, timeout, bad JSON).
    """
    started = time.monotonic()
    source_name = SUBMITTED_FILENAME
    node_exe = Path(settings.node_exe)
    solhint_js = Path(settings.solhint_js)
    ruleset = Path(settings.solhint_config)
    version = None
    if node_exe.exists() and solhint_js.exists():
        version = _probe_version(
            str(node_exe), [str(solhint_js), "--version"], _first_nonempty_line
        )

    if not node_exe.exists():
        return _make_response(
            status="unavailable",
            summary="Solhint is not available locally (Node runtime not found), so no analysis was run.",
            source_name=source_name,
            started=started,
            version=version,
            detail="Node runtime missing; Solhint is launched with the local Node.",
        )
    if not solhint_js.exists():
        return _make_response(
            status="unavailable",
            summary="Solhint is not available locally, so no analysis was run.",
            source_name=source_name,
            started=started,
            version=version,
            detail="Solhint executable missing under tools\\solhint.",
        )
    if not ruleset.exists():
        return _make_response(
            status="unavailable",
            summary="Solhint's ruleset is missing, so no analysis was run.",
            source_name=source_name,
            started=started,
            version=version,
            detail="The bundled solhint-recommended ruleset was not found.",
        )

    work_root = Path(settings.work_dir)
    try:
        work_root.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # pragma: no cover - filesystem edge
        return _make_response(
            status="error",
            summary="Could not create a temporary working directory.",
            source_name=source_name,
            started=started,
            version=version,
            detail=sanitize_output(str(exc))[0],
        )

    tmp = Path(tempfile.mkdtemp(prefix="solhint-", dir=str(work_root)))
    try:
        src_path = tmp / SUBMITTED_FILENAME
        # Normalize line endings to LF and write byte-exactly so the linter's
        # line/column numbers match the source the user actually submitted.
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        src_path.write_bytes(normalized.encode("utf-8"))

        # Fixed argv template: local absolute paths + fixed flags only.
        argv = [*solhint_argv_static(), SUBMITTED_FILENAME]

        result = await _execute(
            argv,
            cwd=tmp,
            timeout=float(settings.analyzer_timeout),
            limit=int(settings.max_output_bytes),
        )

        combined = (
            result["stdout"].decode(errors="replace")
            + "\n"
            + result["stderr"].decode(errors="replace")
        )
        raw_output, raw_truncated = sanitize_output(combined, str(tmp))
        raw_truncated = raw_truncated or bool(result["truncated"])

        if result["timed_out"]:
            return _make_response(
                status="timeout",
                summary=(
                    f"Solhint exceeded the {settings.analyzer_timeout:g}s time limit "
                    "and was stopped. No findings are reported for this run."
                ),
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        stdout_text = result["stdout"].decode(errors="replace").strip()
        data = None
        if stdout_text:
            try:
                data = json.loads(stdout_text)
            except Exception:
                data = None

        if data is None:
            # No parseable JSON report: Solhint crashed. For an unparsable
            # Solidity file it exits non-zero with a stack trace instead of a
            # report; distinguish that from an unexpected tool error.
            if result["returncode"] not in (0, 1) and _looks_like_parse_error(combined):
                return _make_response(
                    status="compiler_error",
                    summary=(
                        "Solhint could not parse the submitted source (invalid "
                        "Solidity). No findings are reported for this run."
                    ),
                    source_name=source_name,
                    started=started,
                    version=version,
                    detail="Solhint reported a parser error for this source.",
                    raw_output=raw_output,
                    raw_truncated=raw_truncated,
                )
            return _make_response(
                status="error",
                summary="Solhint did not produce a usable report for this run.",
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        try:
            parse_error = _report_parse_error_message(data)
            if parse_error:
                return _make_response(
                    status="compiler_error",
                    summary=(
                        "Solhint could not parse the submitted source (invalid "
                        "Solidity), so nothing was linted. No findings are "
                        "reported for this run."
                    ),
                    source_name=source_name,
                    started=started,
                    version=version,
                    detail=sanitize_output(parse_error)[0],
                    raw_output=raw_output,
                    raw_truncated=raw_truncated,
                )
            findings = parse_solhint_json(data, version)
        except ValueError as exc:
            return _make_response(
                status="error",
                summary="Solhint produced a report that could not be parsed.",
                source_name=source_name,
                started=started,
                version=version,
                detail=sanitize_output(str(exc))[0],
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        if not findings:
            return _make_response(
                status="no_findings",
                summary=(
                    "Solhint reported no findings for this run. A clean run does "
                    "not mean the contract is safe."
                ),
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        return _make_response(
            status="ok",
            summary=f"Solhint reported {len(findings)} finding(s) for this run.",
            source_name=source_name,
            started=started,
            version=version,
            findings=findings,
            raw_output=raw_output,
            raw_truncated=raw_truncated,
        )
    except Exception as exc:  # pragma: no cover - unexpected failure path
        return _make_response(
            status="error",
            summary="Solhint run failed unexpectedly.",
            source_name=source_name,
            started=started,
            version=version,
            detail=sanitize_output(str(exc))[0],
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = [
    "TOOL_NAME",
    "run_solhint",
    "parse_solhint_json",
    "normalize_problem",
    "sanitize_output",
    "solhint_argv_static",
    "get_tool_info",
    "_report_parse_error_message",
]
