# `tools\` — local Solidity security tool bench

Everything here is kept **out of the app source tree** and off the global `PATH`. Tools are called by absolute path with explicit arguments. No RPC/chain/wallet/keys, no global installs, and no dependency downloads at analysis time.

## Layout

| Path | What | Verified version | Notes |
|---|---|---|---|
| `tools\solc\solc.exe` | Official Windows Solidity compiler | **0.8.37+commit.f401782d.Windows.msvc** | From `binaries.soliditylang.org/windows-amd64/`; SHA-256 verified against the official `list.json` (`967743b2…7d9ec9`). |
| `tools\slither\venv\` | Isolated Python venv for Slither — **CANONICAL install used by the app** | Slither **0.11.6**, crytic-compile **0.4.2** | Created with `py -m venv`; installed with that venv's own `pip`. Global Python was **not** modified. Wired into the app via `backend/config.py` (`AUDITOR_SLITHER_EXE`). |
| `tools\slither\samples\ReentrancyVault.sol` | Minimal demo contract (created 2026-10-16 with user approval) | n/a | Intentionally vulnerable (reentrancy + unguarded `selfdestruct`) purely to exercise detectors. **Not production code.** |
| `tools\slither\samples\slither-report.json` | JSON output of the local Slither run | n/a | Machine-readable evidence for the combined-report design. |
| `tools\mythril\` | Placeholder (empty) — contains a failed venv only | — | Mythril is **research-only**; not installed. See `action.md` (Blocked) and `research.md` §17.6: the pip path failed on native builds (`pyethash` ships **no wheels** for any Python; `blake2b-py` 0.4.0 has **no cp311 Windows wheel**), and Docker is absent (its official Windows path is the `mythril/myth` image). |
| `tools\solhint\node_modules\solhint\solhint.js` | Local **Solhint 6.2.5** linter (local npm prefix install) | **6.2.5** | Pure-JS, **no native dependencies**; installed 2026-10-17 with a local npm prefix (**no global install**, no app dependency). Wired into the app via `backend/config.py` (`AUDITOR_SOLHINT_JS`, `AUDITOR_NODE_EXE`, `AUDITOR_SOLHINT_CONFIG`) and launched as `node <solhint.js> <file>` with the bundled `solhint-recommended` ruleset. |

**Installed since 2026-10-17:** **Solhint 6.2.5** (pure-Node, no native deps; local npm prefix under `tools\solhint\`, integrated into the app). **Still not installed — researched 2026-10-17 (see `research.md` §17):** recommended order for this Windows box, each isolated under `tools\<tool>\` and called by absolute path: **next, Surya** (pure-Node, no native deps), then **Echidna** (defer: undocumented/unsigned Windows zip, needs a property harness), while **Securify2** and **MythX** are **avoided** and **Mythril** is **deferred** (Docker-only official path).

## How to run (verified)

```powershell
# compile gate
N:\Auditor\tools\solc\solc.exe <File.sol>

# Slither static analysis pointed at the local compiler
N:\Auditor\tools\slither\venv\Scripts\slither.exe <File.sol> --solc N:\Auditor\tools\solc\solc.exe
N:\Auditor\tools\slither\venv\Scripts\slither.exe <File.sol> --solc N:\Auditor\tools\solc\solc.exe --json -

# Solhint linter (pure-JS; explicit bundled ruleset, no update check)
node N:\Auditor\tools\solhint\node_modules\solhint\solhint.js <File.sol> -f json --config N:\Auditor\tools\solhint\node_modules\solhint\conf\rulesets\solhint-recommended.js --disc
```

Foundry (`forge` **1.8.1**) is pre-existing at `%USERPROFILE%\.foundry\bin` and was **not** reinstalled; call it by absolute path.

## How the app runs Slither (Phase 3)

`backend/slither_adapter.py` runs Slither locally against a **copy** of the submitted source. Properties enforced by the backend (not the browser):

- **One executable, absolute path, fixed argv** — nothing from the source, comments, model output, or request is ever passed as an argument or executed. The exact template is:
  ```
  tools\slither\venv\Scripts\slither.exe Submitted.sol ^
      --solc tools\solc\solc.exe ^
      --compile-force-framework solc ^
      --exclude-dependencies --disable-color ^
      --json slither-out.json
  ```
  `solc` is passed explicitly, so no compiler is selected or downloaded at run time. The JSON goes to a file (`--json <file>`), **not** stdout, because Slither/crytic-compile mix absolute paths into stdout logs.
- **Temp copy, outside the app tree** — the source is written as `Submitted.sol` inside a fresh temp dir under `AUDITOR_WORK_DIR` (default `<system-temp>\auditor-work`, i.e. `C:\Users\NIRUPAM\AppData\Local\Temp\auditor-work`). Never under `tools\` or the app source. The dir is always removed, including on failure/timeout.
- **Bounded run** — wall-clock timeout (`AUDITOR_ANALYZER_TIMEOUT`, default **120 s**); the child is killed on expiry. Per-stream/raw-output cap `AUDITOR_MAX_OUTPUT_BYTES` (default **262144** bytes); the JSON report is refused above 8 MB.
- **No network / RPC / compiler download / wallet / keys.**

Returned statuses: `ok`, `no_findings`, `unavailable`, `compiler_error`, `timeout`, `error`. A run always returns a structured response (partial results on failure); empty/whitespace source is rejected as HTTP 422/400 before the tool runs. Slither's own exit code is **not** trusted (successful runs returned `-1`/255) — success is decided by the presence of the JSON file plus `success: true`.

The normalized finding keeps provenance (`kind: "tool"`), check id, the **original** impact and confidence strings verbatim, a mapped severity, message, file/line span (file reduced to the basename `Submitted.sol`), evidence, and reference. Raw output is path-redacted (`<tools>`, `<work>`, `<path>`) and length-capped. A clean run reports **"no findings"**, never "safe".

## Local run result (2026-10-16)

Slither against `samples\ReentrancyVault.sol` reported **4** results: `reentrancy-eth` (High/Medium), `suicidal` (High/High), `reentrancy-events` (Low/Medium), `low-level-calls` (Informational/High).

## Concurrency note (2026-10-16)

A second OpenCode session was active in this workspace during Phase 2a. It created `tools\slither\.venv\` (also Slither 0.11.6) and `tools\slither\sample-results.json` (a Slither JSON run against the same demo sample). Those artifacts were **left in place, not deleted**.

**Canonical choice (2026-10-16, Phase 3):** `tools\slither\venv\` (above) is the copy the app uses — it is the one already documented here and in `action.md`, uses the conventional `venv` folder name, and was verified end-to-end through the API. The duplicate `tools\slither\.venv\` is **unused by the app and left untouched**. The canonical path is set in `backend/config.py` (`SLITHER_EXE_DEFAULT`) and overridable via `AUDITOR_SLITHER_EXE`.

## MCP bridge reuses this runner (Phase 4, 2026-10-17)

The optional local MCP bridge (`backend/mcp_server.py`) does **not** add a second
runner: its `run_slither` tool calls the **same** `backend/slither_adapter.py`
path documented above (same fixed argv, temp-copy, timeout, caps, sanitizer, and
statuses). The bridge only adds a stdio JSON-RPC transport plus an
`analyzer_status` read-only tool. It is bounded (outer timeout) and its temp
source file/subprocess are always cleaned up. No new analyzer is installed, and
the direct `POST /api/slither` path is unaffected.
