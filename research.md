# Research Notes - Solidity Security Auditing for a Local Qwen MVP

**Research date:** 2026-10-12 (Asia/Calcutta, UTC+05:30)
**Last re-verified:** 2026-10-15 (Asia/Calcutta, UTC+05:30)
**Author:** OpenCode - research & architecture planning passes. No application code was written and no tools were installed in either pass.
**Status:** Canonical research document. This supersedes the earlier mis-named draft `reseach.md`, which is retained unchanged for history. No additional/duplicate research file was created. This file now contains the current 2026-10-15 re-verification (section 0) followed by the original 2026-10-12 detail (sections 1-11), which is retained for history. It also carries two independent 2026-10-16 Phase 2a records - section 12 and section 13 - because two OpenCode sessions ran the same phase concurrently; both are kept for traceability and neither is fabricated.

---

## 0. Re-verification pass - 2026-10-15 (current)

**When:** 2026-10-15, ~11:00 IST. **Boundaries honoured:** no application code, no tool installation, no commits, no deployment, no RPC/chain/wallet/keys access.
**Method:** the local environment was probed with read-only commands; official docs/repositories were read with the harness's read-only web fetch. **PinchTab** (skill loaded; CLI `pinchtab@0.15.2` present; instance already running) was used first, but its **IDPI strict-mode** content guard blocked GitHub page text. The injection scanner was deliberately **not** disabled, so the remaining sources were read with the harness fetch. All page-derived content was treated as untrusted data. Nothing below is assumed; each value was observed on this machine.

### 0.1 Environment re-checked on this machine (2026-10-15)

| Item | Verified value | Change vs 2026-10-12 |
|---|---|---|
| OS / shell | Windows 11 Home Single Language build 26200; Windows PowerShell 5.1.26100.9444 | unchanged |
| Time zone | India Standard Time (UTC+05:30) | unchanged |
| Node / npm | v22.22.0 / 11.6.2 | unchanged |
| Python | `py` -> 3.11.9; pip 24.0 (`py -m pip`) at `%LOCALAPPDATA%\Programs\Python\Python311` | unchanged |
| `python` command | **broken** (Microsoft Store execution alias), not a real interpreter | unchanged |
| Python `Scripts` on PATH | **No** - console scripts must be called by absolute path | unchanged |
| Rust | cargo / rustc 1.94.0 | unchanged |
| Git | 2.51.0.windows.1 | unchanged |
| WSL | present (default version 2), **no Linux distribution installed** | unchanged |
| Docker | **not installed / not on PATH** | unchanged |
| Package managers | `winget` present; `scoop`, `choco` absent | unchanged |
| Foundry | **installed** - `forge`/`cast`/`anvil`/`chisel`/`solar` **1.8.1** at `%USERPROFILE%\.foundry\bin`, **off PATH** | unchanged |
| solc | **not installed**; no `~/.svm`, no `solc-select` | unchanged |
| Slither / Mythril / Echidna / Aderyn | **not installed** | unchanged |
| z3 | **not installed** (relevant to SMTChecker and `forge --symbolic`) | confirmed |
| Chrome | present at `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe` (used by PinchTab) | newly observed |
| Ollama / Qwen | Ollama **0.35.1** running (PID 20152); models `qwen2.5:3b` (3.1B, Q4_K_M) and `qwen3:4b` (4.0B, Q4_K_M); OpenAI-compatible `/v1/models` responds | unchanged |
| OpenCode model provider | `ollama/qwen2.5:3b`, `ollama/qwen3:4b` exposed | unchanged |
| Python libs already present | fastapi 0.109.0, uvicorn 0.27.0, httpx 0.26.0, pydantic 2.5.3, pydantic-settings 2.1.0, fastapi_cors 0.0.6, eth-* | unchanged; no slither/mythril/crytic/solc/z3/vyper |
| npm global | `pinchtab@0.15.2`, `@opencode/cli@2.0.26`, `pnpm`, `yarn`, `truffle`, `@remix-project/remixd`, `vercel`, ... | no `@cyfrin/aderyn` |
| Repo inventory | only `plan.md`, `action.md`, `research.md`, `reseach.md`; **no `AGENTS.md`**; no hidden files, app source, or configs | confirmed |

**Net:** the environment is effectively unchanged from 2026-10-12. **Foundry is the only security-adjacent tool installed.** Everything else must be added, and Slither is the only analyzer that is realistic on this machine in four days.

### 0.2 Tool comparison, re-verified against official sources (checked 2026-10-15)

| Tool | What it analyzes | Install requirement **on this machine** | Useful output formats | Setup effort (here) | MVP decision |
|---|---|---|---|---|---|
| **Slither** | Solidity/Vyper source via compiler AST/IR; ~100 detectors (reentrancy, access control, unchecked calls, delegatecall, ...) with impact + confidence + source location | `py -m pip install slither-analyzer` (PyPI **0.11.6**, `requires_python >=3.10`; we have 3.11.9). Docs now also recommend `uv tool install slither-analyzer`. For direct single-file mode it needs a real **`solc`**; `solc-select` does **not** support Windows, so we supply a Windows `solc` and pass `--solc <path>` | text; `--json` (machine-readable); `--checklist` (Markdown) | **Low-Medium** (pip is easy; the solc placement is the wrinkle) | **MVP - primary, required** |
| **solc** | Compiles Solidity; emits diagnostics, AST/ABI/bytecode. Needed by Slither single-file mode and by Foundry | Download the **windows-amd64** static binary from `binaries.soliditylang.org/windows-amd64/` (list shows 100 builds; latest **0.8.37**) into `N:\Auditor\tools\solc\solc.exe`. Match the source `pragma`; never silently change it. `solcjs` is **not** a Slither substitute | compiler errors/warnings; `--ast-compact-json`; `--standard-json` | **Low** | **MVP - compile gate + Slither/Foundry dependency** |
| **Foundry** `forge` | Builds a Solidity project; runs unit, **stateless fuzz** (`testFuzz_*`, 256 runs default, `bound`/`vm.assume`, `seed`) and **stateful invariant** tests (`invariant_*`, handlers, ghost variables, `afterInvariant()`, `check_interval`, `max_time_delay`); traces (`-vvv`/`-vvvv`); `--brutalize`; MVP `--symbolic` (needs `z3`) | **Already installed (1.8.1)** - only needs PATH or absolute path. Official `foundryup` on Windows needs Git Bash or WSL, which is irrelevant since binaries exist. Configure `offline = true` and `solc = "<path>"` so no compiler is downloaded at analysis time | text; `--json` | **None** (installed) | **MVP - one authored local reentrancy test only; no `--fork-url`/RPC** |
| **Mythril** | EVM **bytecode** via symbolic execution; can emit concrete transaction sequences + source refs | `py -m pip install mythril` (PyPI **0.24.8**, classifiers cover Python **3.6-3.10**, `requires_python >=3.7.0`) - our **3.11.9** is outside the supported range; the robust path is the `mythril/myth` Docker image and **Docker is absent** | text; `-o json` | **High** (unsupported Python + no Docker) | **Defer / best-effort only** behind a strict timeout; source-file mode only |
| **Echidna** | Property-based **call-sequence fuzzer** (`echidna_*` predicates, assertion mode, Foundry mode, JSON driver) | No native Windows build. Official paths: precompiled Linux/macOS, Homebrew, Docker (`ghcr.io/crytic/echidna/echidna`), Stack (Haskell), Nix. Docker absent and WSL has no distro | text / JSON / none drivers | **Not practical here** | **Defer** (Foundry invariant testing covers the local need) |
| **Aderyn** | Rust-based Solidity static analyzer; Markdown/JSON/**SARIF**; Foundry/Hardhat aware | README: "**Windows users must have WSL installed**". Install options include `cyfrinup`, curl, Homebrew, or `npm install @cyfrin/aderyn -g`. Rust is present but the supported Windows path is WSL (no distro). Overlaps Slither | Markdown; JSON; SARIF | **Medium-High on Windows** | **Defer** (optional later) |
| **SMTChecker** | Compile-time **formal** checking of `assert` properties + overflow/div-by-zero/out-of-bounds (CHC engine; BMC deprecated) | Built into solc but must be enabled (`--model-checker-engine`, `--model-checker-targets`). Native solc needs **`z3`** installed (z3 is only bundled in the JS `soljson.js`, which Slither cannot use). Foundry's docs state the model checker is available "for OSX and Linux" and needs z3. **z3 is not installed here** | compiler warnings + counterexamples | **Medium** | **Defer** (optional stretch only) |
| **Local Qwen** | Natural-language explanation, triage, remediation assistance over source + normalized evidence - **not** a scanner or verifier | **Already running** - Ollama 0.35.1 at `http://localhost:11434`, OpenAI-compatible API; models `qwen2.5:3b`, `qwen3:4b` | chat completions (JSON/text) | **None** (running) | **MVP - required** |

### 0.3 Facts confirmed or newly established this pass

1. **`solc-select` is Windows-unsupported** - source-verified: `soliditylang_platform()` returns `linux-amd64`/`macosx-amd64` only and otherwise prints "Unsupported platform." and `sys.exit(1)`. So on native Windows we must supply our own `solc` binary.
2. **Official Windows `solc` binaries exist** at `https://binaries.soliditylang.org/windows-amd64/` (100 builds; latest 0.8.37 on 2026-10-15).
3. **Slither now recommends `uv`** as the fastest install path, but plain `pip` remains documented.
4. **Foundry can be kept offline** - the `offline = true` config key (env `FOUNDRY_OFFLINE`) makes Foundry "not attempt to download any missing solc versions", and `solc = "<path>"` points it at a local compiler. This lets the tool runner stay fully local at analysis time.
5. **Foundry's symbolic mode needs `z3`** (not installed) - so treat `--symbolic` as out of scope; use plain fuzz/invariant tests.
6. **No `AGENTS.md`** and no application code exist in `N:\Auditor`; only the four planning files.
7. Nothing beyond the local model endpoint needs to be online at analysis time if `solc` is pre-placed and Foundry is set offline.

### 0.4 Sources checked 2026-10-15

- Slither repository/README (Python 3.10+, `pip install slither-analyzer`, `uv`, needs `solc`, detectors, `--json`/`--checklist`) - https://github.com/crytic/slither
- Slither on PyPI (0.11.6, `requires_python >=3.10`, classifiers 3.10-3.14) - https://pypi.org/project/slither-analyzer/
- solc-select source (Windows unsupported) - https://github.com/crytic/solc-select/blob/master/solc_select/solc_select.py
- Solidity - Installing the Compiler (npm/Docker/Linux/macOS/static binaries; `solc-bin` mirror) - https://docs.soliditylang.org/en/latest/installing-solidity.html
- Official Windows solc build list (latest 0.8.37) - https://binaries.soliditylang.org/windows-amd64/list.json
- Solidity SMTChecker (engines, targets, z3/solvers, BMC deprecated) - https://docs.soliditylang.org/en/latest/smtchecker.html
- Foundry installation (foundryup requires Git Bash or WSL on Windows; precompiled binaries; Docker) - https://book.getfoundry.sh/getting-started/installation
- Foundry Solidity-compiler config (`solc`, `auto_detect_solc`, `offline`, model checker section) - https://book.getfoundry.sh/reference/config/solidity-compiler
- Foundry fuzz testing (256 default runs, `bound`, `vm.assume`, seed, `--brutalize`, `--symbolic` needs z3) - https://book.getfoundry.sh/forge/fuzz-testing
- Foundry invariant testing (`invariant_*`, handlers, ghost variables, `afterInvariant()`, `check_interval`, `max_time_delay`) - https://book.getfoundry.sh/forge/invariant-testing
- Mythril repository (Python 3.7-3.10, Docker image, `myth analyze`, `-t`, `--execution-timeout`) - https://github.com/Consensys/mythril
- Mythril on PyPI (0.24.8, classifiers 3.6-3.10) - https://pypi.org/project/mythril/
- Echidna repository (precompiled Linux/macOS, Homebrew, Docker, Stack, Nix; JSON driver; test modes) - https://github.com/crytic/echidna
- Aderyn repository (Windows requires WSL; cyfrinup/curl/Homebrew/npm; MD/JSON/SARIF) - https://github.com/Cyfrin/aderyn
- PyPI slither-analyzer / mythril metadata read via the PyPI JSON API - https://pypi.org/pypi/slither-analyzer/json, https://pypi.org/pypi/mythril/json

**Date checked:** 2026-10-15.

Purpose: inform a four-day, local-first MVP that combines existing Solidity analyzers with a local Qwen model into one provenance-preserving report, plus a safe local reentrancy walkthrough. This is product/security research, not a complete audit methodology.

---

## 1. Verified environment (checked locally on this machine, 2026-10-12)

Nothing below is assumed; each row was verified by running a command on this machine.

| Item | Verified value | How verified |
|---|---|---|
| OS | Windows 11 Home Single Language, build 10.0.26200 | `[System.Environment]::OSVersion` + `Win32_OperatingSystem` |
| Shell | Windows PowerShell 5.1.26100.9444 | `$PSVersionTable` |
| Time zone | (UTC+05:30) Chennai/Kolkata/Mumbai/New Delhi | `Get-TimeZone` |
| Node / npm | Node v22.22.0 / npm 11.6.2 | `node --version`, `npm --version` |
| Python | `py` -> Python 3.11.9, pip 24.0 (at `%LOCALAPPDATA%\Programs\Python\Python311`) | `py --version`, `py -m pip --version` |
| `python` command | **Broken** - resolves to the Microsoft Store app-execution alias, not a real interpreter | `python --version` -> "Python was not found" |
| Python `Scripts` dir on PATH | **No** - `%LOCALAPPDATA%\Programs\Python\Python311\Scripts` is absent from PATH | PATH inspection |
| Rust | cargo 1.94.0 / rustc 1.94.0 (at `%USERPROFILE%\.cargo\bin`) | `cargo --version`, `rustc --version` |
| Git | 2.51.0.windows.1 | `git --version` |
| WSL | WSL 2.6.3.0 present, **no Linux distribution installed** | `wsl --status`, `wsl -l -v` |
| Docker | **Not installed / not on PATH** | `Get-Command docker` |
| Foundry | **Installed** - `forge` 1.8.1, plus `cast`, `anvil`, `chisel`, `solar` at `%USERPROFILE%\.foundry\bin` - but **not on PATH** | ran `forge.exe --version` by absolute path |
| solc | **Not installed** (`solc`, `solc-select`, `~/.svm` all absent) | `Get-Command`, path checks |
| Slither | **Not installed** (no `slither`, no `slither-analyzer` in pip) | `Get-Command`, `py -m pip list` |
| Mythril | **Not installed** (no `myth`, no `mythril` in pip) | `Get-Command`, `py -m pip list` |
| Echidna | **Not installed** | `Get-Command`, `~/.echidna` absent |
| Aderyn | **Not installed** | `Get-Command` |
| Package managers | `winget` present; `scoop` and `choco` absent | `Get-Command` |
| Ollama / Qwen | Ollama **0.35.1** running (PID 20152); models `qwen2.5:3b` (1.8 GB) and `qwen3:4b` (2.3 GB, Q4_K_M, 4.0B params); OpenAI-compatible `/v1` works | `Get-Process`, `GET localhost:11434/api/tags`, `/api/version`, `/v1/models`, `/api/show` |
| OpenCode local model provider | OpenCode has an `ollama` provider exposing `ollama/qwen2.5:3b` and `ollama/qwen3:4b` | OpenCode models search |
| Python already-installed libs | fastapi, uvicorn, httpx, pydantic, pydantic-settings, python-multipart, playwright, numpy, lxml, pycryptodome, eth-* (web3-adjacent) | `py -m pip list` |
| npm global | `pinchtab@0.15.2`, `npm@11.6.2`, `pnpm`, `yarn`, `@opencode/cli`, `truffle`, `@remix-project/remixd`, `@ethereumjs/tx`, `vercel` | `npm ls -g --depth=0` |
| Repo contents | Only `plan.md`, `action.md`, `research.md`, `reseach.md`. No `AGENTS.md`, no hidden files, no app source, no configs | directory + hidden glob |

**Prior notes were partly stale:** an earlier draft claimed Foundry was "not installed". It **is** installed (off PATH). This is corrected here.

### Browser-research method
- **PinchTab** is available (skill present; CLI `pinchtab@0.15.2` installed). It was used to read the Aderyn repository page and to confirm it works in this environment.
- PinchTab caveat found: its IDPI allowlist defaults to `127.0.0.1,localhost,::1`, so external docs are blocked until domains are added to `security.allowedDomains`; and on this Windows host PinchTab's own `server stop/restart` fails because it shells out to `ps` (not present). Applying a new allowlist therefore required stopping the server process manually. `security.allowedDomains` was set to a focused list of official documentation/repository hosts (github.com, raw.githubusercontent.com, docs.soliditylang.org, getfoundry.sh, book.getfoundry.sh, cyfrin.io, www.cyfrin.io). This can be reverted with `pinchtab config set security.allowedDomains "127.0.0.1,localhost,::1"`.
- The rest of the sources were read with the harness's read-only web fetch. No page-provided instructions were treated as commands.

---

## 2. Tool-by-tool comparison (verified 2026-10-12)

### Slither - static analyzer (MVP: primary, required)
- **What it analyzes:** Solidity (and Vyper) source via the compiler AST/IR; ~100 detectors covering reentrancy, access control, unchecked calls, delegatecall, arithmetic, etc. Reports impact + confidence and source locations. Average < 1 s/contract.
- **Install on this machine:** `py -m pip install slither-analyzer` (requires Python 3.10+; we have 3.11.9, supported). Slither also needs a real **`solc`** for direct single-file analysis (it does *not* accept `solcjs`).
- **Output formats:** text, `--json` (machine-readable), `--checklist` Markdown; detector IDs, impact/confidence, file/line.
- **Setup effort:** Low, **except** for the solc-on-Windows wrinkle below.
- **Critical wrinkle:** `solc-select` (the tool Slither's docs recommend for managing solc) **does not support Windows**: its `soliditylang_platform()` only handles linux/darwin and exits with "Unsupported platform" otherwise. So on native Windows we must supply a Windows `solc` binary ourselves (download from `binaries.soliditylang.org/windows-amd64/`, or reuse the copy Foundry fetches into `~/.svm`), then point Slither/crytic-compile at it (e.g. `slither <file> --solc <path>`). Alternatively use a Foundry project and `slither .`.
- **MVP decision:** **Include (primary).**
- **Source:** https://github.com/crytic/slither (README "How to install", checked 2026-10-12); https://github.com/crytic/solc-select (checked 2026-10-12); PyPI `slither-analyzer` 0.11.6, `requires_python >=3.10` (checked 2026-10-12).

### solc - Solidity compiler (MVP: required compile gate)
- **What it analyzes:** compiles source; emits AST/ABI/bytecode, compiler diagnostics and errors. Needed by Slither for single-file mode; needed by Foundry.
- **Install on this machine:** download the Windows static binary from `binaries.soliditylang.org/windows-amd64/` into a workspace tools folder (e.g. `N:\Auditor\tools\solc`), or reuse Foundry's `~/.svm` copy. `solcjs` (npm) is *not* a substitute for Slither. Version must match the source `pragma`; do not silently change it.
- **Output formats:** compiler errors/warnings; `--abi`/`--bin`/`--ast-compact-json`; standard-JSON.
- **Setup effort:** Low.
- **MVP decision:** **Include (compile gate + Slither dependency).**
- **Source:** https://docs.soliditylang.org/en/latest/installing-solidity.html (checked 2026-10-12).

### Foundry (`forge`) - build, unit, fuzz, invariant tests (MVP: usable, already installed)
- **What it analyzes:** compiles a Solidity project and runs unit tests, stateless fuzz tests (functions with parameters; 256 runs by default) and stateful **invariant** tests (`invariant_*`, handlers, ghost variables, `afterInvariant()`), plus traces (`-vvvv`), `--brutalize` and an MVP `--symbolic` mode.
- **Install on this machine:** **already installed** (forge 1.8.1) - just add `%USERPROFILE%\.foundry\bin` to PATH or call by absolute path. Official `foundryup` on Windows needs Git Bash or WSL, but that is irrelevant here since binaries already exist. Verify any needed solc is fetched (forge manages its own `~/.svm`).
- **Output formats:** text + `--json`.
- **MVP decision:** **Include** - only for one authored toy reentrancy test. **Local only:** no `--fork-url`, no RPC. Do not promise general vulnerability discovery from `forge test`.
- **Sources:** https://book.getfoundry.sh/getting-started/installation ; https://book.getfoundry.sh/forge/fuzz-testing ; https://book.getfoundry.sh/forge/invariant-testing (all checked 2026-10-12).

### Mythril - symbolic execution (MVP: best-effort, timeboxed; likely defer)
- **What it analyzes:** EVM bytecode via symbolic execution; can produce concrete transaction sequences and source references.
- **Install on this machine:** `py -m pip install mythril`. **Compatibility risk:** the project documents **Python 3.7-3.10** (PyPI classifiers stop at 3.10), while this machine has **3.11.9**. `requires_python` is `>=3.7.0`, so pip will *attempt* it, but it is outside the supported range and historically fragile. The recommended robust path is the `mythril/myth` Docker image - **Docker is not installed here**.
- **Output formats:** text + `-o json`.
- **Setup effort:** Medium-High on this machine; strict timeout required (`--execution-timeout`, `-t`).
- **MVP decision:** **Attempt only if a timeboxed install + a source-file run works; otherwise defer** and clearly report "unavailable". Source-file mode only - never address/RPC mode.
- **Sources:** https://github.com/ConsenSysDiligence/mythril (checked 2026-10-12); PyPI `mythril` 0.24.8 (checked 2026-10-12).

### Echidna - property-based fuzzer (MVP: defer)
- **What it analyzes:** fuzzes call sequences against user-written invariants (`echidna_`-prefixed bool functions / assertion mode / Foundry mode); JSON output driver.
- **Install on this machine:** **no native Windows build.** Official paths are precompiled Linux/macOS binaries, Homebrew, Docker (`ghcr.io/crytic/echidna/echidna`), Stack (Haskell) and Nix. Docker is absent and WSL has no distro, so installation is not practical in four days.
- **MVP decision:** **Defer.** (Foundry invariant testing covers the same need locally with no extra install.)
- **Source:** https://github.com/crytic/echidna (checked 2026-10-12).

### Aderyn - Rust static analyzer (MVP: defer / optional)
- **What it analyzes:** Solidity project static analysis (Rust); Markdown, JSON and SARIF reports; Foundry/Hardhat aware; VSCode extension.
- **Install on this machine:** README states **"Windows users must have WSL installed"**. Install options: `cyfrinup`, curl installer, Homebrew, or npm (`npm install @cyfrin/aderyn -g`). Rust is present, but the project's supported Windows path is WSL, which has no distro here.
- **MVP decision:** **Defer.** It overlaps Slither, so it adds redundancy rather than a new analysis method for the four-day MVP. Re-evaluate later via the npm package or macOS/Linux.
- **Source:** https://github.com/Cyfrin/aderyn (checked 2026-10-12, read via PinchTab).

### Solidity SMTChecker - compile-time formal checking (MVP: defer / optional)
- **What it analyzes:** proves `assert`-style properties and checks overflow/underflow, div-by-zero, out-of-bounds, etc. Engines: CHC (powerful, handles unbounded transactions/reentrancy) and deprecated BMC. Reports counterexamples.
- **Install on this machine:** built into solc, but **must be enabled** (`--model-checker-engine`, `--model-checker-targets`). On native binaries it uses **z3**, which must be installed on the system (`z3` is only statically bundled in the JS `soljson.js` build, which Slither cannot use). Needs explicit assertions/specs; not a whole-contract auditor.
- **MVP decision:** **Defer.** Optional stretch only, and only if z3 is available for the chosen solc binary.
- **Source:** https://docs.soliditylang.org/en/latest/smtchecker.html (checked 2026-10-12).

### Local Qwen (MVP: required)
- **Verified:** Ollama 0.35.1 at `http://localhost:11434`, OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`), models `qwen2.5:3b` and `qwen3:4b`. OpenCode is also configured to reach these models.
- **MVP decision:** **Include.** Use as an explainer/critic over source + normalized evidence. Never a deterministic scanner.

---

## 3. Cross-cutting findings that shape the plan

1. **Slither is reachable but needs a hand-placed Windows solc** (solc-select does not support Windows).
2. **Foundry is already usable** - the cheapest path to a runnable toy reentrancy demonstration (no install, no Docker).
3. **Mythril is at real risk on Python 3.11** and Docker is absent - plan for it as optional/partial.
4. **Echidna and Aderyn both want WSL/Docker**, neither of which is provisioned - defer both.
5. **Do not rely on the `python` command** (Store alias); use `py` or absolute interpreter paths.
6. **Console scripts land off PATH** (`Python311\Scripts`), so invoke `slither.exe` by absolute path (or add that folder to PATH).
7. **Everything can stay local:** analyzers are local binaries, the model is local, and no chain/RPC/wallet component is required for any MVP flow.

**Minimum viable set:** Slither + a Windows solc + local Qwen + a single Foundry toy test. Everything else is optional and must not break the audit if absent.

---

## 4. Architecture options

### Option A - Node/TypeScript monolith (Vite frontend + Node backend)
- Frontend: Vite + React (editor, findings, Qwen chat, tool status).
- Backend: Express/Fastify on 127.0.0.1; spawns tools via `child_process` with fixed arg templates, timeouts, output caps, isolated temp dirs; calls Ollama over HTTP (OpenAI-compatible).
- Pros: one toolchain; Node/npm already present; simplest single-process story; easy to enforce one allowlist.
- Cons: Slither/Mythril are Python tools, so we parse their stdout/JSON from Node; any future need to use Slither's Python API is awkward.

### Option B - Python backend + Vite/React frontend (hybrid)  <- RECOMMENDED
- Frontend: same Vite + React SPA.
- Backend: FastAPI + uvicorn (127.0.0.1) - **already installed** (plus httpx, pydantic) - orchestrates tools via `asyncio.create_subprocess_exec` with the same fixed allowlist/timeouts/output caps; calls Ollama via httpx or the OpenAI-compatible endpoint.
- Pros: analyzers (Slither, crytic-compile, Mythril) are Python-native, so invocation/parsing and any richer integration are first-class; backend dependencies are effectively zero-setup; strong Pydantic model for the normalized finding schema; `asyncio` subprocess + timeout support is clean.
- Cons: two runtimes to run in dev (uvicorn + vite), or build the SPA and serve it statically from FastAPI; team must be comfortable in Python.

**Recommendation: Option B.** Rationale: the riskiest part of this MVP is tool integration, and the analyzers are Python tools on a machine that already has FastAPI/uvicorn/httpx/pydantic. Keeping the frontend on Vite/React satisfies the UI requirement while putting orchestration where the tooling lives. This *revises* the earlier Node-only recommendation in `plan.md`. If the team is JS-only, Option A remains viable because JSON parsing and subprocess control are simple in Node - but then treat Mythril as fully optional.

**Shared design (both options):**
- Frontend: Solidity input (paste/sample), findings list with severity/confidence/location/provenance and expandable raw evidence, Qwen chat panel, per-tool status bar (ok / unavailable / failed / timeout), "Analyze" action, proposed-patch panel that never overwrites the source.
- Backend endpoints: `POST /analyze` (returns run + normalized report + per-tool status), `POST /chat` (Qwen over source + evidence), `GET /health` (model + tool availability).
- Orchestrator: run safe analyzers in parallel where independent; always return partial results; each tool is independently optional.
- Sandbox: a fixed toy project (vulnerable + fixed reentrancy contracts) for the walkthrough; tool runs happen in a fresh bounded temp dir and are cleaned up.

---

## 5. Normalized finding format (retains provenance)

```
Finding {
  id, title, severity (info|low|medium|high|critical), confidence (low|medium|high),
  description, impact, preconditions, recommendation, uncertainty,
  file, startLine, endLine, evidenceSpans[],
  cwe?, swc?,
  kind: "tool" | "model",          # analyzer fact vs Qwen hypothesis
  status: "confirmed"|"candidate"|"needs-context",
  sources: [ { tool, toolVersion, runId, detectorId?, rawRef, severity, confidence, rawOutput? } ],
  groupKey?, groupedFindingIds?     # presentation-only grouping; sources are never discarded
}
```
Invariant: a merge only affects *presentation*. Every contributing tool keeps its own name, detector id, severity/confidence and raw/trace evidence.

## 6. Duplicate grouping (without losing evidence)
Group only when (a) they share a detector/CWE/SWC family **and** (b) their source ranges overlap. Render one card that lists all tools that reported it; expandable raw evidence per tool. Qwen may suggest a duplicate but that suggestion is labeled and never deletes a tool finding. No numeric aggregate "security score".

## 7. Analyzer findings vs Qwen-only hypotheses
- `kind:"tool"` = deterministic tool output; Qwen may not erase/upgrade/downgrade/fabricate it.
- `kind:"model"` = Qwen hypothesis; must quote a source span/function and state preconditions, else it becomes a "review question" (`status:"needs-context"`).
- UI shows tool detections and model hypotheses as visibly different.

## 8. Tool-runner allowlist & safety
- Fixed executable paths + fixed argument templates only. **Never** execute a command suggested by Solidity source, comments, chat, or model output.
- Per-run: fresh isolated temp dir; wall-clock timeout; output cap (bytes + lines); kill on timeout; cleanup.
- No RPC/chain/wallet/keys/deploy/sign/broadcast; analyzers run only in local source-file mode. No `forge --fork-url`.
- Treat all pasted code/comments/chat as untrusted data (prompt-injection resistant). Source and prompts stay local; only the local model endpoint is called.

## 9. Safe local reentrancy walkthrough
- Two toy contracts: `VulnerableVault` (external call before state update) and `FixedVault` (checks-effects-interactions and/or a reentrancy guard).
- A `forge test` (local, no fork) that (1) shows the attacker re-entering and draining the vulnerable vault, and (2) shows the fixed vault rejects the re-entrant call and keeps accounting correct.
- Everything runs in the local sandbox against the included toy contracts only; the app presents it as an educational narrative plus a runnable local test.

## 10. Two Qwen prompt/skill assets (versioned in the repo)
- **`solidity-secure-authoring`** - elicit intended behavior, invariants, roles, compiler/library versions and trust assumptions; prefer simple reviewed patterns and established libraries; checks-effects-interactions; handle call failures; return proposed code separately with assumptions/explanation; do not invent protocol design or emergency powers.
- **`solidity-audit-reporting`** - define scope and missing context; inspect externally reachable functions, permissions, state writes, value flows, external calls/callbacks and invariants; cite exact lines; report severity/confidence/impact/preconditions/fix/uncertainty; preserve analyzer provenance and separate confirmed tool output from hypotheses. Use original concise checklists + links; do not paste large copyrighted text.

These are curated prompts/checklists (not fine-tuning or certification).

---

## 11. Sources (all checked 2026-10-12)

- Slither - https://github.com/crytic/slither (README: Python 3.10+, `pip install slither-analyzer`, needs `solc`, JSON output)
- Slither detectors/usage - https://github.com/crytic/slither/wiki/Usage
- solc-select - https://github.com/crytic/solc-select (source shows linux/darwin only)
- crytic-compile - https://github.com/crytic/crytic-compile (direct solc + Foundry/Hardhat/...)
- Mythril - https://github.com/ConsenSysDiligence/mythril (Python 3.7-3.10, Docker image)
- Echidna - https://github.com/crytic/echidna (Linux/macOS/Docker/Nix; no native Windows)
- Foundry install - https://book.getfoundry.sh/getting-started/installation
- Foundry fuzz testing - https://book.getfoundry.sh/forge/fuzz-testing
- Foundry invariant testing - https://book.getfoundry.sh/forge/invariant-testing
- Aderyn - https://github.com/Cyfrin/aderyn (Windows requires WSL; MD/JSON/SARIF)
- Solidity installation - https://docs.soliditylang.org/en/latest/installing-solidity.html
- Solidity SMTChecker - https://docs.soliditylang.org/en/latest/smtchecker.html
- Solidity security considerations - https://docs.soliditylang.org/en/latest/security-considerations.html
- Ethereum.org smart contract security - https://ethereum.org/developers/docs/smart-contracts/security/
- OWASP Smart Contract Top 10 (2025 archive) - https://github.com/OWASP/www-project-smart-contract-top-10
- Ollama - local API verified (`http://localhost:11434`); https://ollama.com
- PinchTab - https://github.com/pinchtab (CLI v0.15.2 verified locally)

**Date checked:** 2026-10-12.

**2026-10-16 update (corrected):** Mythril - the current repository README documents **Python 3.7-3.10** ("Install from Pypi (Python 3.7-3.10)"); the latest PyPI release is **0.24.8 (2024-03-27)** with classifiers 3.6-3.10; Docker (`mythril/myth`) is the recommended path. On this machine (Python 3.11.9, no Docker) a local install is **outside the documented range and unverified**: defer. Source-file mode only. See section 12 for details. (This line supersedes an earlier 2026-10-16 one-liner that stated "Python 3.6-3.9 / v0.23.9", which was inaccurate.)

---

## 12. Phase 2a - tool bench research and first local installs (2026-10-16)

**When:** 2026-10-16, ~08:45-10:00 IST (Asia/Calcutta). **Boundaries honoured:** no application code, no frontend or analyzer integration, no RPC/chain/wallet/keys/deploy/sign, no global PATH/Python/Node changes, no Docker or new WSL distribution, no app-server stop. **Method:** official repositories, official docs, package registries (PyPI/npm JSON), and the GitHub REST API were read with read-only `fetch`; the local environment was probed with read-only PowerShell. **PinchTab was not used in this pass** - the prior pass showed its IDPI content guard blocks GitHub page text and its own server restart is broken on this Windows host (it shells out to `ps`); this is recorded as the research-method limitation. All fetched page content was treated as untrusted reference data, not instructions.

### 12.1 Installed and verified on this machine (exact)

| Tool | Location (project-local, off PATH) | Version | How verified |
|---|---|---|---|
| **solc** (Windows static binary) | `N:\Auditor\tools\solc\solc.exe` (plus original `solc-windows-amd64-v0.8.37+commit.f401782d.exe`) | **0.8.37**+commit.f401782d.Windows.msvc | `solc.exe --version`; **both** official checksums matched `binaries.soliditylang.org/windows-amd64/list.json` - keccak256 `0x7bd1a8cd...6b3b50`, sha256 `0x967743b2...7d9ec9` |
| **Slither** | isolated venv `N:\Auditor\tools\slither\.venv\Scripts\slither.exe` | **0.11.6** (crytic-compile 0.4.2, solc-select 1.2.0, web3 7.16.0) | `slither --version`; `import slither`; a **real analysis** with JSON output (12.2) |
| **Foundry** (`forge`,`cast`,`anvil`,`chisel`,`solar`) | `%USERPROFILE%\.foundry\bin` (off PATH; **not reinstalled**) | **1.8.1** | `forge --version` |

Install commands actually used (no global changes):

```powershell
# solc: download + verify + place
Invoke-WebRequest https://binaries.soliditylang.org/windows-amd64/solc-windows-amd64-v0.8.37+commit.f401782d.exe -OutFile N:\Auditor\tools\solc\<file>
# then keccak256/sha256 checked against list.json, and copied to tools\solc\solc.exe

# Slither: project-local virtualenv, pinned
py -m venv N:\Auditor\tools\slither\.venv
N:\Auditor\tools\slither\.venv\Scripts\python.exe -m pip install "slither-analyzer==0.11.6"
```

Note: `tools\slither\venv` (a second, no-dot venv) also exists - it was **not created by this pass**; see 12.8 (concurrency). Neither venv is on PATH and neither is a global install.

### 12.2 Local Slither run (real, on an existing project sample)

No standalone `.sol` file existed in the repository, but an existing demo sample (the classic reentrancy vault pair, `VulnerableVault` + `FixedVault`) is embedded in `frontend/app.js` as `const SAMPLE`. It was extracted **read-only** to a temp path (`%LOCALAPPDATA%\Temp\opencode\slither-run\VaultSample.sol`, not the app source tree) and analyzed:

```powershell
N:\Auditor\tools\slither\.venv\Scripts\slither.exe VaultSample.sol `
  --solc N:\Auditor\tools\solc\solc.exe --json VaultSample-slither.json
```

Observed result: `success: true`; **6 detector results**, including:

- `reentrancy-eth` - **High** impact / Medium confidence - `VulnerableVault.withdraw(uint256)` (external call before state write).
- `reentrancy-events` - Low / Medium - both vaults (event emitted after external call).
- `solc-version` - Informational / High - `^0.8.20` names known severe issues (VerbatimInvalidDeduplication, FullInlinerNonExpressionSplitArgumentEvaluationOrder, MissingSideEffectsOnSelectorAccess).
- `low-level-calls` - Informational / High - the `.call{value:...}("")` sites.

The `--json` file was re-parsed successfully (PowerShell `ConvertFrom-Json`), confirming machine-readable output for the future normalizer. Slither's `--json` needs a real `solc` (never `solcjs`); `--solc <path>` is the Windows workaround because `solc-select` is unsupported here.

### 12.3 Per-tool research (checked 2026-10-16)

Command forms below are the documented CLI shapes; on this machine the verified absolute paths are `N:\Auditor\tools\slither\.venv\Scripts\slither.exe` and `N:\Auditor\tools\solc\solc.exe`.

- **Slither** - *type:* source-level static analysis over the compiler AST/IR (Solidity + Vyper). *Finds:* ~100 detectors - reentrancy (eth/events/no-eth/benign), access control (`suicidal`, `controlled-delegatecall`), unchecked low-level calls/`send`, `tx.origin`, uninitialised state/local/storage, shadowing, naming, `solc-version`, assembly, unused state, etc., each with impact + confidence + source location. *Uses:* `slither <dir|file> [--solc <path>] [--json out.json] [--checklist] [--sarif out.sarif] [--exclude-*] [--filter-paths]`; also `slither-format`, `slither-check-erc`, `slither-prop`. *Inputs:* compilable Solidity + a compiler; single-file needs `solc`; projects use Foundry/Hardhat auto-detection via crytic-compile. *Output:* text, **JSON** (`--json`, also `--zip`), **SARIF** (`--sarif`), **Markdown checklist** (`--checklist`) - all parseable. *Install (Windows):* pip into a venv (done). *Local:* fully local. *License:* **AGPL-3.0** (copyleft - relevant if the bench is ever offered as a network service). *Limits:* needs compilable source; detector-based (false positives/negatives, no proof); hardcoded/ignored findings require triage. *MVP:* primary - **installed**.
- **solc** - *type:* the Solidity compiler. *Provides:* diagnostics, AST (`--ast-compact-json`), ABI/bytecode, standard-JSON; the compile gate every other tool depends on. *Install (Windows):* static `windows-amd64` binary (done; checksum-verified). *Local:* yes. *License:* GPL-3.0. *MVP:* required - **installed (0.8.37)**. Pin to the source `pragma`; never silently swap the compiler.
- **Foundry** (`forge`) - *type:* build + test harness: unit tests, stateless fuzz (`testFuzz_*`, 256 runs default; `bound`, `vm.assume`, `seed`), stateful invariant tests (`invariant_*`, handlers, ghost vars), traces (`-vvv`/`-vvvv`), `--brutalize`. *Uses:* `forge test [--json] [-vvvv]`; keep `offline = true` and `solc = "<path>"` so nothing is downloaded at analysis time; **never** `--fork-url`/RPC. *Output:* text, `--json`. *Install:* already present (1.8.1). *Local:* yes. *License:* MIT OR Apache-2.0. *Limits:* only finds what a written test asserts; not a general vulnerability scanner. *MVP:* one authored toy reentrancy test only.
- **Mythril** - *type:* **EVM-bytecode symbolic execution**; produces concrete transaction sequences + source refs. *Finds:* SWC classes (integer bugs, reentrancy, unchecked send/selfdestruct, weak randomness, etc.). *Uses:* `myth analyze <file>.sol -t <txs> --execution-timeout <s> -o json`; **source mode only** here (never `-a <address>`/RPC). *Inputs:* Solidity (compiles via solc) or bytecode. *Output:* text, `-o json` (parseable). *Install (Windows):* README documents **Python 3.7-3.10**; latest PyPI **0.24.8 (2024-03-27)**, classifiers stop at 3.10; Docker `mythril/myth` is recommended. This machine is Python 3.11.9 with **no Docker** -> outside supported range. A non-installing `pip install --dry-run mythril` **did resolve** the full dependency tree on 3.11 (incl. `z3-solver 4.12.5.0`, `py-evm 0.7.0a1`), so an attempt is *possible* but unsupported/untested. *Local:* yes (source mode). *License:* MIT. *Blocker:* unsupported Python + no Docker. *Concrete path for a later phase:* build a **Python 3.10** venv under `tools\mythril\` (obtain 3.10 first) **or** use Docker - neither is done here. *MVP:* **defer / best-effort**, strict timeout, never block the audit. *Not installed.*
- **MythX** - *type:* **hosted** SaaS by ConsenSys Diligence (hybrid static + symbolic + Harvey fuzzing); you upload source/bytecode (or address) to their API with an **API key** and get issues + a dashboard report. *Local?* **No - source leaves the machine**. *Current status:* **sunset.** `mythx.io` now shows a "MythX Sunset" banner - "we have made the difficult decision to sunset the MythX suite, effective **March 31st**" (the year is **not printed on the fetched page**; widely reported as 2023) - noting Mythril remains open source, Harvey became *Diligence Fuzzing*, and Maru will be archived. `docs.mythx.io` still serves legacy "create an account / generate API keys" content. *Action taken:* **none** - no account, no key, no source upload (per instructions). *Verdict:* **defunct/hosted; not an MVP candidate.**
- **Securify (v2.0)** - *type:* ETH Zurich SRI context-sensitive **Datalog** static analysis; Solidity >= 0.5.8. *Finds:* 37 patterns mapped to SWC (TOD/amount/receiver, unrestricted write/selfdestruct/delegatecall, DAO reentrancy, tx-origin, unhandled exceptions, naming, solc-version, ...). *Uses:* `securify <file>.sol [--include-severity ...| --exclude-severity ...]`, `securify --list`; optional Etherscan address mode (needs an API key). *Inputs:* **flat contracts only** (no `import` statements) + `solc`. *Output:* terminal text + DOT graphs; **no documented machine-readable JSON**. *Install:* needs **`solc` + `souffle` + `graphviz`** and a Python 3.7 virtualenv; Linux-oriented (`apt` instructions), `LD_LIBRARY_PATH` fiddling. *Local:* yes (if built). *License:* Apache-2.0. *Maintenance:* last commit **2025-05-25** (a README edit), 17 commits total, 635 stars, not archived but **effectively dormant**; overlaps Slither. *Verdict:* **defer / not practical** on this Windows box (souffle + Python 3.7 + flat-contract limit). Documentation only.
- **Solhint** - *type:* Solidity **linter** (security + style + best-practice rules), parser-based (no compiler needed). *Finds:* ~18 security rules incl. `reentrancy`, `avoid-tx-origin`, `avoid-low-level-calls`, `avoid-call-value`, `check-send-result`, `multiple-sends`, `avoid-suicide`, `no-unchecked-calls`, `compiler-version` (>= ^0.8.24), `func-visibility`/`state-visibility`, `not-rely-on-time`/`not-rely-on-block-hash`, `no-inline-assembly`, plus many style rules. *Uses:* `solhint --init`; `solhint 'contracts/**/*.sol'` or a single file; `--formatter json|sarif|stylish|compact|unix|table|tap` (JSON/SARIF are parseable); `--fix`, `--quiet`, `--max-warnings`, `stdin`, `list-rules`; config `.solhint.json` (`extends: solhint:recommended`), `.solhintignore`. *Install:* `npm install -g solhint` or project-local (Node present). *Local:* yes. *License:* **MIT**. *Maintenance:* latest **6.2.5**, published/modified **2026-10-08** -> **actively maintained**. *Windows:* Node-based, works. *MVP:* strong low-cost candidate for a later phase - fast, local, JSON formatter; complements (not replaces) Slither. **Not installed this phase** (research-only per scope).
- **Surya** - *type:* Solidity **inspector / visualiser**, **not a vulnerability scanner**. *Provides:* `graph` (DOT call graph), `ftrace` (`-j` JSON call trace), `flatten`, `describe` (contract/function summary incl. visibility/payable/state-modifying), `inheritance` (DOT), `dependencies` (C3 linearization), `parse` (`-j` AST), `mdreport` (Markdown report). *Inputs:* `.sol` files; `-i/--import` to resolve imports or `-c/--content` to pass source inline; `dot` (graphviz) only needed for image rendering. *Output:* text, DOT, **JSON** (`ftrace -j`, `parse -j`), **Markdown** (`mdreport`) - usable as Qwen context and in a combined report. *Install:* `npm install -g surya`. *Local:* yes. *License:* repository `LICENSE` file = **Apache-2.0** (GitHub metadata agrees); the README body says "GPL-3.0" - a **discrepancy** worth noting; treat the LICENSE file as authoritative. *Maintenance:* latest **0.4.13**, last push **2025-06-26** -> low but present activity, not archived. *MVP:* cheap local helper for call-graph/reachability context; add later. **Not installed this phase.**
- **Echidna** - *type:* property-based **call-sequence fuzzer** (crytic). *Finds:* counterexamples to user-written properties (`echidna_*` bool functions), assertion failures, or Foundry-mode tests; emits the failing call sequence. *Uses:* `echidna <target> --contract <C> --config <yaml> --format json`; needs the contracts to compile (README says install Slither/crytic-compile). *Inputs:* Solidity + properties + solc/crytic-compile. *Output:* text, JSON, replay corpus, Foundry reproducers. *Install (Windows):* **correction to earlier notes** - the README documents only Linux/macOS precompiled binaries, Homebrew, Docker (`ghcr.io/crytic/echidna/echidna`), Stack, and Nix, **but GitHub releases ship a native `echidna-<v>-x86_64-windows.zip` asset consistently since v2.2.x (2024) through the current v2.3.3 (2026-07-27)**. So a native Windows binary **does exist** (undocumented); it still needs `solc` + `crytic-compile` to compile contracts. *Local:* yes. *License:* **AGPL-3.0**. *Verdict:* **not installed this phase** (scope: research only, no WSL/Docker); a later phase can try the Windows zip under `tools\echidna\` reusing the Slither venv's crytic-compile, with no Docker/WSL. Foundry invariant tests cover the same need meanwhile.

### 12.4 Local vs hosted

| Category | Tools |
|---|---|
| **Local-only** (source never leaves the machine) | solc, Slither, Foundry, Solhint, Surya, Securify, Mythril (source mode), Echidna, local Qwen (Ollama) |
| **Hosted / sends source off-machine** | **MythX** (defunct) |

No local tool in this bench needs to upload source. Two offline-hygiene rules: pin/place `solc` so Slither never fetches a compiler, and set Foundry `offline = true` so `forge` never downloads. `solc-select` is Windows-unsupported and was therefore not used.

### 12.5 Recommended staged integration plan

1. **Phase 2b (next, core):** wire the placed `solc` + Slither into the backend allowlisted tool-runner; normalize Slither `--json` into the `Finding` schema; per-tool status; compile gate via `solc`; zero downloads at analysis time.
2. **Phase 2c (cheap breadth):** add **Solhint** (project-local npm, `--formatter json`) as a fast lint/context layer; optionally add **Surya** (`describe`, `ftrace -j`, `mdreport`) to give Qwen call-graph/reachability context. Both are parser-based, need no compiler, and are local.
3. **Phase 2d (demonstration):** one authored Foundry toy reentrancy test (`offline = true`, pinned `solc`) for the vulnerable-vs-fixed walkthrough.
4. **Phase 3 (optional/experimental, never blocking):** attempt **Mythril** in an isolated **Python 3.10** venv under `tools\mythril\` (strict `--execution-timeout`, source mode) and/or **Echidna** via the native Windows zip under `tools\echidna\` (still needs solc/crytic-compile). Report either as `unavailable` if it does not run.
5. **Not pursued:** **MythX** (sunset/hosted), **Securify** (dormant; needs souffle + Python 3.7 + flat contracts), **Aderyn** (WSL).

### 12.6 Local Qwen knowledge-notes plan (source-linked; to become versioned prompts)

Three assets, kept as concise, original-wording notes with direct links, the tool/version/date checked, and licence/attribution - no large third-party text copied:

1. **Secure Solidity authoring** - already drafted at `prompts/solidity-secure-authoring.md`. Elaboration: intent/invariants/roles/versions/trust assumptions; checks-effects-interactions; access control; external-call failure handling; established libraries.
2. **Evidence-based auditing & report writing** - already drafted at `prompts/solidity-audit-reporting.md`. Elaboration: scope, externally reachable functions, permissions, state writes, value flows, external calls/callbacks, invariants; cite exact code; severity/confidence/impact/preconditions/fix/uncertainty; preserve tool provenance.
3. **Tool-use guide (NEW)** - *when* to run each tool; exact verified inputs/commands and Windows paths/versions (e.g. `slither ... --solc tools\solc\solc.exe --json`); *how to read output* (detector id, impact/confidence, source location, SWC/CWE); *common limitations* (needs compilable source, detector-based, false positives/negatives, timeout/failure reporting); and **how to tell a `kind:"tool"` finding from `kind:"model"` speculation** - tool output is deterministic evidence anchored to a location; model output is a labelled hypothesis that must quote a code span and state preconditions or become a "review question". Keep concrete command examples inside the tool-use guide, separate from the general security principles in assets 1-2.

### 12.7 Sources checked 2026-10-16

- Slither repo (detectors, `--json`/`--sarif`/`--checklist`, `--solc`, AGPL-3.0) - https://github.com/crytic/slither ; latest release 0.11.6 - https://api.github.com/repos/crytic/slither/releases/latest
- Slither package (0.11.6, Python >=3.10) - https://pypi.org/project/slither-analyzer/
- Official Windows solc build list (0.8.37; keccak256/sha256) - https://binaries.soliditylang.org/windows-amd64/list.json
- Solidity installing-compiler docs - https://docs.soliditylang.org/en/latest/installing-solidity.html
- Foundry install (Windows via Git Bash/WSL for foundryup) - https://book.getfoundry.sh/getting-started/installation ; compiler config (`offline`, `solc`) - https://book.getfoundry.sh/reference/config/solidity-compiler
- Mythril README (Python 3.7-3.10; Docker; MIT) - https://github.com/ConsenSysDiligence/mythril ; PyPI 0.24.8 (2024-03-27) - https://pypi.org/project/mythril/
- MythX sunset banner - https://mythx.io/ ; legacy docs (account/API keys) - https://docs.mythx.io/
- Securify2 README (solc + souffle + graphviz, Python 3.7, flat contracts, 37 patterns, Apache-2.0) - https://github.com/eth-sri/securify2 ; last commit 2025-05-25 - https://api.github.com/repos/eth-sri/securify2/commits?per_page=1
- Solhint README + rules (npm, `.solhint.json`, formatters incl. JSON/SARIF, MIT) - https://github.com/protofire/solhint ; security rules - https://github.com/protofire/solhint/blob/develop/docs/rules.md ; npm 6.2.5 - https://registry.npmjs.org/solhint
- Surya README (commands, `ftrace -j`, `mdreport`) - https://github.com/ConsenSysDiligence/surya ; LICENSE (Apache-2.0) - https://raw.githubusercontent.com/ConsenSysDiligence/surya/master/LICENSE ; npm 0.4.13 - https://registry.npmjs.org/surya
- Echidna README (install paths) - https://github.com/crytic/echidna ; releases incl. `echidna-2.3.3-x86_64-windows.zip` - https://api.github.com/repos/crytic/echidna/releases ; AGPL-3.0
- Local verification: `slither --version` (0.11.6), `slither --help`, `solc.exe --version` (0.8.37), `forge --version` (1.8.1), Ollama `/api/version` (0.35.1), app `/api/health` (`status: ok`).

### 12.8 Concurrency observation (recorded for traceability)

While this pass ran, a **second OpenCode process** was active in the same workspace (two `opencode` PIDs; files under `tools\slither\` and lines in `action.md`/`research.md` were written between ~08:49 and ~09:06 IST that this pass did not create). Specifically: `tools\slither\venv` (a second Slither venv), `tools\slither\samples\ReentrancyVault.sol`, `tools\slither\samples\slither-report.json`, `tools\slither\sample-results.json`, and the `Phase 2` entries in `action.md`. **All of it was preserved untouched** (not deleted or overwritten). The result is two independent Slither installs (`.venv` by this pass, `venv` by the other process); either works. This note exists so the duplication is not mistaken for a mistake by a single agent.

**Date checked:** 2026-10-16.

> Correction added later the same day (see §12): Mythril's README documents **Python 3.7–3.10** (PyPI classifiers 3.6–3.10) and the current PyPI/release is **0.24.8**, not 0.23.9. The conclusion (defer) is unchanged.

---

## 13. Phase 2a - Tool research + first local installs (solc, Slither) - 2026-10-16 (second of two independent records; subsection labels below remain `12.x` for traceability - see section 12 for the first record)

**When:** 2026-10-16, ~08:46-09:1x IST. **Boundaries honoured:** no application code changed, no app server stopped, no global PATH/Python/Node changes, no commits, no RPC/chain/wallet/keys/deploy/sign/broadcast, no code uploaded to any service. An intentionally-vulnerable demo contract was created **only after asking the user** (none existed). All page content was treated as reference data.

**Method / browser note:** the harness read-only web fetch was used for all sources. The web-search tool returned "cancelled" repeatedly, and **PinchTab was not retried** (its earlier `#chatHistory`/IDPI failures are documented in §0/§1). Installs were done with official channels: a direct download of the official Windows solc binary (SHA-256 verified) and an **isolated venv** `pip install` (no `curl | sh`, no remote scripts).

### 12.1 Environment re-verified this pass (2026-10-16)

| Item | Value |
|---|---|
| OS / shell | Windows 11 build 26200; Windows PowerShell 5.1 |
| Python | `py` -> **3.11.9** (only interpreter installed; no 3.10) |
| Node / npm | **22.22.0** / **11.6.2** |
| Foundry | `forge` **1.8.1** pre-existing at `%USERPROFILE%\.foundry\bin` (off PATH) - **not reinstalled** |
| WSL / Docker | WSL 2 present, **no distro**; **Docker absent** |
| graphviz `dot` | **absent** (needed only for Surya/graph image output) |
| z3 | absent (SMTChecker / `forge --symbolic`) |
| Ollama / Qwen | unchanged: 0.35.1 at `http://localhost:11434`, `qwen2.5:3b` + `qwen3:4b` |
| App server | `GET http://127.0.0.1:8000/api/health` -> **200** (left running, untouched) |

### 12.2 What was installed (exact paths + verified versions)

- **solc 0.8.37** -> `N:\Auditor\tools\solc\solc.exe`. Source: `binaries.soliditylang.org/windows-amd64/solc-windows-amd64-v0.8.37+commit.f401782d.exe` (latest stable in the official `list.json`). **SHA-256 verified** by `Get-FileHash` against the value published in `list.json`: `967743b2d12155f59a0ddf9410afe25c7deaa73f4fe3d33c4dc0739f627d9ec9` (match). `solc --version` -> `0.8.37+commit.f401782d.Windows.msvc`.
- **Slither 0.11.6** (+ crytic-compile 0.4.2) -> isolated venv `N:\Auditor\tools\slither\venv\`. Created with `py -m venv`; installed with that venv's own `pip` (`pip install slither-analyzer`). `slither --version` -> `0.11.6`; `crytic-compile` 0.4.2. **Global Python untouched** (global `pip list` shows no slither/crytic).
- **Foundry 1.8.1** verified present; **not** reinstalled.
- **Not installed** (research-only/deferred): Mythril, Echidna, Securify, Solhint, Surya, MythX, Aderyn, z3, graphviz.

### 12.3 Local analysis run (verified)

Ran Slither against the approved demo sample `tools\slither\samples\ReentrancyVault.sol`, pointing it at the local compiler: `slither.exe <file> --solc tools\solc\solc.exe`. Result: **4 detections** (1 contract, 102 detectors): `reentrancy-eth` (High/Medium), `suicidal` (High/High), `reentrancy-events` (Low/Medium), `low-level-calls` (Informational/High). `--json` output parsed cleanly (4 detectors with impact/confidence); written to `samples\slither-report.json`. Slither exits non-zero when findings exist (expected). This confirms the full local chain **solc -> crytic-compile -> Slither** works on this Windows machine.

### 12.4 Tool-by-tool: findings this pass

**Slither** - static analysis of Solidity/Vyper via compiler AST/IR; ~100 detectors with impact/confidence/location. *Install:* `uv tool install slither-analyzer` (docs-recommended) or `pip install slither-analyzer`; needs a real `solc` (not `solcjs`). *Outputs:* text, `--json`, `--checklist` md. *Local.* Limits: heuristic (not proof); needs matching `pragma`; limited economic/logic insight. **Phase: MVP primary (installed).** Source: https://github.com/crytic/slither (AGPL-3.0).

**solc** - compiler + diagnostics/AST/ABI/bytecode. *Install:* official Windows static binary (done). *Outputs:* errors/warnings, `--ast-compact-json`, `--standard-json`. *Local.* **Phase: MVP compile gate (installed).** Source: https://docs.soliditylang.org/en/latest/installing-solidity.html ; build list https://binaries.soliditylang.org/windows-amd64/list.json (GPL-3.0).

**Foundry `forge`** - build + unit/fuzz(`testFuzz_*`)/invariant(`invariant_*`) tests, traces. Pre-existing 1.8.1. Keep `offline = true` + `solc = "<path>"`; no `--fork-url`. **Phase: MVP local demonstration (installed).** Sources: https://book.getfoundry.sh/forge/fuzz-testing , https://book.getfoundry.sh/forge/invariant-testing (MIT OR Apache-2.0).

**Mythril** - symbolic execution on EVM bytecode; concrete tx sequences + SWC IDs. README: **PyPI install for Python 3.7-3.10**, or Docker image `mythril/myth`. **This machine has only Python 3.11.9 and no Docker.** A `pip --dry-run` *would* install 0.24.8 (over-broad `requires_python >=3.7`), but it is officially unsupported -> high risk; the robust path (Docker) is unavailable; a source-only run also needs `solc`. *Commands (if runnable):* `myth analyze <file.sol> -t <n> --execution-timeout <s>`, `-o json`. *Local.* **Phase: blocked/deferred - document the blocker, keep optional behind a strict timeout.** Sources: https://github.com/ConsenSysDiligence/mythril (MIT) ; PyPI 0.24.8 https://pypi.org/pypi/mythril/json.

**MythX** - **hosted** analysis-as-a-service (SWC-based), account + `MYTHX_API_KEY`, source uploaded, web dashboard reports. **Status verified from the official site: sunset.** The homepage reads "**MythX Sunset** ... we have made the difficult decision to sunset the MythX suite, effective March 31st"; subscriptions cancelled/refunded; no new subscriptions. Mythril stays open source, Harvey -> Diligence Fuzzing, Maru archived. **Phase: retired - do not use** (also violates the offline/no-exfiltration scope). No account/API key created; no code uploaded. Source: https://mythx.io/ and https://mythx.io/sunset/.

**Securify** - static analysis for Solidity; v2 (ETH Zurich + ChainSecurity) uses **Datalog** context-sensitive analysis, supports Solidity ≥0.5.8, claims ~37 patterns. v1 repo is explicitly "**[DEPRECATED]**" (last code push 2020-01-23); v2 last pushed 2025-05-25. *Install/use:* Docker (`docker build`/`run`) or local with **solc + souffle + graphviz** - none available here (no Docker). Overlaps Slither with a heavier, less-maintained setup. **Phase: deprecated/superseded - omit.** Sources: https://github.com/eth-sri/securify , https://github.com/eth-sri/securify2 (Apache-2.0).

**Solhint** - linter for Solidity: style + a thin **Security** rule set (`avoid-call-value`, `avoid-low-level-calls`, `avoid-sha3`, `avoid-suicide`, `avoid-throw`, `avoid-tx-origin`, ...); categories also include Best Practices, Style Guide, Gas Consumption, Misc. *Install:* `npm install -g solhint` (npm latest **6.2.5**). *Use:* `solhint --init` -> `.solhint.json`; `solhint 'contracts/**/*.sol'`; `-f` formatters **stylish/table/tap/unix/json/compact/sarif**; `solhint stdin`; `solhint list-rules`; `--fix`; `--save`. *Local.* Limits: mostly style/config; shallow security vs Slither. **Phase: Phase 2b low-risk local add (research-only now).** Sources: https://github.com/protofire/solhint , https://github.com/protofire/solhint/blob/develop/docs/rules.md (MIT).

**Surya** - structure/visualisation inspector; **no vulnerability detection**. Commands: `graph`, `ftrace` (`-j` JSON), `flatten`, `describe`, `inheritance`, `dependencies`, plus `parse`; `-i/--import` or `-c/--content`; `--no-color`. *Repo note:* the canonical repo is now **`ConsenSysDiligence/surya`** (moved from `crytic/surya`); npm package `surya` latest **0.4.13**; last push 2025-06-26. Piping to `dot -Tpng` needs **graphviz** (absent; and Windows must use `-o` not `>`). **Phase: Phase 2b low-risk local add for manual triage (research-only now).** Sources: repo README (Apache-2.0).

**Echidna** - property/call-sequence fuzzer. Modes: `property` (default; `echidna_`-prefixed arg-less bool), `assertion`, `foundry`, `verification`. Uses Slither for pre-analysis; text/JSON/UI output. **Windows finding (correction):** the README still documents Linux/macOS/Homebrew/Docker/Stack/Nix, **but the v2.3.3 release (2026-07-27) ships `echidna-2.3.3-x86_64-windows.zip`** - so a native Windows binary now exists and the earlier "no native Windows build" claim is outdated. No install attempted this phase; **no WSL/Docker created**. Limits: needs Slither + a compiler; finds only what invariants express; bounded fuzzing (not proof). **Phase: candidate for a later phase (native Windows binary) or defer if Foundry invariants suffice.** Source: https://github.com/crytic/echidna (AGPL-3.0), releases page.

### 12.5 Local vs hosted / blocked summary

| Tool | Where it runs | Status on this machine |
|---|---|---|
| solc | Local | **Installed 0.8.37** |
| Slither | Local | **Installed 0.11.6** (isolated venv) |
| Foundry | Local | **Installed 1.8.1** (pre-existing) |
| Solhint | Local (npm) | Runnable (npm available); research-only now |
| Surya | Local (npm; `dot` for images) | Runnable for tables; graphviz absent; research-only now |
| Mythril | Local (Py ≤3.10) **or** Docker | **Blocked** (Py 3.11 + no Docker) |
| Echidna | Local binary (Windows zip exists) | Research-only; not installed; no WSL/Docker created |
| Securify v2 | Local (Docker/souffle/graphviz) | **Deprecated/superseded** - omit |
| MythX | **Hosted** (uploads source) | **Sunset** - do not use |

### 12.6 Recommended staged integration order

1. **Phase 2a (done):** Windows solc + isolated Slither + Foundry verification + research. Keep MythX/Securify out.
2. **Phase 2b (next, local & low-risk):** add **Solhint** (`-f json`/`sarif`) and **Surya** (structure/tables; install graphviz only if graph images are wanted); normalise Slither JSON into the finding schema; add per-tool status (ok/failed/timeout/unavailable).
3. **Phase 3 (local, higher effort):** try **Echidna** from the Windows zip in a bounded sandbox, or prefer **Foundry invariant tests** if that already covers the need. Attempt **Mythril** only in an isolated venv with a strict timeout, or explicitly report `unavailable` (no Docker/WSL to be added).
4. **Never:** MythX (sunset, hosted) and Securify (deprecated). No RPC/chain/wallet/keys/deploy/sign/broadcast; solc pre-placed and Foundry `offline`.

### 12.7 Sources checked 2026-10-16

GitHub API repo metadata (archived/license/last-push): `crytic/slither` (AGPL-3.0, pushed 2026-10-14), `ConsenSysDiligence/mythril` (MIT, pushed 2026-04-27), `crytic/echidna` (AGPL-3.0, pushed 2026-10-15), `protofire/solhint` (MIT, pushed 2026-10-08), `ConsenSysDiligence/surya` (Apache-2.0, pushed 2025-06-26), `eth-sri/securify` ([DEPRECATED], pushed 2020-01-23), `eth-sri/securify2` (Apache-2.0, pushed 2025-05-25), `foundry-rs/foundry` (Apache-2.0), `ethereum/solidity` (GPL-3.0). Raw READMEs: slither, mythril, solhint, surya, echidna, securify2. Release API: echidna v2.3.3 assets (Windows zip), mythril v0.24.8, solhint v6.2.5. PyPI JSON: slither-analyzer 0.11.6 (`>=3.10`), mythril 0.24.8 (`>=3.7.0`, classifiers ≤3.10). Official sites: https://mythx.io/ (sunset), https://binaries.soliditylang.org/windows-amd64/list.json (0.8.37 + SHA-256), https://docs.soliditylang.org/, https://book.getfoundry.sh/. npm registry: solhint 6.2.5, surya 0.4.13.

### 12.8 Concurrency note (important)

A **second OpenCode session was concurrently active** in `N:\Auditor` during this pass (it created `tools\slither\.venv\` and `tools\slither\sample-results.json`, and appended its own entries to `action.md` / a one-line note here). Its artifacts were **left in place, not deleted**, to honour "preserve unrelated changes". The record above reflects this session's independently verified facts; where its one-line Mythril note was inaccurate it is corrected in the note at the top of this section. **Operators should avoid running two agents against these planning files at once** (write-race risk).

**Date checked:** 2026-10-17.

## 14. Phase 4 — Project-owned Qwen knowledge assets + local MCP bridge (2026-10-17)

Two goals: (A) turn the ad-hoc prompt files into **versioned, project-owned
knowledge modules** wired into backend prompt assembly; (B) determine whether a
local **MCP** (Model Context Protocol) bridge connecting Qwen review/chat to a
Slither-exposing server is compatible with this app, and — if safely small —
implement it. Nothing here fine-tunes or trains the model; the knowledge modules
are *guidance text*, and MCP is a *transport*, not a model feature.

### 14.1 Goal A — versioned knowledge modules

`backend/knowledge.py` is the single registry. Each module is a Markdown file in
`prompts/` with a metadata header (`<!-- knowledge-module -->`, `<!-- version -->`,
`<!-- updated -->`), a model-facing body between `<!-- prompt:begin -->` and
`<!-- prompt:end -->`, and a trailing `## Sources` section that is cited for
provenance but **stripped before sending** (keeps prompts short). Task routing is
deliberately minimal so no request loads unnecessary text:

| Task | Modules sent |
|---|---|
| `review` | `audit-reporting` + `tool-usage` |
| `chat` | `audit-reporting` + `tool-usage` |
| `suggest` | `secure-authoring` |

All tasks additionally receive one shared `GUARDRAILS` block (untrusted-data
rule, tool-finding provenance, "triage not audit", never say "safe"). Measured
sizes: secure-authoring ≈2.6 KB, audit-reporting ≈3.3 KB, tool-usage ≈2.8 KB →
assembled system prompts ≈3.6 KB (suggest) to ≈7.1 KB (review/chat). The loader
is cached (`lru_cache`) and each body is capped (`max_chars`).

### 14.2 Goal B — is Qwen tool calling real here? (VERIFIED: yes)

Checked against official docs **and** the installed runtime:

- **Ollama tool calling** is documented at
  <https://docs.ollama.com/capabilities/tool-calling> — the `/api/chat` request
  takes a `tools` array and the response `message.tool_calls` carries the model's
  requested calls; results are returned as a `{"role":"tool", ...}` message.
- **Model capability tags** (from `/api/tags` on this machine): `qwen2.5:3b` →
  `["completion","tools"]`; `qwen3:4b` → `["completion","tools","thinking"]`.
  Library pages: <https://ollama.com/library/qwen2.5>, <https://ollama.com/library/qwen3>.
- **Empirically confirmed this session** with throwaway probes: both models emit
  real `tool_calls` through `/api/chat` with `tools` (correctly selecting
  `run_slither` vs `analyzer_status`), and the full loop (tool call → `tool`
  result message → final answer) completes.
- **Incidental environment change (must be recorded):** launching `ollama serve`
  triggered a **pre-staged Ollama repair/update** that silently upgraded Ollama
  from **0.35.1 → 0.40.5** (completed 2026-10-17 00:49 IST). `/api/tags` and
  tool calling were re-verified on 0.40.5. This was *not* requested; it is a
  change to a global tool version present on the machine.

### 14.3 Goal B — can a local MCP SDK/server run in this app? (SDK: no; stdlib: yes)

- The **official `mcp` Python SDK** (v2.3.0, MIT, Python ≥3.10) requires
  `pydantic>=2.12.0` and `httpx2>=2.10.0` (see PyPI JSON
  <https://pypi.org/pypi/mcp/json>). This app pins `pydantic` 2.5.3 and
  `httpx` 0.26.x; installing the SDK would pull ~12 transitive packages and
  risks breaking the running app. **Not installed — rejected as "not small".**
- The **protocol itself** is simple over stdio: newline-delimited **JSON-RPC 2.0**
  on the child process's stdin/stdout (server logs go to stderr), with a legacy
  `initialize` handshake and `tools/list` / `tools/call` / `resources/list` /
  `resources/read`. The current spec revision (`2026-07-28`) moves to per-request
  `_meta` + `server/discover`; a dual-era server may answer both. References:
  <https://modelcontextprotocol.io/docs/concepts/transports> (stdio transport).
- **Decision:** implement a **stdlib-only** MCP server (`backend/mcp_server.py`)
  and client (`backend/mcp_client.py`). This keeps the dependency footprint at
  zero and cannot break the app.

### 14.4 What was built (surface)

- **Tools** (no arguments — the model can never supply code/paths/commands):
  - `analyzer_status` — read-only; reports local analyzer availability/versions.
  - `run_slither` — delegates to the **existing** `slither_adapter.run_slither`
    (the runner is not duplicated). The source under review is delivered
    **out-of-band** by the client via the `AUDITOR_MCP_SOURCE_FILE` env var, so
    it never travels through the model.
- **Resources** (read-only): `auditor://knowledge/{secure-authoring,audit-reporting,tool-usage}`.
- **API:** `GET /api/mcp/status` (live spawn + capability probe); `/api/chat`
  gains `tools_enabled` (default **false**); new `POST /api/tools/run` executes an
  approved tool then asks the model for a grounded reply. The direct
  `POST /api/slither` button/endpoint is unchanged.

### 14.5 Approval / safety model

The model's tool request is **never** executed on its own. `/api/chat` with
`tools_enabled` returns a *proposal* (`requires_approval: true`) and runs
nothing; only the user pressing **Approve** hits `/api/tools/run`. Safeguards:
allowlisted tool names only; arguments validated and ignored; source never
supplied by the model; bounded timeouts; temp source file + subprocess always
cleaned up; findings keep `kind:"tool"` provenance and the "not a complete
audit" notice.

### 14.6 Verification (observed this session)

- `tests/test_mcp_bridge.py`: **48/48** (knowledge routing/stripping; MCP
  handshake, tools/list, resources, delegation, argument rejection; approval
  gate; direct `/api/slither` intact).
- **Live, real Qwen, no mocks** (server on `127.0.0.1:8000`, model
  `qwen2.5:3b`): a chat with `tools_enabled=true` produced a **real
  `run_slither` proposal**; approving it spawned the MCP server, ran Slither
  0.11.6 (**3 findings**: `reentrancy-eth` High/Medium, `solc-version`
  Info/High, `low-level-calls` Info/High), and returned a grounded reply.
  A second run of `analyzer_status` returned `slither 0.11.6 available`,
  `solc 0.8.37 available`, others `not_integrated`.

### 14.7 Sources checked 2026-10-17

- Ollama tool calling: <https://docs.ollama.com/capabilities/tool-calling>
- Ollama model library (tools tag): <https://ollama.com/library/qwen2.5>, <https://ollama.com/library/qwen3>
- MCP Python SDK metadata: <https://pypi.org/pypi/mcp/json> (v2.3.0; `pydantic>=2.12.0`, `httpx2>=2.10.0`)
- MCP transports (stdio): <https://modelcontextprotocol.io/docs/concepts/transports>
- Runtime: local `/api/tags` (this machine) → `qwen2.5:3b`, `qwen3:4b` both tagged `tools`; Ollama reported **0.40.5**.

**Date checked:** 2026-10-17.

## 15. Phase 5 — Demo-readiness verification of the Qwen + MCP + Slither flow (2026-10-17)

Goal: verify the exact path a judge sees in a **real browser** — paste
`tools\slither\samples\ReentrancyVault.sol`, ask local Qwen to run Slither,
confirm the proposal runs nothing until approval, reject once (no run), then
approve in a separate run and read the findings. Fix only demo-blocking or
misleading issues; add no analyzers or network/chain code.

### 15.1 Method

Python **Playwright 1.62.0** (bundled Chromium) against the running app on
`http://127.0.0.1:8000`. Selectors were grounded in the current UI
(`#solidityInput`, `#mcpToggle`, `#chatQuestion`, `#chatBtn`, `#chatProposal`,
`#slitherFindings`, `#chatSpinner`, `#chatError`, …), not assumed old IDs.
Network calls to `POST /api/tools/run` and `POST /api/slither` were recorded to
prove what did or did not run. Screenshots were captured but could not be
visually inspected by the agent (the model has no image input); states were
verified from the DOM instead.

### 15.2 Result (observed)

- **Proposal gate holds.** With the MCP toggle on, a chat produced a real
  `run_slither` **proposal** and **zero** `/api/tools/run` calls; the Slither
  panel stayed empty.
- **Reject path.** Clicking **Dismiss** logged *"(Tool request dismissed. Nothing
  was run.)"*; still **zero** tool calls and an empty findings panel.
- **Approve path.** A second ask → **Approve** made exactly **one**
  `/api/tools/run` call (the direct `/api/slither` endpoint was **not** used),
  Slither ran through the MCP bridge, and **4 findings** rendered with detector,
  severity, confidence and location. The model's chat reply explained the
  findings but did not alter them (the panel is rendered from tool output).
- **States.** MCP toggle default **off**; chat spinner visible + Send disabled
  during a request, re-enabled after; error texts correct
  (`Enter Solidity source first.`, `Ask a question.`); console clean.

### 15.3 Bug found and fixed — doubled line numbers on the MCP path

**Symptom:** approving the tool showed source locations `Submitted.sol:39-51`
and `57-61` for a 32-line contract whose real locations are `20-26` and `29-31`
(a direct `POST /api/slither` of the same source showed `20-26` / `29-31`).

**Cause:** double newline translation. `backend/mcp_client.py` wrote the
out-of-band source with `Path.write_text(...)`, which on Windows rewrites LF as
CRLF. `backend/slither_adapter.py` then wrote that already-CRLF string again
with `write_text(...)`, producing `CR CR LF` and an extra line break per line —
so Slither reported roughly `2n-1` for every `n`.

**Fix:** `backend/slither_adapter.py` now normalizes line endings
(`\r\n`/`\r` → `\n`) and writes **bytes**; `backend/mcp_client.py` writes the
hand-off file with **bytes** too. Net effect: the MCP path and the direct path
now report identical, correct locations for LF **and** CRLF input (the CRLF case
was also a latent bug on the direct path for pasted Windows line endings).

**Regression guards added:** one live check in `tests/test_slither_integration.py`
(CRLF vs LF parity) and two live checks in `tests/test_mcp_bridge.py`
(MCP-vs-direct parity, and MCP CRLF-vs-LF parity).

### 15.4 Verification after the fix

- Browser flow re-run: findings now show **`Submitted.sol:20-26`** and
  **`Submitted.sol:29-31`**, matching the direct analyzer baseline.
- `tests/test_slither_integration.py` **54/54**; `tests/test_mcp_bridge.py`
  **50/50**; `tests/test_parsing.py` **all passed**; `tests/test_error_paths.py`
  **20/20**; `py -m compileall -q backend tests` exit 0.
- **Server lifecycle:** the restarted backend was first launched from a
  background shell, which was reaped when that turn ended (process exited with
  code 1, no traceback — external termination, not a crash). It was relaunched
  **detached** (`Start-Process`, independent of the shell) and re-verified:
  listening on `127.0.0.1:8000`, `/api/health` → `status: ok`,
  `slither: available`, `GET /` → 200, `/api/mcp/status` → tools
  `['analyzer_status', 'run_slither']`.
- No new analyzers; no network/RPC/chain/wallet/keys/deploy; nothing committed.

### 15.5 Note on model prose

Qwen's chat reply is grounded and enumerates the Slither findings, but the small
model (3B) sometimes adds generic template lines that read oddly (a boilerplate
"No other issues detected" item). This is model *prose* quality, not a findings
problem: the deterministic panel is the source of truth and is clearly labeled
"tool finding". Recorded as a visible limitation, not a demo blocker.

**Date:** 2026-10-17.

---

## 16. Phase 6 — Read-only deployed-address review: research, proxy/upgrade analysis, and threat model (2026-10-17)

**When:** 2026-10-17 (Asia/Calcutta, UTC+05:30). **Status: research + staged plan only, nothing implemented.** No app code change, no MCP capability change, no analyzer/library/tool install, and `reseach.md` (superseded draft) was left untouched. The app was already running on `127.0.0.1:8000`; **no duplicate server was started**. This section records what an extension to "paste a source **and** optionally review a **deployed address, read-only**" would require, and it does not add that capability.

### 16.1 Method and its limits

- **PinchTab** (`pinchtab@0.15.2`) was attempted first, as requested. It could not be used for this research: navigation returned `403 ... navigation blocked by IDPI: Domain not in allowlist: ethereum.org (idpi_domain_blocked)`, and where a page did load the IDPI scanner returned `403 content blocked by IDPI scanner: 3 role-hijack patterns detected; transaction-coercion pattern detected`. PinchTab's allowlist/security config was **deliberately not modified**. This matches the class of limitation already recorded in §0 and §12.
- **Fallback used:** the harness read-only web fetch. Every URL in §16.11 was read with it on **2026-10-17**. Fetched page text is treated as **untrusted data**; no instruction from a fetched page is followed or executed.
- No browser session, no RPC/chain call, no wallet, no key, and no transaction in this phase.

### 16.2 Address → source / metadata retrieval (read-only)

An EVM address by itself does not identify a contract's source. Retrieval is a *third-party, best-effort, verification-dependent* lookup:

- **Etherscan API v2 (unified multichain).** Base `https://api.etherscan.io/v2/api`; a required `chainid` selects the chain; one API key covers the supported chains (docs list `/supported-chains`). `module=contract&action=getsourcecode` returns verified source + contract metadata; `action=getabi` returns the ABI for a verified contract. **Caveat:** `status: "0"` is used for *both* an error and a valid request that returned no record, so a caller must inspect `message`/`result` rather than treat `0` as failure; `result` is a JSON-encoded string. Docs: `https://docs.etherscan.io/etherscan-v2` and `https://docs.etherscan.io/api-endpoints/contracts` (checked 2026-10-17).
- **Blockscout REST API (per instance).** Blockscout is open-source and self-hostable; each instance exposes REST endpoints under `/api/v2/...` that include contract/verification data. A caller must know the instance base URL. Docs: `https://docs.blockscout.com/devs/apis/rest` (checked 2026-10-17).
- **Sourcify.** A decentralized contract metadata/source repository with an API and re-verification support; can return matched source/metadata and distinguishes full vs partial match. Docs: `https://docs.sourcify.dev/docs/intro/` and `https://docs.sourcify.dev/docs/api/` (checked 2026-10-17).
- **Chain identity.** Chain IDs follow EIP-155; the public registry at `https://chainid.network/chains.json` (and `https://chainlist.org`) maps chain IDs to names/RPCs. **An address is meaningless without a chain ID**, so chain selection must be an explicit input, not a default.
- **Address checksums.** EIP-55 defines the mixed-case checksum address. Normalize for display/compare, but never silently "correct" a user-supplied address; a mixed-case mismatch is worth surfacing.

**Unverified / missing-source handling (required):** if no verified source exists, say so plainly and stop, or offer a clearly-labeled bytecode/metadata-level result if a local tool can produce one read-only. **Never fabricate source** and never present an ABI as if it were the source. "Partial" verification can still yield an ABI or metadata.

**Rate limits / keys:** provider rate limits and keys are unavoidable for Etherscan v2. Treat `429`/over-limit as a normal status with retry guidance; never bundle a key in the repo; never log a key.

### 16.3 Proxy / beacon / admin resolution

Implementations are found through standardized storage slots and interfaces:

- **ERC-1967 (Proxy Storage Slots)** standardizes the storage slots for the **implementation**, **beacon**, and **admin** (slot = `keccak256(name) − 1` for the names `eip1967.proxy.implementation`, `eip1967.proxy.beacon`, `eip1967.proxy.admin`). Reading these slots is the canonical way to discover a proxy's target/admin. `https://eips.ethereum.org/EIPS/eip-1967`.
- **ERC-1822 (Universal Upgradeable Proxy Standard, "UUPS")** — *Stagnant*; proxy + logic with an upgrade path guarded by the implementation. `https://eips.ethereum.org/EIPS/eip-1822`.
- **ERC-1167 (Minimal Proxy / "clone")** — a tiny proxy that delegates to a fixed implementation. `https://eips.ethereum.org/EIPS/eip-1167`.
- **ERC-897 (DelegateProxy)** — historical `implementation()` / `proxyType()` interface. `https://eips.ethereum.org/EIPS/eip-897`.
- **ERC-2535 (Diamonds)** — multi-facet proxy routing by selector. `https://eips.ethereum.org/EIPS/eip-2535`.
- **ERC-7201 (Namespaced Storage Layout)** — `@custom:storage-location erc7201:...`. `https://eips.ethereum.org/EIPS/eip-7201`.
- **OpenZeppelin** supports the **UUPS**, **transparent**, and **beacon** patterns; transparent proxies have a *proxy admin* that can only upgrade (not interact with the implementation), UUPS relies on `_authorizeUpgrade` implemented in the logic contract, and a beacon can upgrade many beacon proxies atomically. `https://docs.openzeppelin.com/upgrades-plugins/` (checked 2026-10-17).

**Read-only resolution plan (evidence, not assumption):** ask the user for **chain + address**; attempt the explorer's public *verification metadata* first (it often labels "Proxy"); if that is inconclusive, read the ERC-1967 slots with a bounded read-only call (only if the user consents to an RPC read). **The tool must not assume an arbitrary address is a proxy or is upgradeable** — it should report "detected: UUPS/transparent/beacon/ERC-1167/unknown/none".

### 16.4 What "same address" means — and what it does not

- A **normal immutable contract cannot change logic without changing address.** Keeping the *same address* after a logic change requires an **already deployed, compatible proxy** at that address plus a **valid upgrade authority** (UUPS/transparent/beacon/diamond). This is not a general property of arbitrary contracts.
- **What a valid proxy upgrade can preserve:** the proxy **address**; the proxy's **storage** (state, ownership/roles, balances and config recorded as storage); ETH/ERC-20/ERC-721 balances held *at the proxy address*.
- **What it cannot preserve / caveats:** the **implementation address changes**; `immutable`/`constant` values and constructor-set values live in the logic **code**, so they change with the implementation; **event history and off-chain state** are untouched; storage **layout must stay compatible** (append-only for used slots — no reorder/retype/remove); an **uninitialized implementation** can be taken over (UUPS/transparent); a compromised **admin/owner key** can upgrade to malicious code; any behavior keyed to the old code hash changes.
- **OpenZeppelin** describes upgrade-safety and implementation-compatibility validation in `deployProxy`/`upgradeProxy` (Hardhat), the Foundry library `OpenZeppelin/openzeppelin-foundry-upgrades`, and Upgrades Core/CLI. The **Foundry plugin does not track deployments** and requires **reference contracts** (`@custom:oz-upgrades-from`) for validation; the **Hardhat plugin** records deployments under `.openzeppelin/`. Docs: `https://docs.openzeppelin.com/upgrades-plugins/foundry/foundry-upgrades` and `https://docs.openzeppelin.com/contracts/5.x/api/proxy` (checked 2026-10-17).

### 16.5 Library / tooling maturity (evaluated, not installed)

| Item | Maturity / position | Read-only fit here |
|---|---|---|
| OpenZeppelin Contracts 5.x + `.../proxy` (UUPS/transparent/beacon, `Initializable`, ERC-1967 slots) | Primary, widely used | Reference only; not needed to *read* a deployed proxy |
| OZ Upgrades Plugins (Hardhat + Foundry) + Upgrades Core/CLI (upgrade-safety + storage-layout validation) | Primary, mature | For *authoring/validating* upgrades; requires the project + reference contracts + (for the real upgrade) a signer |
| Foundry 1.8.1 (present, off PATH) | Present on this machine | Local build/tests; no `--fork-url`/RPC |
| Slither 0.11.6 (present) | Present | Source-level static analysis once a source is obtained |
| Etherscan v2 / Blockscout / Sourcify | Established providers | **The** address→source path; verbatim read-only |
| Qwen (Ollama) | Present | Explains; never determines |

Performing or validating a real upgrade needs a project, the implementation sources, and a **signer/broadcast** — all out of scope (no keys, no signing, no broadcast, no writes to any deployed contract). The extension is **read-only review**.

### 16.6 Safe MCP evolution

- MCP standardizes two transports: **stdio** (newline-delimited JSON-RPC over the child process's stdin/stdout; the server **MUST NOT** write anything to stdout that is not a valid MCP message, and uses stderr for logs) and **Streamable HTTP** (validate the `Origin` header, bind to localhost when running locally, authenticate). Source: `https://modelcontextprotocol.io/specification/2025-06-18/basic/transports` (checked 2026-10-17). The project already implements the stdio path; keep it and **do not add a network listener**.
- **Proposed (future, not in this phase):** add a *read-only* "resolve publicly verified source" tool/resource. Any **outbound** call (explorer API or a bounded RPC read) must be **explicitly consented** per use, with **user-supplied** chain + address; nothing about the source/model may supply a URL or address.
- **Excluded from the surface, permanently:** signing, key handling, sending transactions, deploying, writing/overwriting files, running arbitrary commands, and any breaking change to the existing protocol version/capabilities. The current MCP surfaces (`backend/mcp_auditor.py` read-only server; `backend/mcp_server.py` approval-gated bridge) are **unchanged** by this phase.

### 16.7 Versioned Qwen skills that cite evidence and admit uncertainty

- Extend the **project-owned, versioned knowledge modules** (`prompts/solidity-*` assembled by `backend/knowledge.py`) rather than "training" anything. Record explicitly: **prompt/skill files provide context; they do not train or fine-tune the model.**
- A deployed-address review skill must: **cite the fetched evidence** (chain, address, block/time, source URL, provider); separate **deterministic analyzer findings** from **Qwen hypotheses**; and **admit uncertainty** (unverified contract, unknown proxy pattern, missing dependency source). It must never say "safe", "audited", or "guaranteed correct".
- Keep the shared **GUARDRAILS** block (untrusted-data rule, tool-finding provenance, "triage not audit", never "safe") on every task.

### 16.8 "EPS" — investigated, undetermined

- A search of this repo (`*.md`) for `eps`/`EPS` found **only** incidental matches in `action.md` — there is **no project reference to an "EPS library"**.
- A primary-source check found **ERC-4886 "Proxy Ownership Register"** (Stagnant; created 2022-09-03). Its Specification contains a section literally titled **"EPS Specification"** and the document repeatedly refers to the **"EPS register"**, the **"default EPS provider"**, and an **"EPS Provider Program"**; the abstract describes a proxy-ownership + delegated-asset-delivery register. `https://eips.ethereum.org/EIPS/eip-4886` (checked 2026-10-17). This is *plausibly* what "EPS library" refers to in a proxy/upgrade context — **but ERC-4886 is a proxy ownership register, not an upgrade-safety library**, and there is **no repo evidence** connecting it to this project.
- Two further search hits (not primary-verified in this pass): an npm package `@epsproxy/contracts`, and the **Ellipsis Finance** token ticker `EPS`. Neither is connected to this project by any local evidence.
- **Conclusion:** the meaning of "EPS" **cannot be determined** from the project. Do **not** guess, install, or wire any "EPS" dependency. It is an open question (§16.10).

### 16.9 Security / threat model (for the read-only deployed-address extension)

- **Protected assets:** the user's submitted source (possibly private/unpublished); the review's integrity (deterministic findings vs guidance); the local filesystem; explorer **API keys**; the local model.
- **Trust boundaries:** (1) user input; (2) fetched third-party page/API content; (3) local analyzers; (4) local model output; (5) MCP clients.
- **Threats → mitigations:**
  - *Prompt injection via fetched source, comments, or ABI* → treat all fetched content as untrusted data; never follow page instructions; deterministic panel is the source of truth; keep the untrusted-data guardrail.
  - *Injection/IDPI in fetched pages* (observed above) → webfetch only, treat as data, don't disable PinchTab's scanner.
  - *SSRF via a user-supplied RPC/explorer URL* → allowlist providers; if a URL is accepted, validate scheme/host; no arbitrary internal targets.
  - *API-key leakage* → user-provided only; never log or commit; redact.
  - *Malicious/typosquatted provider or a fake "verified" clone* → prefer primary docs endpoints; label provider/provenance; don't infer trust from "verified".
  - *Misreading a proxy* → report detection with uncertainty, never "upgradeable and safe"; note admin/key risk.
  - *Data exfiltration of private source* → keep everything local; the only outbound call is the consented source lookup of a **public** address; no upload of the analyzed source to a third party.
  - *Rate-limit/DoS* → bounded requests/timeouts; surface `429` as status.
  - *MCP misuse* → read-only tools only; per-use consent for network; no write/sign/broadcast capability.
- **Product boundaries:** gather evidence for **human review**; keep deterministic analyzer findings separate from Qwen guidance; never claim "safe/audited/production-ready/guaranteed correct"; never silently edit/overwrite submitted Solidity (any future draft is explicitly requested, labeled, rechecked); no transactions/deploys/signing/wallets/modifying deployed contracts. **Deployed-address review is read-only.**

### 16.10 Open decisions / questions for the user

1. **What does "EPS" mean?** Best-supported reading is ERC-4886 (Proxy Ownership Register, whose spec section is titled "EPS Specification") — but that is a *proxy ownership register*, not an upgrade-safe library, and nothing in the repo confirms it. Alternatives seen: npm `@epsproxy/contracts`; Ellipsis Finance token `EPS`. Please give the exact library/repo, or say "drop it".
2. **Preferred first network** for address→source lookup (Ethereum mainnet / a specific L2 / a testnet). The address is ambiguous without a chain ID.
3. **Source provider + API keys:** Etherscan v2 (key required), a Blockscout instance, or Sourcify (typically no key)? Are keys available, and which provider is preferred? Keys must never be bundled.
4. **RPC read consent:** is a bounded read-only `eth_getStorageAt`/`eth_call` (via a user-provided or public RPC) acceptable for proxy detection, or restrict to explorer APIs only?
5. **Proxy-analysis depth:** detect + report only, or also attempt storage-layout compatibility validation (which needs the implementation sources and the Foundry/OZ upgrades toolchain)?
6. **MCP:** expose the new resolve-source capability as a read-only tool behind consent, or keep it entirely in the app and out of MCP?
7. **Bytecode-only contracts:** allow a clearly-labeled read-only bytecode/metadata result, or refuse when no source is verified?

### 16.11 Sources checked (all read 2026-10-17)

- ERC-1967 Proxy Storage Slots — `https://eips.ethereum.org/EIPS/eip-1967`
- ERC-1822 UUPS (Stagnant) — `https://eips.ethereum.org/EIPS/eip-1822`
- ERC-1167 Minimal Proxy — `https://eips.ethereum.org/EIPS/eip-1167`
- ERC-897 DelegateProxy — `https://eips.ethereum.org/EIPS/eip-897`
- ERC-2535 Diamonds — `https://eips.ethereum.org/EIPS/eip-2535`
- ERC-7201 Namespaced Storage Layout — `https://eips.ethereum.org/EIPS/eip-7201`
- ERC-4886 Proxy Ownership Register (contains "EPS Specification") — `https://eips.ethereum.org/EIPS/eip-4886`
- EIP-55 Checksum Address — `https://eips.ethereum.org/EIPS/eip-55`
- OpenZeppelin Upgrades Plugins (overview) — `https://docs.openzeppelin.com/upgrades-plugins/`
- OpenZeppelin Foundry Upgrades — `https://docs.openzeppelin.com/upgrades-plugins/foundry/foundry-upgrades`
- OpenZeppelin Proxies / writing upgradeable — `https://docs.openzeppelin.com/upgrades-plugins/proxies`, `https://docs.openzeppelin.com/upgrades-plugins/writing-upgradeable`
- OpenZeppelin Contracts 5.x Proxy API — `https://docs.openzeppelin.com/contracts/5.x/api/proxy`
- Etherscan v2 API + contract endpoints — `https://docs.etherscan.io/etherscan-v2`, `https://docs.etherscan.io/api-endpoints/contracts`, index `https://docs.etherscan.io/llms.txt`
- Blockscout REST API — `https://docs.blockscout.com/devs/apis/rest`
- Sourcify — `https://docs.sourcify.dev/docs/intro/`, `https://docs.sourcify.dev/docs/api/`
- MCP transports — `https://modelcontextprotocol.io/specification/2025-06-18/basic/transports`
- Chain registry — `https://chainid.network/chains.json`, `https://chainlist.org`
- Solidity security considerations — `https://docs.soliditylang.org/en/latest/security-considerations.html`
- Slither repository/README — `https://github.com/crytic/slither`

**Note:** the PinchTab attempts and their exact error strings are recorded for traceability; PinchTab's security configuration was not changed. No tool was installed, no MCP capability changed, no app code changed, and `reseach.md` was not touched.

**Date:** 2026-10-17.

---

## 17. Solidity-tool research pass — MythX, Securify, Solhint, Surya, Echidna, and Mythril recovery

**When:** 2026-10-17 (~14:24 IST onward). **Boundaries honoured:** no tool installed, downloaded, or executed; no app code, MCP behavior, or tool environment changed; no new tracker; not a full re-read of the project. **Method:** each tool was checked against primary sources — the project's own repository (GitHub API for `archived` / `pushed_at` / license), the official README/docs, and the package registries (npm, PyPI, Docker Hub). A local environment probe grounds the practicality calls. All sources were accessed **2026-10-17 (Asia/Calcutta)**.

**Preconditions confirmed before editing:** Mythril is under **Blocked** in `action.md`; no install process remains active (the only `pip`/`mythril`/`npm install` process match was this pass's own probe; the lone `python.exe` is the running app `uvicorn backend.main:app`, not an installer); `tools\` contains only `mythril` (empty venv, no Mythril), `slither`, `solc`. Two `opencode.exe` (shared serve host + a client) matched the previously documented concurrency pattern.

### 17.0 Local environment snapshot (2026-10-17)

| Component | Present / version | Bearing on the tools below |
|---|---|---|
| Node.js | **v22.22.0** | Satisfies Solhint's `engines: node >=20`; enables pure-Node tools. |
| npm | **11.6.2** | Can install Solhint/Surya into a local prefix. |
| Python | **3.11.9 only** (`py -0p` → `-V:3.11`), MSVC v.1938 64-bit | Blocks the unofficial Mythril pip path; affects Securify2 (wants 3.7). |
| Docker | **absent** (`docker` not on PATH) | Removes the official Mythril container path and Securify2's container path. |
| Graphviz `dot` | **absent** | Affects Surya image output (not its text/JSON commands). |
| git | 2.51.0.windows.1 | — |
| Foundry `forge` | 1.8.1 | — |
| solc (local) | 0.8.37 (`tools\solc\solc.exe`) | Compiler the analyzers would be pointed at. |
| Slither (local) | 0.11.6 in `tools\slither\venv\` | The tool every candidate is compared against. |

### 17.1 MythX — **AVOID** (discontinued hosted service)

- **What it was / what it checked.** A hosted SaaS ("security verification platform") that wrapped **Mythril** (symbolic execution), **Harvey** (fuzzing), and **Maru** (decompiler). It was *not* a local tool: you submitted source/bytecode and the analysis ran on ConsenSys servers.
- **Status (official).** The `mythx.io` homepage now shows a **"MythX Sunset 🌅"** notice: *"we have made the difficult decision to sunset the MythX™ suite, effective March 31st"*, with *"Mythril remains open source, Harvey has evolved into Diligence Fuzzing, and Maru will be archived"*. The `mythx-cli` repo (`ConsenSysDiligence/mythx-cli`) was last pushed **2024-03-20** (94 stars, MIT, not archived but dormant).
- **Local vs hosted.** Hosted only; required an account + **API key**; uploaded code to a third party.
- **Install method / platform.** `mythx-cli` (`npm`) + API key. Irrelevant now — the backend service is retired, so the CLI has nothing to call.
- **Complement to Slither.** None in its current form — it was a *front end* for Mythril; the offline engine (Mythril) is the only recoverable piece (see §17.6).
- **Recommendation: AVOID.** The service is discontinued, and a hosted, account/key-gated scanner that uploads source conflicts with this project's local-only, no-upload design.

*Sources:* `https://mythx.io/`; `https://github.com/ConsenSysDiligence/mythx-cli` (GitHub API: pushed 2024-03-20).

### 17.2 Securify / Securify2 — **AVOID / DEFER** (abandoned; heavy, non-Windows setup)

- **What it checks.** ETH Zurich SRI's static analyzer. **Securify2** "supports 37 vulnerabilities" and implements *"novel context-sensitive static analysis written in Datalog"* (souffle engine); it analyzes *"contracts written in Solidity >= 0.5.8"*. It complements Slither by using a different (Datalog/context-sensitive) method and surfacing some pattern classes Slither weights differently — but the overlap is substantial.
- **Status (official).** Securify **v1** README is titled `[DEPRECATED]` and the repo was last pushed **2020-01-23**. Securify2 was last pushed **2025-05-25** (635 stars, Apache-2.0, not archived) — i.e. light/occasional maintenance, not active development.
- **Local vs hosted.** Runs **locally** (self-hosted / self-built), but "blockchain mode" for deployed contracts fetches source and needs an **Etherscan API key**; the local sourcetrail path does not.
- **Install method / requirements (Windows).** Official README gives a Debian/Ubuntu `apt` flow (Python **3.7** virtualenv + **souffle** + **graphviz** + a pinned **solc 0.5.12**) or **Docker** (`docker build -t securify .` with `ARG SOLC=0.5.12`). **No Windows instructions and no Windows artifacts.** Input is a **flat, single** `.sol`; output is terminal text plus DOT graphs — **no JSON** is documented.
- **Practicality here.** Poor: needs Python 3.7 (we have 3.11.9), the external **souffle** Datalog engine, graphviz (absent), an **old pinned solc 0.5.12** (ours is 0.8.37), and Docker (absent) as the only documented shortcut. Plus the project is effectively abandoned.
- **Recommendation: AVOID.** Reasons: abandoned + heavy native/engine setup + no Windows path + old compiler pin + overlapping findings, for no clear gain over the installed Slither.

*Sources:* `https://github.com/eth-sri/securify2` (README; API pushed 2025-05-25); `https://github.com/eth-sri/securify` (README `[DEPRECATED]`; pushed 2020-01-23).

### 17.3 Solhint — **INSTALL NEXT** (fast, pure-Node linter; fully local)

- **What it checks / how it complements Slither.** A Solidity **linter**: style + best-practice rules, plus **security-oriented** rules (e.g. small-surface checks around `tx.origin`, `throw`/`suicide`/`sha3` deprecations, visibility of functions/state, compiler version pinning, no empty blocks, no complex fallback, unused vars) and maintainability rules (`code-complexity`, `function-max-lines`, `max-states-count`, `ordering`, gas rules). Documented rule groups: **Best Practices**, **Style**, **Security**, **Miscellaneous** (`protofire.github.io/solhint/docs/rules.html`). It is **not** a symbolic/bytecode analyzer — it does cheap, deterministic pattern linting with low false-positive cost, so it **complements** Slither (which does deep static/symbolic review) rather than duplicating it.
- **Status (official).** Active. Latest on npm is **solhint 6.2.5**; repo (`protofire/solhint`) last pushed **2026-10-08** (1,127 stars, MIT, not archived).
- **Local vs hosted.** Fully **local**; no account, no API key, no upload.
- **Install method / platform (Windows).** `npm install -g solhint` (or a project/local prefix); package `bin.solhint`, **pure JavaScript**, **no native dependencies**, no run-time downloads. `engines: node >=20` — the installed **Node 22.22.0** qualifies. Works on Windows.
- **Solidity/compiler constraints.** Parser-based; version-agnostic in practice, no solc invocation required. No significant native deps.
- **Practicality here.** High — the cheapest, most reliable addition, with no state to manage beyond config.
- **Recommendation: INSTALL NEXT (1st).** Keep it isolated under `N:\Auditor\tools\solhint\` (local npm prefix) and call the shim by absolute path, matching the existing `tools\` policy.

*Sources:* `https://registry.npmjs.org/solhint/latest` (v6.2.5, `engines.node >=20`, MIT); `https://github.com/protofire/solhint` (API pushed 2026-10-08); `https://protofire.github.io/solhint/docs/rules.html`.

### 17.4 Surya — **INSTALL NEXT** (structure/call-graph inspector; pure Node + optional graphviz)

- **What it checks / how it complements Slither.** Surya is a Solidity **structure/documentation inspector**: `describe` (per-function/modifier summaries), `graph` (function call graph), `inheritance`, `dependencies` (contract dependency tree), `parse`, and `ftrace`/`callgraph` queries. It produces human-readable structure and graphs that aid **manual review** — complementary to Slither's `printers`, but far more convenient for call/inheritance/dependency views. It is **not** a vulnerability detector.
- **Status (official).** Latest on npm is **surya 0.4.13**; repo (`ConsenSysDiligence/surya`) last pushed **2025-06-26** (1,172 stars, not archived). **License discrepancy:** the README's License section still says **GPL-3.0**, but the repository `LICENSE` and the GitHub API both report **Apache-2.0** and the npm package declares **Apache-2.0** — treat **Apache-2.0** as authoritative and flag the stale README line.
- **Local vs hosted.** Fully **local**; no account/API key.
- **Install method / platform (Windows).** `npm install -g surya`; package `bin.surya`, **pure JavaScript**. **Graphviz (`dot`)** is required **only** for the graph/image outputs (README: *"you need to have graphviz installed, so that you can run the `dot` command"*); `describe`, `dependencies`, `inheritance`, `parse` and DOT-text output work without it. README notes Windows users should use the `-o` flag instead of shell `>` redirection. Our box has **no graphviz** (so plan to consume text/DOT, or add graphviz later if images are wanted).
- **Solidity/compiler constraints.** Parses Solidity directly; generally version-flexible and fine with the local **solc 0.8.37**. No significant native deps.
- **Practicality here.** High — pure Node, fast, no services.
- **Recommendation: INSTALL NEXT (2nd, after Solhint).** Isolate under `N:\Auditor\tools\surya\` (local npm prefix), call by absolute path.

*Sources:* `https://registry.npmjs.org/surya/latest` (v0.4.13, Apache-2.0); `https://github.com/ConsenSysDiligence/surya` (README install/License; API pushed 2025-06-26, license Apache-2.0).

### 17.5 Echidna — **DEFER** (native binary; needs a property harness; Windows asset undocumented)

- **What it checks / how it complements Slither.** A **property-based fuzzer** (Haskell, powered by **hevm**) that falsifies user-defined predicates / Solidity `assert`s by generating inputs. It **complements** Slither by finding *runtime/stateful* bugs static analysis misses — but only for properties you actually write. It is **not** a "point it at a `.sol`" tool: value comes from authoring an `echidna_*`/invariant harness.
- **Status (official).** Very active. Latest release **v2.3.3**, published **2026-07-27**; repo (`crytic/echidna`) last pushed **2026-10-15** (3,185 stars, **AGPL-3.0**). v2.3.3 bundles **hevm 0.58.0**. (Note: the README also references an **Echidna MCP server** and `ECHIDNA_MCP_URL` — relevant to MCP-aware projects, out of scope for this doc.)
- **Local vs hosted.** Fully **local**; also ships a **Docker** image (`ghcr.io/crytic/echidna/echidna`). No account/API key for local runs (an RPC URL is used only for on-chain fuzzing).
- **Install method / platform (Windows).** The official README lists **precompiled binaries (Linux/macOS `.tar.gz`)**, **Homebrew**, **Docker**, **Stack**, and **Nix** — **Windows is not in the documented install matrix**. However, the v2.3.3 release **does** publish **`echidna-2.3.3-x86_64-windows.zip`** (22,274,161 bytes; `sha256:e08f754b794a0066a79161fdc61a19322e2cc38a7314e997e7f7818d8a1b8c97`; 454 downloads). Two cautions: (1) it was uploaded **separately by a maintainer** (not the release CI), and (2) it is the **only** asset **without** a `.sigstore.json` provenance sidecar that every other asset has. So a Windows binary exists but is **undocumented and lacks the signed attestation**.
- **Solidity/compiler constraints.** Uses crytic-compile/Slither to extract ABI info; works with the local **solc 0.8.37**; needs a harness contract. AGPL-3.0 license is a consideration if ever redistributed.
- **Practicality here.** Partial — the binary is drop-in, but real value requires harness design, and Windows support is undocumented/unsigned.
- **Recommendation: DEFER.** Revisit only if dynamic fuzzing is prioritized; if so, prefer the **signed** asset (Linux/Docker) or at minimum verify the Windows zip's SHA-256 against the release before use. Lower priority than the two pure-Node tools.

*Sources:* `https://github.com/crytic/echidna` (README install/features; API pushed 2026-10-15, AGPL-3.0); `https://api.github.com/repos/crytic/echidna/releases/latest` (v2.3.3, 2026-07-27, asset list + digests).

### 17.6 Mythril — **recovery options** given the blocked install (do not retry the pip path blindly)

**Observed blocker (from `action.md`, 2026-10-17 14:16–14:20 IST).** The single permitted install attempt (`N:\Auditor\tools\mythril\venv\Scripts\python.exe -m pip install --no-input mythril`, on the only interpreter, **Python 3.11.9**) failed (exit 1). The two hard failures were building wheels for **`blake2b-py` 0.4.0** (`error: linker 'link.exe' not found` — a Rust/maturin build) and **`pyethash` 0.1.27** (`error: Microsoft Visual C++ 14.0 or greater is required`). Docker is **absent**, so the official image path is unavailable.

**Refined root cause (correcting earlier notes).** The blocker is the **missing native toolchain**, not merely the Python version:

- **`pyethash` 0.1.27** — PyPI shows the package is **sdist-only for every release** (0.1.24–0.1.27, all `.tar.gz`; last upload 2015). There are **no wheels for any Python/OS**, so on Windows it **always** requires a C compiler + Python headers (MSVC). This is a **fundamental** blocker for a pure-pip Windows install — and it means *"just use Python 3.10"* does **not** by itself fix it.
- **`blake2b-py` 0.4.0** — the latest version, chosen because the dependency is **unpinned**. Its PyPI file list shows **no cp311 Windows wheel** (0.4.0 publishes a Windows wheel **only for cp312**). So on Python 3.11/Windows, pip must compile **0.4.0** from source → needs **Rust/cargo + MSVC `link.exe`**. (Older **0.2.2 / 0.3.0 / 0.3.1 / 0.3.2 do** publish cp311 Windows wheels, and 0.1.3 had cp36–cp39 Windows wheels.) pip does **not** automatically downgrade on a build failure, so pinning is required to avoid Rust. **Correction:** an earlier note said "`blake2b-py` wheels only up to cp38" — that was inaccurate; the precise issue is that **0.4.0 lacks a cp311 Windows wheel**.
- **Python version.** Mythril's README documents **Python 3.7–3.10**; PyPI classifiers list **3.6–3.10 only** (none for 3.11+), though the metadata field is `requires_python = >=3.7.0` (so pip will not pre-block 3.11 — it tries and then fails at the native builds). Installed: **3.11.9**.
- **Mythril build state.** PyPI **0.24.8** (uploaded **2024-03-27**); the `mythril` wheel itself builds fine (`mythril-0.24.8-py3-none-any.whl`); only its native deps fail. Repo (`ConsenSysDiligence/mythril`) last pushed **2026-04-27** (4,273 stars, MIT) — *repo* activity continues but there is **no release newer than 0.24.8 (2024-03)**.

**Officially supported install methods (from the official README/registries).**

1. **PyPI** — `pip3 install mythril`, documented for **Python 3.7–3.10**. Not Windows-friendly: `pyethash` has no wheels, so a C toolchain is required.
2. **Docker** — the badge/README point to **`mythril/myth`**. Docker Hub `mythril/myth` exists and is **active** (248,641 pulls; `last_updated` **2024-03-27**, matching 0.24.8).
3. **Docs** — the README links `mythril-classic.readthedocs.io`. In this pass the `/en/master/` and `/en/develop/` **installation pages returned 404**, i.e. the docs site is **stale**; the **README is the authoritative install source**.

**Recovery paths (ranked; no assumption that Python 3.10 or build tools are present).**

- **A. Docker (official; cleanest).** Provision Docker, then `docker pull mythril/myth` and run `myth analyze` in-container against a mounted temp copy. This avoids Python and the native builds entirely, and matches official method #2. **Blockers now:** Docker engine is not installed. **Next action if pursued:** install Docker, **pin the exact tag/digest** (image froze at 2024-03-27), run a bounded `myth analyze` on a temp copy only, and normalize the text output. Note: the image is **not** maintained past 0.24.8.
- **B. Native Windows build (no Docker).** Requires **all** of: (i) an isolated **Python ≤3.10** venv; (ii) **MSVC C++ Build Tools** (VS 2022 Build Tools, "Desktop development with C++", providing `link.exe` + Python headers) for **`pyethash`**; and (iii) a **Rust toolchain** (rustup + cargo, MSVC target) for **`blake2b-py` 0.4.0** — unless `blake2b-py==0.3.2` is **pinned** (it has a cp311 Windows wheel), which removes the Rust requirement but **not** the MSVC one (`pyethash` still needs it). **Blockers now:** no 3.10 interpreter and no build tools installed; heavy footprint.
- **C. "Just use Python 3.10" alone — insufficient.** Because `pyethash` ships **no wheels at all**, a C compiler is still required on Windows. This corrects the earlier assumption that the version gap was the sole cause.
- **D. Third-party/prebuilt wheels — not officially supported.** Not recommended.
- **E. Keep Mythril out of scope.** Given that Mythril is speculative symbolic execution needing per-run tuning and its only clean Windows path is **Docker**, deferring it is reasonable.

**Recommendation: DEFER (do not retry the pip install).** If Mythril is wanted on this machine, use the **official Docker image** (path A) after provisioning Docker; otherwise leave it listed as research-only. Do **not** re-run pip on Python 3.11, and do not install a Python 3.10 and expect it to succeed without **also** adding a C++ toolchain.

*Sources:* `https://pypi.org/project/mythril/` and `https://pypi.org/pypi/mythril/json` (v0.24.8, uploaded 2024-03-27, `requires_python >=3.7.0`, classifiers 3.6–3.10); `https://raw.githubusercontent.com/ConsenSysDiligence/mythril/develop/README.md` (PyPI for Python 3.7–3.10; Docker); `https://hub.docker.com/v2/repositories/mythril/myth/` (active, 248,641 pulls, last_updated 2024-03-27); `https://pypi.org/simple/pyethash/` and `https://pypi.org/pypi/pyethash/json` (sdist-only, all releases); `https://pypi.org/simple/blake2b-py/` (0.4.0 has no cp311-win wheel; 0.3.2 does); `https://mythril-classic.readthedocs.io/en/master/installation.html` + `.../en/develop/installation.html` (both 404 on 2026-10-17).

### 17.7 Recommendation summary and install order (Windows, isolated under `N:\Auditor\tools\<tool>\`)

Facts grounding the order: **Node 22.22.0 / npm 11.6.2 present** (satisfies Solhint's `node >=20`); **no Docker**; **no graphviz**; **Python 3.11.9 only**; **solc 0.8.37**; **Slither 0.11.6** already installed.

| Order | Tool | Verdict | Why (one line) |
|---|---|---|---|
| 1 | **Solhint** | **Install next** | Pure-Node, no native deps, active (6.2.5), MIT; fast lint that complements Slither. |
| 2 | **Surya** | **Install next** | Pure-Node structure/call-graph inspector, Apache-2.0; text/DOT works without graphviz. |
| 3 | **Echidna** | **Defer** | Very active + signed Linux/Docker builds, but Windows asset is undocumented/unsigned and it needs a property harness. |
| 4 | **Securify2** | **Avoid / defer** | Abandoned-ish; Python 3.7 + souffle + old solc pin + no Windows path; overlaps Slither. |
| 5 | **MythX** | **Avoid** | Hosted service **sunset**; account/API-key gated and uploads source (conflicts with local-only design). |
| 6 | **Mythril** | **Defer** | Official Windows path is effectively **Docker-only**; pip path is blocked by `pyethash`/`blake2b-py` native builds. |

**Isolation pattern for the two "install next" tools (documentation only — nothing was installed in this pass):** install each into its **own** directory under `tools\` using a **local npm prefix** (e.g. `npm install --prefix N:\Auditor\tools\solhint solhint@6.2.5` and `npm install --prefix N:\Auditor\tools\surya surya@0.4.13`), then invoke the local shim by **absolute path** (`tools\solhint\node_modules\.bin\solhint.cmd`, `tools\surya\node_modules\.bin\surya.cmd`). This keeps them out of the app source tree, off the global `PATH`, and free of global installs — consistent with the `tools\README.md` policy and the existing `tools\slither\venv\` precedent. Any future wiring into the app must go through an explicit, fixed-argv adapter (as Slither does), not a shell.

**Sources (all accessed 2026-10-17, Asia/Calcutta):** MythX — `https://mythx.io/`; `https://github.com/ConsenSysDiligence/mythx-cli`. Securify — `https://github.com/eth-sri/securify2`; `https://github.com/eth-sri/securify`. Solhint — `https://registry.npmjs.org/solhint/latest`; `https://github.com/protofire/solhint`; `https://protofire.github.io/solhint/docs/rules.html`. Surya — `https://registry.npmjs.org/surya/latest`; `https://github.com/ConsenSysDiligence/surya`. Echidna — `https://github.com/crytic/echidna`; `https://api.github.com/repos/crytic/echidna/releases/latest`. Mythril — README, PyPI (`mythril`, `pyethash`, `blake2b-py`), Docker Hub `mythril/myth`, and the (404) readthedocs install pages listed in §17.6. Repository status fields (`archived`, `pushed_at`, license) were read from the GitHub REST API on the same date.

**Note:** no tool was installed, downloaded, or executed in this pass; no application code, MCP behavior, `action.md` trap rules, or tool environment was changed; the canonical tracker remains `action.md`.
