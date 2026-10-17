"""Local Slither analyzer adapter (Phase 3).

Runs the already-installed local **Slither 0.11.6** (canonical install:
``tools\\slither\\venv\\Scripts\\slither.exe``) against a *copy* of the
submitted Solidity source, using the explicit local **solc 0.8.37** compiler
(``tools\\solc\\solc.exe``). The machine-readable JSON output is normalized
into the app's report format.

Safety properties (enforced here, not in the browser):

- Exactly one executable is invoked, by absolute path, with a **fixed argv
  template**. Nothing from the source, comments, model output, or the request
  is ever passed as an argument or executed.
- The submitted source is written to a fresh temporary file inside a fresh
  isolated temp directory; user-supplied filesystem paths are never analyzed.
- Temp files live under ``settings.work_dir`` (outside ``tools/`` and the app
  source tree) and are always removed, including on failure/timeout.
- Bounded wall-clock time (child is killed on expiry) and bounded output size.
- No network, RPC, compiler download, wallet, or key access. ``solc`` is
  passed explicitly so no compiler is selected or downloaded at run time.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .config import settings
from .schemas import SlitherResponse, ToolFinding, ToolInfo, ToolLocation

TOOL_NAME = "slither"
SUBMITTED_FILENAME = "Submitted.sol"
_JSON_NAME = "slither-out.json"

# Known non-integrated tools. Slither + solc are integrated here; Solhint is
# integrated in its own adapter (backend/solhint_adapter.py) and is surfaced via
# the health endpoint, but deliberately NOT added to `get_tools_info()` so the
# read-only MCP surface is unchanged.
_NOT_INTEGRATED = ("mythril", "echidna", "aderyn", "foundry")

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Hard ceiling for reading Slither's JSON report (a run that produces more than
# this is treated as a failure rather than parsed).
_MAX_JSON_BYTES = 8_000_000

# slither --version / solc --version results, cached per executable path.
_VERSION_CACHE: dict[str, str | None] = {}

# Slither impact -> normalized severity.
_IMPACT_TO_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    "informational": "informational",
    "info": "informational",
}

_ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|]+")
_POSIX_ABS_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:usr|home|tmp|etc|var|opt|bin|lib|root|mnt|media)(?:/[^\s\"'<>|]+)"
)


# --------------------------------------------------------------------------
# Version / availability probing
# --------------------------------------------------------------------------


def _first_nonempty_line(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _parse_solc_version(text: str) -> str | None:
    m = re.search(r"Version:\s*([^\s]+)", text)
    return m.group(1) if m else _first_nonempty_line(text)


def _probe_version(exe: str, args: list[str], parser) -> str | None:
    key = f"{exe}|{args}"
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    version: str | None = None
    try:
        proc = subprocess.run(  # noqa: S603 - fixed exe + fixed args, no shell
            [exe, *args],
            capture_output=True,
            timeout=10,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
        text = (proc.stdout or b"").decode(errors="replace")
        text += (proc.stderr or b"").decode(errors="replace")
        version = parser(text)
    except Exception:
        version = None
    _VERSION_CACHE[key] = version
    return version


def get_tool_info(name: str | None = None) -> ToolInfo | dict:
    """Return availability/version for a single known tool (no network)."""
    if name is None or name == TOOL_NAME:
        exe = settings.slither_exe
        if Path(exe).exists():
            return ToolInfo(
                name=TOOL_NAME,
                status="available",
                available=True,
                version=_probe_version(exe, ["--version"], _first_nonempty_line),
            )
        return ToolInfo(
            name=TOOL_NAME,
            status="unavailable",
            available=False,
            detail="Slither executable not found in tools\\slither\\venv\\Scripts.",
        )
    if name == "solc":
        exe = settings.solc_exe
        if Path(exe).exists():
            return ToolInfo(
                name=name,
                status="available",
                available=True,
                version=_probe_version(exe, ["--version"], _parse_solc_version),
            )
        return ToolInfo(
            name=name,
            status="unavailable",
            available=False,
            detail="solc executable not found in tools\\solc.",
        )
    if name == "solhint":
        # Solhint is integrated (local Node linter). Delegated to its adapter,
        # which already returns the same ToolInfo shape. NOTE: `get_tools_info()`
        # deliberately does NOT include Solhint, so the read-only MCP surface
        # (which consumes it) is unchanged.
        try:
            from . import solhint_adapter

            return solhint_adapter.get_tool_info("solhint")
        except Exception:
            pass
    return ToolInfo(
        name=name or "unknown",
        status="not_integrated",
        available=False,
        detail="Not integrated in this phase.",
    )


def get_tools_info() -> list[ToolInfo]:
    """All known tools: slither + solc real status, others not_integrated."""
    return [get_tool_info(TOOL_NAME), get_tool_info("solc")] + [
        get_tool_info(n) for n in _NOT_INTEGRATED
    ]


def health_tools() -> tuple[dict[str, str], list[ToolInfo]]:
    """String map (for back-compat) plus structured tool details."""
    infos = get_tools_info()
    return {i.name: i.status for i in infos}, infos


# --------------------------------------------------------------------------
# Output sanitization
# --------------------------------------------------------------------------


def sanitize_output(text: str, work_dir: str | None = None) -> tuple[str, bool]:
    """Redact local paths from tool output and cap its length.

    Returns (sanitized_text, truncated). Never returns absolute local paths
    or the tool install locations verbatim.
    """
    if not text:
        return "", False

    # Redact the (per-run) temp working directory first.
    if work_dir:
        text = text.replace(work_dir, "<work>")
        text = text.replace(work_dir.replace("\\", "/"), "<work>")

    # Redact the tool install directories (venv + solc folder).
    for exe in (settings.slither_exe, settings.solc_exe):
        try:
            parent = str(Path(exe).resolve().parent)
        except Exception:
            continue
        if parent:
            text = text.replace(parent, "<tools>")
            text = text.replace(parent.replace("\\", "/"), "<tools>")

    # Any remaining absolute paths (tracebacks, compiler invocations, ...).
    text = _ABS_PATH_RE.sub("<path>", text)
    text = _POSIX_ABS_RE.sub("<path>", text)

    cap = int(settings.max_output_bytes)
    truncated = len(text) > cap
    return text[:cap], truncated


# --------------------------------------------------------------------------
# Bounded subprocess execution
# --------------------------------------------------------------------------


async def _drain(reader: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    """Read a stream up to `limit` bytes; keep draining to avoid blocking."""
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while True:
        chunk = await reader.read(65536)
        if not chunk:
            break
        remaining = limit - total
        if remaining > 0:
            take = chunk[:remaining]
            chunks.append(take)
            total += len(take)
            if len(take) < len(chunk):
                truncated = True
        else:
            truncated = True
    return b"".join(chunks), truncated


async def _execute(
    argv: list[str], cwd: Path, timeout: float, limit: int
) -> dict:
    """Run `argv` (no shell) with a wall-clock timeout and bounded output."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
    )
    try:
        (out, out_t), (err, err_t) = await asyncio.wait_for(
            asyncio.gather(_drain(proc.stdout, limit), _drain(proc.stderr, limit)),
            timeout=timeout,
        )
        await proc.wait()
        return {
            "stdout": out,
            "stderr": err,
            "truncated": out_t or err_t,
            "timed_out": False,
            "returncode": proc.returncode,
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except Exception:
            pass
        return {
            "stdout": b"",
            "stderr": b"",
            "truncated": False,
            "timed_out": True,
            "returncode": None,
        }


# --------------------------------------------------------------------------
# JSON normalization
# --------------------------------------------------------------------------


def _safe_file_label(filename: str | None) -> str:
    """Reduce any tool-reported filename to a neutral basename."""
    if not filename:
        return SUBMITTED_FILENAME
    name = str(filename).replace("\\", "/").split("/")[-1]
    return name or SUBMITTED_FILENAME


def _lines_span(source_mapping: dict) -> tuple[int | None, int | None]:
    lines = source_mapping.get("lines")
    if isinstance(lines, list) and lines:
        nums = [int(x) for x in lines if isinstance(x, int)]
        if nums:
            return min(nums), max(nums)
    return None, None


def _location_from_element(element: dict) -> ToolLocation:
    sm = element.get("source_mapping") or {}
    start_line, end_line = _lines_span(sm)
    return ToolLocation(
        file=_safe_file_label(
            sm.get("filename_relative") or sm.get("filename_short")
        ),
        start_line=start_line,
        end_line=end_line,
        start_column=sm.get("starting_column"),
        end_column=sm.get("ending_column"),
    )


def _short_title(description: str, check: str) -> str:
    text = (description or "").strip()
    if not text:
        return check
    # First line/sentence, bounded.
    first = re.split(r"[\r\n]", text, maxsplit=1)[0].strip()
    if len(first) > 160:
        first = first[:157] + "..."
    return first or check


def _safe_reference(ref) -> str | None:
    if isinstance(ref, str) and ref.startswith(("http://", "https://")):
        return ref
    return None


def normalize_detector(detector: dict, tool_version: str | None) -> ToolFinding:
    """Convert one Slither detector dict into a normalized ToolFinding."""
    check = str(detector.get("check") or "unknown")
    impact = str(detector.get("impact") or "Unknown")
    confidence = str(detector.get("confidence") or "Unknown")
    severity = _IMPACT_TO_SEVERITY.get(impact.strip().lower(), "unknown")
    description = str(detector.get("description") or "").strip()

    elements = detector.get("elements") or []
    locations = [_location_from_element(e) for e in elements if isinstance(e, dict)]

    if locations:
        primary = locations[0]
    else:
        primary = ToolLocation(file=SUBMITTED_FILENAME)

    evidence = None
    for e in elements:
        name = e.get("name") if isinstance(e, dict) else None
        if name:
            evidence = str(name)[:300]
            break

    det_id = detector.get("id")
    if not isinstance(det_id, str) or not det_id:
        det_id = f"{check}:{primary.start_line}-{primary.end_line}"

    return ToolFinding(
        id=det_id,
        kind="tool",
        status="confirmed",
        tool=TOOL_NAME,
        tool_version=tool_version,
        check=check,
        severity=severity,  # type: ignore[arg-type]
        impact=impact,
        confidence=confidence,
        title=_short_title(description, check),
        description=description or _short_title(description, check),
        reference=_safe_reference(detector.get("reference")),
        file=primary.file,
        start_line=primary.start_line,
        end_line=primary.end_line,
        locations=locations,
        evidence=evidence,
    )


def parse_slither_json(data: dict, tool_version: str | None) -> list[ToolFinding]:
    """Normalize a parsed Slither JSON document into findings.

    Raises ValueError if the document is not a successful Slither result.
    """
    if not isinstance(data, dict):
        raise ValueError("Slither JSON is not an object")
    if data.get("success") is False:
        raise ValueError(str(data.get("error") or "Slither reported failure"))
    results = data.get("results") or {}
    detectors = results.get("detectors") or []
    if not isinstance(detectors, list):
        raise ValueError("Slither JSON 'results.detectors' is not a list")
    return [
        normalize_detector(d, tool_version)
        for d in detectors
        if isinstance(d, dict)
    ]


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

_TOOL_NOTICE = (
    "Slither is a deterministic static analyzer. Its findings are partial and "
    "may miss issues; a run with no findings does NOT mean the contract is "
    "safe. This is not a complete security audit."
)


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
) -> SlitherResponse:
    findings = findings or []
    return SlitherResponse(
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


def _first_compile_error_line(text: str) -> str | None:
    """Best-effort short compile-diagnostic line from sanitized output."""
    for marker in ("Error:", "Compilation warnings/errors", "Invalid solc compilation"):
        idx = text.find(marker)
        if idx != -1:
            snippet = text[idx : idx + 300].strip()
            return re.sub(r"\s+", " ", snippet)
    return None


async def run_slither(source: str) -> SlitherResponse:
    """Analyze `source` with the local Slither and return a normalized report.

    Always returns a structured response; never raises for tool-level failure
    (unavailable, compile error, timeout, bad JSON).
    """
    started = time.monotonic()
    source_name = SUBMITTED_FILENAME
    slither_exe = Path(settings.slither_exe)
    solc_exe = Path(settings.solc_exe)
    version = _probe_version(str(slither_exe), ["--version"], _first_nonempty_line)

    if not slither_exe.exists():
        return _make_response(
            status="unavailable",
            summary="Slither is not available locally, so no analysis was run.",
            source_name=source_name,
            started=started,
            version=version,
            detail="Slither executable missing under tools\\slither\\venv\\Scripts.",
        )
    if not solc_exe.exists():
        return _make_response(
            status="unavailable",
            summary="Slither needs the local solc compiler, which is not available.",
            source_name=source_name,
            started=started,
            version=version,
            detail="solc executable missing under tools\\solc.",
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

    tmp = Path(tempfile.mkdtemp(prefix="slither-", dir=str(work_root)))
    try:
        src_path = tmp / SUBMITTED_FILENAME
        # Normalize line endings to LF and write byte-exactly. A Windows
        # text-mode write would rewrite LF as CRLF; if the caller already
        # supplied CRLF (e.g. the MCP handoff), that yields CRCRLF and Slither
        # then reports every source line number doubled. Writing normalized
        # bytes keeps the analyzer's line/column numbers matching the source
        # the user actually submitted.
        normalized = source.replace("\r\n", "\n").replace("\r", "\n")
        src_path.write_bytes(normalized.encode("utf-8"))
        json_path = tmp / _JSON_NAME

        # Fixed argv template. Only local absolute paths + fixed flags.
        argv = [
            str(slither_exe),
            SUBMITTED_FILENAME,
            "--solc",
            str(solc_exe),
            "--compile-force-framework",
            "solc",
            "--exclude-dependencies",
            "--disable-color",
            "--json",
            _JSON_NAME,
        ]

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
                    f"Slither exceeded the {settings.analyzer_timeout:g}s time limit "
                    "and was stopped. No findings are reported for this run."
                ),
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        if not json_path.exists():
            detail = _first_compile_error_line(raw_output)
            # No report file: almost always a compile failure (invalid source)
            # or an internal Slither error. Distinguish when we can.
            low = raw_output.lower()
            looks_compile = bool(detail) or any(
                marker in low
                for marker in (
                    "invalid solc compilation",
                    "compilation warnings/errors",
                    "error:",
                    "parsererror",
                    "expected type name",
                )
            )
            if looks_compile:
                return _make_response(
                    status="compiler_error",
                    summary=(
                        "Slither could not compile the submitted source "
                        "(invalid Solidity or an unsupported compiler setting). "
                        "No findings are reported for this run."
                    ),
                    source_name=source_name,
                    started=started,
                    version=version,
                    detail=detail,
                    raw_output=raw_output,
                    raw_truncated=raw_truncated,
                )
            return _make_response(
                status="error",
                summary="Slither did not produce a report for this run.",
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        # Read and parse the JSON report (bounded).
        size = json_path.stat().st_size
        if size > _MAX_JSON_BYTES:
            return _make_response(
                status="error",
                summary="Slither's report was unexpectedly large and was not parsed.",
                source_name=source_name,
                started=started,
                version=version,
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )
        try:
            text = json_path.read_bytes().decode("utf-8-sig", errors="replace")
            data = json.loads(text)
        except Exception as exc:
            return _make_response(
                status="error",
                summary="Slither produced a report that could not be parsed.",
                source_name=source_name,
                started=started,
                version=version,
                detail=sanitize_output(str(exc))[0],
                raw_output=raw_output,
                raw_truncated=raw_truncated,
            )

        try:
            findings = parse_slither_json(data, version)
        except ValueError as exc:
            return _make_response(
                status="error",
                summary="Slither reported a failure for this run.",
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
                    "Slither reported no findings for this run. A clean run does "
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
            summary=f"Slither reported {len(findings)} finding(s) for this run.",
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
            summary="Slither run failed unexpectedly.",
            source_name=source_name,
            started=started,
            version=version,
            detail=sanitize_output(str(exc))[0],
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = [
    "TOOL_NAME",
    "SUBMITTED_FILENAME",
    "run_slither",
    "parse_slither_json",
    "normalize_detector",
    "sanitize_output",
    "get_tool_info",
    "get_tools_info",
    "health_tools",
]
