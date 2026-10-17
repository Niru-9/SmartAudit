"""Runtime configuration for the Auditor backend.

Everything is local-first: the only network call the backend makes is to the
configured local Ollama endpoint. No hosted models, no blockchain, no RPC.

Analyzer tools are called by absolute path from `tools/` (never from PATH)
with a fixed argument template. See `backend/slither_adapter.py`.

Settings can be overridden with environment variables:
    AUDITOR_OLLAMA_URL        default http://localhost:11434
    AUDITOR_MODEL             default qwen2.5:3b
    AUDITOR_TIMEOUT           request timeout in seconds, default 180
    AUDITOR_NUM_PREDICT       max tokens to generate, default 1600
    AUDITOR_TEMPERATURE       sampling temperature, default 0.2
    AUDITOR_HOST              bind host, default 127.0.0.1
    AUDITOR_PORT              bind port, default 8000
    AUDITOR_SLITHER_EXE       default tools\\slither\\venv\\Scripts\\slither.exe
    AUDITOR_SOLC_EXE          default tools\\solc\\solc.exe
    AUDITOR_NODE_EXE          Node runtime used to launch Solhint, default the
                              `node` on PATH (or the standard Windows install)
    AUDITOR_SOLHINT_JS        default tools\\solhint\\node_modules\\solhint\\solhint.js
    AUDITOR_SOLHINT_CONFIG    Solhint ruleset used for a run, default the bundled
                              tools\\solhint\\node_modules\\solhint\\conf\\rulesets\\
                              solhint-recommended.js
    AUDITOR_ANALYZER_TIMEOUT  analyzer wall-clock timeout (s), default 120
    AUDITOR_MAX_OUTPUT_BYTES  per-stream/raw-output cap in bytes, default 262144
    AUDITOR_WORK_DIR          base dir for isolated analysis temp dirs,
                              default <system temp>\\auditor-work
    AUDITOR_MCP_STATE_FILE    where the current-analysis record is written for
                              the read-only MCP server, default
                              <system temp>\\auditor-work\\mcp-current-analysis.json

Phase 6.2 (read-only deployed-contract lookup) settings:
    AUDITOR_SOURCIFY_BASE_URL     verified-source provider base URL (allowlisted;
                                  default https://sourcify.dev/server)
    AUDITOR_SOURCIFY_API_KEY      OPTIONAL provider key, server-side only. Sourcify
                                  reads do not require a key; if set it is sent as a
                                  bearer header and is NEVER logged or returned.
    AUDITOR_DEPLOYED_LOOKUP_TIMEOUT   per-lookup wall-clock timeout (s), default 20
    AUDITOR_DEPLOYED_MAX_RESPONSE_BYTES  provider response byte cap, default 524288
    AUDITOR_DEPLOYED_MAX_SOURCE_BYTES    bytes of verified source returned to the UI,
                                         default 262144
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository root = parent of the backend/ directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
FRONTEND_DIR = REPO_ROOT / "frontend"

# Canonical local analyzer installs. Chosen 2026-10-16: `tools\slither\venv\`
# (the copy already documented in tools\README.md); the duplicate
# `tools\slither\.venv\` is left untouched and is NOT used by the app.
SLITHER_EXE_DEFAULT = REPO_ROOT / "tools" / "slither" / "venv" / "Scripts" / "slither.exe"
SOLC_EXE_DEFAULT = REPO_ROOT / "tools" / "solc" / "solc.exe"

# Solhint (local Node linter, installed isolated under tools\solhint\ with a
# local npm prefix on 2026-10-17). It is a pure-JS package, so it is launched as
# `node <solhint.js> <file>` by absolute path — no global install, no wrapper
# script, and no shell. The ruleset is passed explicitly so a run never depends
# on a config file that happens to sit in the working directory.
NODE_EXE_DEFAULT = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
SOLHINT_JS_DEFAULT = (
    REPO_ROOT / "tools" / "solhint" / "node_modules" / "solhint" / "solhint.js"
)
SOLHINT_CONFIG_DEFAULT = (
    REPO_ROOT
    / "tools"
    / "solhint"
    / "node_modules"
    / "solhint"
    / "conf"
    / "rulesets"
    / "solhint-recommended.js"
)
# Temporary analysis dirs live OUTSIDE tools/ and the app source tree.
WORK_DIR_DEFAULT = Path(tempfile.gettempdir()) / "auditor-work"
# Current-analysis record read by the read-only MCP server (backend/mcp_auditor.py).
MCP_STATE_FILE_DEFAULT = WORK_DIR_DEFAULT / "mcp-current-analysis.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUDITOR_", extra="ignore")

    ollama_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    timeout: float = 180.0
    num_predict: int = 1600
    temperature: float = 0.2
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Local analyzer integration (Phase 3: Slither) ---------------------
    slither_exe: str = str(SLITHER_EXE_DEFAULT)
    solc_exe: str = str(SOLC_EXE_DEFAULT)
    # --- Local analyzer integration (Solhint; added 2026-10-17) -----------
    # Solhint is launched as `node <solhint_js> <file>` with an explicit ruleset.
    node_exe: str = str(NODE_EXE_DEFAULT)
    solhint_js: str = str(SOLHINT_JS_DEFAULT)
    solhint_config: str = str(SOLHINT_CONFIG_DEFAULT)
    analyzer_timeout: float = 120.0
    max_output_bytes: int = 262_144
    work_dir: str = str(WORK_DIR_DEFAULT)
    # Where the backend records the current analysis for the read-only MCP
    # server (backend/mcp_auditor.py). Analyzer output only; never model text.
    mcp_state_file: str = str(MCP_STATE_FILE_DEFAULT)

    # --- Phase 6.2: read-only deployed-contract verified-source lookup -----
    # One allowlisted provider (Sourcify). The base URL is fixed config, never
    # user-supplied, so no arbitrary URL can be reached (SSRF guard).
    sourcify_base_url: str = "https://sourcify.dev/server"
    # OPTIONAL server-side-only provider key. Sourcify reads need no key; if set,
    # it is sent as a bearer header and is never logged or returned anywhere.
    sourcify_api_key: str = ""
    deployed_lookup_timeout: float = 20.0
    deployed_max_response_bytes: int = 524_288
    deployed_max_source_bytes: int = 262_144

    # Shown to the user and returned with every model response.
    notice: str = (
        "AI-assisted review only. Model output may be incomplete or incorrect "
        "and is not a complete security audit. Nothing here compiles, deploys, "
        "signs, or contacts a blockchain."
    )


settings = Settings()


def _env_summary() -> str:
    """Small, human-readable summary for logging (never prints source)."""
    return (
        f"ollama_url={settings.ollama_url} model={settings.model} "
        f"timeout={settings.timeout}s num_predict={settings.num_predict} "
        f"temperature={settings.temperature} "
        f"slither_exe={settings.slither_exe} solc_exe={settings.solc_exe} "
        f"node_exe={settings.node_exe} solhint_js={settings.solhint_js} "
        f"solhint_config={settings.solhint_config} "
        f"analyzer_timeout={settings.analyzer_timeout}s "
        f"max_output_bytes={settings.max_output_bytes} work_dir={settings.work_dir} "
        f"mcp_state_file={settings.mcp_state_file} "
        f"sourcify_base_url={settings.sourcify_base_url} "
        f"sourcify_api_key={'set' if settings.sourcify_api_key else 'unset'} "
        f"deployed_lookup_timeout={settings.deployed_lookup_timeout}s "
        f"deployed_max_response_bytes={settings.deployed_max_response_bytes} "
        f"deployed_max_source_bytes={settings.deployed_max_source_bytes}"
    )


__all__ = [
    "Settings",
    "settings",
    "REPO_ROOT",
    "PROMPTS_DIR",
    "FRONTEND_DIR",
    "SLITHER_EXE_DEFAULT",
    "SOLC_EXE_DEFAULT",
    "NODE_EXE_DEFAULT",
    "SOLHINT_JS_DEFAULT",
    "SOLHINT_CONFIG_DEFAULT",
    "WORK_DIR_DEFAULT",
    "MCP_STATE_FILE_DEFAULT",
    "_env_summary",
    "os",
]
