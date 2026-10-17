"""Read/write store for the **current analysis** (Phase 6).

The Auditor app is request/response: a Slither run is returned to the caller and
then forgotten. The read-only MCP server (``backend/mcp_auditor.py``) needs a
stable place to read "the current audit findings" from, so the backend records
the most recent normalized result here after each run (the direct
``POST /api/slither`` path and the user-approved MCP path).

Design boundaries
-----------------
- **Analyzer output only.** The record holds the app's existing normalized
  ``SlitherResponse`` data (findings, status, summary, tool + version). Model /
  Qwen text (reviews, chat replies, per-finding explanations) is **never**
  stored, so the read-only surface can never present model prose as analyzer
  output.
- **Reduced on purpose.** ``raw_output``, ``raw_output_truncated`` and ``detail``
  are excluded (no raw tool output / diagnostics on the read-only surface).
- **No paths.** Every file label is reduced to a neutral basename before it is
  stored, so no local filesystem path can leak even if a caller supplied one.
- **Current = latest.** A single record is overwritten in place; there is no
  history and no accumulation.
- Nothing here runs an analyzer, writes Solidity, or touches the network. It
  only serializes data the app already produced.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .schemas import SlitherResponse

# Schema tag so a stale/foreign file is ignored rather than mis-read.
SCHEMA = "auditor.current-analysis/1"

# Response fields that must never reach the read-only surface.
EXCLUDED_FIELDS = ("raw_output", "raw_output_truncated", "detail")

# Allowed normalized severities (unknown is the safe fallback).
_SEVERITIES = {"critical", "high", "medium", "low", "informational", "unknown"}

_DEFAULT_NOTICE = (
    "Deterministic static-analyzer output. Findings are partial and may miss "
    "issues; no findings does NOT mean the code is safe. This is not a complete "
    "security audit."
)


def state_path() -> Path:
    """Path of the current-analysis record (resolved at call time)."""
    return Path(settings.mcp_state_file)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _safe_file(name: Any) -> str:
    """Reduce any filename to a neutral basename (never a local path)."""
    if not isinstance(name, str) or not name:
        return "Submitted.sol"
    base = name.replace("\\", "/").split("/")[-1]
    return base or "Submitted.sol"


def _safe_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_finding(
    data: dict[str, Any], *, tool: str | None = None, tool_version: str | None = None
) -> dict[str, Any]:
    """Coerce a finding dict (full or brief) into the canonical stored shape.

    No text is invented: absent free-text fields become empty/None rather than
    copies of other fields.
    """
    check = str(data.get("check") or "unknown")
    start = _safe_int(data.get("start_line"))
    end = _safe_int(data.get("end_line"))
    fid = data.get("id")
    if not isinstance(fid, str) or not fid:
        fid = f"{check}:{start}-{end}"
    severity = data.get("severity")
    if severity not in _SEVERITIES:
        severity = "unknown"
    ref = data.get("reference")
    evidence = data.get("evidence")

    locations: list[dict[str, Any]] = []
    for loc in data.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        locations.append(
            {
                "file": _safe_file(loc.get("file")),
                "start_line": _safe_int(loc.get("start_line")),
                "end_line": _safe_int(loc.get("end_line")),
                "start_column": _safe_int(loc.get("start_column")),
                "end_column": _safe_int(loc.get("end_column")),
            }
        )

    return {
        "id": fid,
        "kind": "tool",
        "status": "confirmed",
        "tool": str(data.get("tool") or tool or "slither"),
        "tool_version": data.get("tool_version") or tool_version,
        "check": check,
        "severity": severity,
        "impact": str(data.get("impact") or "Unknown"),
        "confidence": str(data.get("confidence") or "Unknown"),
        "title": str(data.get("title") or check),
        "description": str(data.get("description") or ""),
        "reference": ref if isinstance(ref, str) else None,
        "file": _safe_file(data.get("file")),
        "start_line": start,
        "end_line": end,
        "locations": locations,
        "evidence": str(evidence) if evidence is not None else None,
    }


def _build_record(
    *,
    tool: str,
    tool_version: str | None,
    status: str,
    summary: str,
    source_name: str,
    findings: list[dict[str, Any]],
    notice: str | None,
    finding_count: int | None = None,
    truncated: bool = False,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "recorded_at": _now(),
        "origin": "analyzer",  # deterministic tool output only (never model text)
        "tool": tool,
        "tool_version": tool_version,
        "status": status,
        "summary": summary,
        "source_name": _safe_file(source_name),
        "finding_count": finding_count if isinstance(finding_count, int) else len(findings),
        "findings": findings,
        "findings_truncated": bool(truncated),
        "notice": notice or _DEFAULT_NOTICE,
        "excluded_fields": list(EXCLUDED_FIELDS),
    }


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".current-analysis-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record_analysis(resp: SlitherResponse) -> dict[str, Any]:
    """Record a normalized ``SlitherResponse`` as the current analysis.

    Used by the direct ``POST /api/slither`` path. Raises only on an I/O
    failure; callers treat recording as best-effort.
    """
    findings = [
        normalize_finding(f.model_dump(), tool=resp.tool, tool_version=resp.tool_version)
        for f in resp.findings
    ]
    record = _build_record(
        tool=str(resp.tool),
        tool_version=resp.tool_version,
        status=str(resp.status),
        summary=str(resp.summary),
        source_name=resp.source_name,
        findings=findings,
        notice=resp.notice,
    )
    _write_atomic(state_path(), record)
    return record


def record_tool_payload(
    payload: dict[str, Any], *, source_name: str = "Submitted.sol"
) -> dict[str, Any]:
    """Record the brief payload returned by the approved MCP bridge path.

    The bridge returns fewer fields per finding than the direct path; missing
    fields are left empty rather than invented, and the record keeps the same
    canonical shape.
    """
    tool = str(payload.get("tool") or "slither")
    tool_version = payload.get("tool_version")
    if not isinstance(tool_version, str):
        tool_version = None
    raw_findings = payload.get("findings") or []
    findings = [
        normalize_finding(f, tool=tool, tool_version=tool_version)
        for f in raw_findings
        if isinstance(f, dict)
    ]
    count = payload.get("finding_count")
    record = _build_record(
        tool=tool,
        tool_version=tool_version,
        status=str(payload.get("status") or "error"),
        summary=str(payload.get("summary") or ""),
        source_name=source_name,
        findings=findings,
        notice=payload.get("notice") if isinstance(payload.get("notice"), str) else None,
        finding_count=count if isinstance(count, int) else None,
        truncated=bool(payload.get("findings_truncated")),
    )
    _write_atomic(state_path(), record)
    return record


def load_current_analysis() -> dict[str, Any] | None:
    """Return the current-analysis record, or ``None`` when none is available.

    A missing, unreadable, or schema-mismatched file is treated as "no results"
    rather than being surfaced as machine detail.
    """
    path = state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        return None
    return data


def clear_current_analysis() -> None:
    """Remove the current-analysis record if present (used by tests/ops)."""
    try:
        state_path().unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


__all__ = [
    "SCHEMA",
    "EXCLUDED_FIELDS",
    "state_path",
    "normalize_finding",
    "record_analysis",
    "record_tool_payload",
    "load_current_analysis",
    "clear_current_analysis",
]
