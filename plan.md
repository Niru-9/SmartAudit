# Smart Contract Security Workbench — MVP Plan

> **Audience:** OpenCode is the coding agent; this is the build brief.
> **Hackathon window:** four days from the team's actual kickoff (planning began 2026-10-12).
> **Revision:** 2026-10-15 — environment re-verified; tool decisions refreshed; the two earlier four-day sections and the two acceptance sections were consolidated into one each; the architecture recommendation is now stated once and consistently (Python orchestrator + Vite/React SPA). Research re-verified 2026-10-15 in `research.md` (section 0). No application code was written in this pass. **2026-10-16:** tool order/scope revised after Phase 2a research + first installs (see `research.md` §12): solc 0.8.37 + Slither 0.11.6 installed locally; Solhint/Surya added as low-risk local adds; Echidna corrected to "optional later" (a native Windows zip now exists in v2.3.3); MythX (sunset, hosted) and Securify (deprecated) omitted.
> **Goal:** a local-first web tool that checks pasted Solidity with multiple existing analysis tools plus local Qwen, then presents one evidence-linked, combined report and safe remediation guidance.

## Environment reality (verified locally 2026-10-15)

Full evidence and source links are in `research.md` (section 0). The verified facts that constrain this plan:

- Windows 11 (build 26200), PowerShell 5.1. Node 22.22.0, npm 11.6.2. Python 3.11.9 via `py` (the bare `python` command is a broken Microsoft Store alias, and the Python `Scripts` folder is not on PATH, so console scripts must be called by absolute path).
- **Foundry 1.8.1 is already installed** (`forge`, `cast`, `anvil`, `chisel`, `solar` at `%USERPROFILE%\.foundry\bin`), just not on PATH. No install needed for the toy test.
- **WSL 2 is present but has no Linux distribution; Docker is not installed.** `winget` exists; `scoop`/`choco` do not.
- **Not installed:** `solc`, Slither, Mythril, Echidna, Aderyn, **z3**. No `~/.svm`, no `solc-select`.
- **Local model works:** Ollama 0.35.1 with `qwen2.5:3b` (3.1B, Q4_K_M) and `qwen3:4b` (4.0B, Q4_K_M); OpenAI-compatible endpoint at `http://localhost:11434`; OpenCode also exposes these as `ollama/qwen2.5:3b` and `ollama/qwen3:4b`.
- **Consequences:** Slither is installable on Python 3.11 but needs a hand-provided **Windows `solc`** because `solc-select` does not support Windows. Mythril documents Python 3.7-3.10 and Docker is absent, so treat it as **optional/at-risk**. Echidna and Aderyn both expect WSL/Docker, so **defer** both. SMTChecker needs `z3` and engine configuration, so **defer**. Foundry can be kept offline (`offline = true` + `solc = "<path>"`).
- Repository contains only `plan.md`, `action.md`, `research.md`, `reseach.md` (superseded draft). **No `AGENTS.md`**, no app source, no hidden config.

## Product scope

A developer pastes one Solidity contract, runs an audit, reviews findings and asks follow-up questions in a chat panel. The app runs whatever supported local tools are available, combines their output with Qwen's source-grounded review, and clearly identifies where each observation came from.

Deliver three working flows:

1. **Multi-tool audit:** Run Slither as the primary static analyzer. Use the Solidity compiler and/or Foundry for compilation and a small local proof-of-concept test. Add Mythril symbolic analysis only if it runs reliably in a bounded time; otherwise report it as unavailable. Aderyn/Echidna/SMTChecker are stretch options only. Report each tool's status, findings, and limitations.
2. **Ask and refine:** Chat with local Qwen about the submitted source and combined findings. Provide proposed safer code separately, explain changes and assumptions, and let the user copy it. Never silently overwrite source. If changed code is re-audited, show a fresh run; keep the prior report as run history only if simple.
3. **Attack-path walkthrough:** Explain one contained reentrancy scenario as a sequence of calls/state changes; demonstrate against an intentionally vulnerable local sample and compare with a safer version. Any runnable test stays local and uses toy contracts.

## Success criteria

In a short demo, a judge pastes a vulnerable withdrawal contract and sees:

- Slither output with source locations and tool provenance, and Mythril output only if it actually ran.
- A combined view that groups likely duplicate findings without hiding which tools reported them.
- Qwen's explanation, exploit preconditions, and remediation tied to code evidence.
- A safe local-only reentrancy walkthrough or test that contrasts vulnerable and fixed behavior.
- Accurate reporting of missing tools, compile failures, timeouts, partial results, and model failure.

The product must say this is automated triage, not a complete or certified security audit. No wallet, chain connection, RPC, key, deployment, signing, transaction broadcast, remote model, or live-target attack is in scope.

## Tool strategy (decisions 2026-10-15)

| Tool | Analysis type | MVP role (verified) | Plan for availability |
|---|---|---|---|
| Slither | Solidity AST/IR static analysis | **Primary, required** | Install `slither-analyzer` via `py -m pip` (or `uv`). Supply a Windows `solc` binary and pass `--solc <path>`. Use `--json` for the normalizer and `--checklist` for a human view; show setup guidance if absent. |
| solc | Compile and compiler diagnostics | **Required compile gate** + Slither/Foundry dependency | Place a Windows static binary at `N:\Auditor\tools\solc\solc.exe` (download once from `binaries.soliditylang.org/windows-amd64/`; latest is 0.8.37). Match the source `pragma`; never silently change it. `solcjs` is not a Slither substitute. |
| Foundry (`forge`) | Local unit, fuzz, invariant tests | **Included (already installed)** — one authored toy test | Call `forge.exe` by absolute path; fresh temp project; set `offline = true` and `solc = "<path>"` so no compiler is downloaded; local tests only; never `--fork-url`/RPC. `--symbolic` is out (needs z3). |
| Mythril | EVM symbolic execution | **Optional / best-effort; deferred by default** | PyPI documents Python 3.7-3.10 (machine is 3.11.9) and Docker is absent. Timebox a single install attempt on Day 3 only if core work is done; if it fails, report `unavailable`. `--execution-timeout`; source-file mode only. |
| Aderyn | Rust Solidity static analyzer | **Deferred** | Official Windows path requires WSL (no distro). Overlaps Slither. Re-evaluate later via `npm install @cyfrin/aderyn -g` on a Linux/WSL host. |
| Echidna | Property-based call-sequence fuzzer | **Optional later** | **Correction (2026-10-16):** the v2.3.3 release (2026-07-27) now ships a native **`echidna-2.3.3-x86_64-windows.zip`**, so the earlier "no native Windows build" note is outdated. Still deferred this phase (no install); prefer Foundry invariants unless Echidna's modes add value. No WSL/Docker to be added. |
| Solhint | Solidity linter (style + thin security rules) | **Phase 2b low-risk local add** | `npm install -g solhint` (npm **6.2.5**); `solhint --init` + `.solhint.json`; formatters include **json** and **sarif** for the combined report (`list-rules`, `stdin`, `--fix`). Local, npm present. |
| Surya | Structure/visualisation inspector (no vuln detection) | **Phase 2b low-risk local add** | `npm install -g surya` (**0.4.13**); commands `graph`/`describe`/`inheritance`/`ftrace`/`flatten`/`dependencies`; needs graphviz `dot` for images (**absent** here - tables still work). Repo moved to `ConsenSysDiligence/surya`. Local. |
| MythX | **Hosted** analysis-as-a-service | **Retired - omit** | Official site announces the **MythX sunset, effective March 31st** (subscriptions cancelled; components: Mythril open source, Harvey -> Diligence Fuzzing). Hosted + account/API key + source upload also violates the offline/no-exfiltration scope. **Do not use.** |
| Securify | Static analysis (v2, Datalog) | **Deprecated - omit** | v1 repo is explicitly `[DEPRECATED]` (last code push 2020); v2 last pushed 2025-05-25 and needs Docker/souffle/graphviz (none available). Overlaps Slither. Do not integrate. |
| SMTChecker | Compile-time formal checking | **Deferred / optional** | Needs an explicit engine + `z3` for native solc (z3 absent); Foundry docs describe it for macOS/Linux. Not a whole-contract auditor. |
| Local Qwen (Ollama) | Explanation / triage / remediation over source + evidence | **Required** | Already running; OpenAI-compatible endpoint. Never a deterministic scanner. |

Do not install every tool just to maximize the number of logos. Select a stable minimum: **Slither + a Windows solc + local Qwen**, add **Foundry** only for the targeted demonstration, and treat Mythril as optional. A missing analyzer must not prevent other analyzers or the UI from returning partial results.

## Combined report design

Keep raw per-tool output available for transparency. Normalize findings to fields such as: `id`, `title`, `severity`, `confidence`, `description`, `file`, `startLine`, `endLine`, `evidence`, `impact`, `preconditions`, `recommendation`, `sources[]`, `kind` (`tool` or `model`), and `status` (`confirmed`, `candidate`, `needs-context`).

- Tool findings retain the tool name, detector/check ID, original severity/confidence, and raw reference/trace when present.
- Correlate likely duplicates using detector/CWE/SWC identifiers and overlapping source ranges; merge presentation while retaining every source. Do not let Qwen erase, upgrade, downgrade, or fabricate deterministic tool findings.
- Qwen may explain, suggest remediation, identify a likely duplicate, and raise clearly labeled model-only hypotheses. Hypotheses need a quoted source span or function and explicit preconditions; unsupported concerns become review questions.
- Do not invent a numeric aggregate security score. Show per-tool completion and severity counts with the evidence provenance visible.
- A clean report means "no issues detected by the tools that completed," never "safe."

## Local Qwen skills

Keep small, versioned prompt assets in the repository under `prompts/`, selected
per task so a request never loads guidance it does not need. Phase 4 consolidated
these into **three project-owned knowledge modules** assembled by
`backend/knowledge.py`:

- **`solidity-secure-authoring`:** ask for intent, invariants, roles, compiler/library versions and trust assumptions; use careful checks-effects-interactions and access controls; handle external-call failures; favor established libraries; return proposed code separately with assumptions and explanation; do not invent protocol design decisions. *(used by the suggest/rewrite task)*
- **`solidity-audit-reporting`:** define scope; inspect externally reachable functions, permissions, state changes, value flows, external calls/callbacks and invariants; cite exact code evidence; report severity/confidence/impact/preconditions/fix/uncertainty; preserve analyzer provenance; separate confirmed tool output from hypotheses; identify missing architecture or economic context. *(used by review + chat)*
- **`solidity-tool-usage`:** how to read deterministic analyzer output (Slither 0.11.6 / solc 0.8.37), what each status means, and the rule that tool findings are evidence that must not be created, altered, or re-attributed. *(used by review + chat)*

Every assembled system prompt also ends with one shared guardrail block
(untrusted-data rule, tool-finding provenance, "triage not audit", never say
"safe"). These are curated prompts/checklists, **not model training or
certification**. Prefer original summaries with links and respect licenses; do
not paste large copyrighted reference texts into the module files. A `## Sources`
footer is kept for provenance but stripped before sending.

**Optional MCP bridge (Phase 4):** an optional, stdlib-only local MCP (Model
Context Protocol) server may expose `analyzer_status` and `run_slither` (the
latter delegates to the existing Slither adapter) plus read-only knowledge
resources. The model may *propose* a call, but it only runs after explicit user
approval, and the model never supplies source/paths/commands.


## Safety and execution boundaries

- Analyze pasted local source only; never fetch code or interact with a live or test network.
- Run tools through a backend **allowlist** with fixed executable/argument templates, a bounded timeout, output-size caps, and an isolated temporary project directory. **Never** execute commands or scripts suggested by Solidity source or model output.
- Treat pasted code/comments/chat as untrusted data; prompt injection inside comments must not change system instructions.
- Provide only explanatory attack paths and runnable tests against included toy contracts. No live targeting, exploit deployment, drain scripts, or transaction sequences for real addresses.
- Source and prompts stay on the local machine; use only the configured local model endpoint. Pre-place `solc` and set Foundry `offline = true` so no download happens at analysis time.

## UI direction and states

A compact security workbench: source editor on the left; combined findings and assistant conversation on the right; top bar with model status and Analyze. Findings show severity, confidence, source location, provenance, and expandable raw evidence. The design should feel like a focused developer tool, with readable contrast and a narrow-screen stacked layout.

Handle empty editor/sample, analyzing, no findings, findings, compiler failure, each analyzer unavailable/failure/timeout, partial results, model offline, invalid model response, unsupported source, copied patch, and re-audit. Preserve code after every error. Do not imply an analyzer ran when it did not.

## Architecture options and recommendation

Two options were evaluated against the actual environment (details in `research.md`).

**Option A — Node/TypeScript monolith.** Vite + React frontend; Express/Fastify backend on `127.0.0.1`; tools spawned via `child_process` with fixed arg templates, timeouts, output caps, isolated temp dirs; Ollama over the OpenAI-compatible HTTP API. Pros: one toolchain, Node/npm already present, simplest single-runtime story. Cons: Slither and any Mythril integration are Python tools parsed from Node.

**Option B — Python backend + Vite/React frontend (RECOMMENDED).** Same SPA frontend; **FastAPI + uvicorn** backend on `127.0.0.1` (already installed alongside `httpx`/`pydantic`) orchestrates analyzers via `asyncio.create_subprocess_exec` with the same fixed allowlist/timeouts/output caps, and calls Ollama over HTTP. Rationale: the analyzers are Python-native, the backend stack needs no new install, `asyncio` gives clean subprocess timeouts, and a Pydantic model cleanly defines the normalized finding schema. This **supersedes the earlier Node-only recommendation** in this file. If the team is strictly JS-only, Option A stays viable with Mythril fully optional.

**Deployment variant of B (used for the demo):** build the SPA once and have FastAPI serve the static files, so the demo runs one local process (plus the tool runner's child processes). Dev mode can keep Vite and uvicorn separate.

**Shared components (either option):**

- SPA: Solidity input (paste/sample), findings list, Qwen chat panel, per-tool status bar, proposed-patch panel that never overwrites source.
- Backend endpoints: `POST /analyze` (returns run + normalized report + per-tool status), `POST /chat` (Qwen over source + evidence), `GET /health` (model + tool availability).
- Tool runner: fixed allowlist; fresh isolated temp dir per run; wall-clock timeout; byte + line output caps; kill on timeout; cleanup; never runs a command from source or model output.
- Orchestrator: run independent analyzers in parallel where safe; always return partial results; each tool is independently optional.
- Sandbox: a fixed toy project (vulnerable + fixed reentrancy contracts) for the walkthrough.
- Two versioned prompt assets under `prompts/`.

**Normalized finding:**

```
Finding {
  id, title, severity (info|low|medium|high|critical), confidence (low|medium|high),
  description, impact, preconditions, recommendation, uncertainty,
  file, startLine, endLine, evidenceSpans[],
  cwe?, swc?,
  kind: "tool" | "model",
  status: "confirmed" | "candidate" | "needs-context",
  sources: [ { tool, toolVersion, runId, detectorId?, rawRef, severity, confidence, rawOutput? } ],
  groupKey?, groupedFindingIds?
}
```

**Duplicate grouping:** group only when a detector/CWE/SWC family matches **and** source ranges overlap; one card lists all reporting tools with expandable per-tool evidence. **Merging is presentation-only; every source is retained.** Qwen may suggest a duplicate, but the suggestion is labeled and never deletes a tool finding.

**Provenance distinction:** `kind:"tool"` is deterministic output that Qwen may explain but not alter; `kind:"model"` is a labeled hypothesis that must quote code and state preconditions, else it becomes a "review question".

**Safety:** fixed allowlist; never run a command from source/model output; no RPC/chain/keys/wallets/deploy/sign/broadcast; source and prompts stay local; model hypotheses labeled; no aggregate security score; a clean report means "nothing found by the tools that completed", never "safe".

## Four-day sequence (realistic)

### Day 1 — Inspect, scaffold, schema
- **Start:** log a timestamped entry in `action.md`.
- **Env check:** confirm Node/Python/Ollama; confirm tool presence (`slither`, `myth`, `solc`, `forge`) and record versions. Place the Windows `solc.exe` in `N:\Auditor\tools\solc` and verify it runs.
- **Scaffold:** Vite + React frontend and FastAPI backend (local-only). Add a health/model check to Ollama (`http://localhost:11434`).
- **Schema:** define the normalized finding format with the provenance array; create `prompts/solidity-secure-authoring.md` and `prompts/solidity-audit-reporting.md`.
- **Complete:** a skeleton that accepts pasted Solidity and returns a normalized structure plus per-tool status.

### Day 2 — Slither integration + normalization
- **Tool runner:** implement the allowlist runner (fixed paths/args, timeout, output caps, isolated temp dirs, cleanup).
- **Slither:** first integration; install `slither-analyzer`, wire `--json` output, retain raw output, and handle the compile gate via the placed `solc`.
- **Grouping:** duplicate correlation by overlapping range + detector/CWE; preserve all sources; mark tool vs model.
- **Status:** per-tool statuses (ok / failed / timeout / unavailable); partial results supported.
- **Complete:** paste a sample → Slither findings with full provenance.

### Day 3 — Optional tools, chat, walkthrough
- **Mythril (optional):** probe only if a clean install works; enforce a strict timeout; if not practical, document the blocker and skip. Never block the audit.
- **Qwen chat:** integrate the audit-reporting prompt; require evidence citations; label model hypotheses distinctly.
- **Toy contracts:** create `sandbox/Vulnerable.sol` (reentrancy) and `sandbox/Fixed.sol` (checks-effects-interactions). Add the local-only explanation/walkthrough.
- **Foundry test:** with forge available, add one minimal local test (`offline = true`, `solc` pinned) that contrasts the two; otherwise a conceptual walkthrough.
- **Complete:** combined report + chat explanations + safe walkthrough.

### Day 4 — Harden and demo
- **States:** exercise empty / no-findings / compiler failure / timeout / unavailable / model-offline / partial results.
- **Safety audit:** verify no RPC/keys/wallets/deploy/sign/broadcast; allowlist enforced; Foundry offline; no runtime downloads.
- **Docs:** setup instructions, supported tools, limitations, and a 2-minute demo script.
- **Stabilize:** fix only demo-blocking defects; update `action.md` with completion times.
- **Complete:** demo-ready MVP meeting the acceptance checklist.

## Acceptance checklist (demo success criteria)

- [ ] Paste one Solidity source file and run analysis without wallet/network/RPC connection.
- [ ] Local Qwen status and Slither status shown; Mythril status shown **only** if it actually ran.
- [ ] Multiple available analysis sources feed one report with source provenance intact.
- [ ] Findings expose severity, confidence, evidence/location, impact, preconditions, remediation and uncertainty.
- [ ] Tool detections and model hypotheses are visibly different.
- [ ] Duplicate presentation does not discard original tool evidence.
- [ ] Follow-up chat explains results and proposes code **without overwriting source**.
- [ ] One safe local reentrancy walkthrough contrasts vulnerable and fixed sample.
- [ ] Missing tool, timeout, compile error, partial results, model-offline and no-findings states work.
- [ ] No RPC, chain, keys, signing, broadcast, or remote model integration exists.
- [ ] No compiler download occurs at analysis time (solc pre-placed, Foundry offline).
- [ ] Setup and demo instructions are documented.

## Demo narrative

"Paste this toy vault. Slither reports an external-call ordering risk; if Mythril ran, it also reports a candidate. The report groups related evidence while showing which tools found it. Ask Qwen to explain the callback preconditions and propose a checks-effects-interactions fix. The local sample demonstrates the before/after behavior. Here are the tools that ran, the ones unavailable, and the limits of the result."

## Out of scope

Production assurance; chain/address scans (original MVP — see the update below); remote dependencies downloaded at analysis time; wallet/account support; on-chain attack simulation; automated in-place edits; economic risk scoring; advanced multi-contract/DeFi analysis; model fine-tuning; persistence; PDF export unless trivial after core flows.

*(Update 2026-10-17: a **read-only** deployed-address review is now planned as **Phase 6** above. The original pasted-source MVP is unchanged. Still permanently out of scope: writing to, upgrading, or transacting with any deployed contract, and any signing/key/wallet/broadcast capability.)*

## Build order for OpenCode

1. Inspect the target folder and existing instructions; do not assume framework, runtime, or endpoint.
2. Send a concise inventory: OS/shell, project stack, local Qwen API shape, and each tool/version present or missing.
3. Build the end-to-end report path around Slither and Qwen first; add Mythril only after confirming a local source-only run works. Keep each integration optional so one failed tool cannot fail the audit.
4. Add UI, refinement, and the safe scenario; polish after the core demo is reliable.
5. Update `action.md` before and after every meaningful task. Keep `plan.md` updated if scope changes.

## Phase 6 — Staged plan: read-only deployed-address review (2026-10-17)

**Status:** Phases **6.1 and 6.2 implemented** 2026-10-17. **6.1:** chain selector + contract-address input + offline EIP-55 validation + explicit default-off consent. **6.2:** one explicit user action (`POST /api/deployed/lookup`) performs a **consent-gated, read-only** retrieval of a deployed contract's **verified source + basic metadata** via **Sourcify** (allowlisted URL, bounded timeout/size, honest status; optional server-side-only key never logged/returned). **Plan adjustment (2026-10-17, user decision; supersedes the earlier mainnet-only note):** the supported networks are **Ethereum mainnet (1) plus the Ethereum testnets Sourcify officially supports — Sepolia (11155111), Holesky (17000), Hoodi (560048)**; **Base, Arbitrum, and every other non-Ethereum network are excluded**, as are deprecated testnets Sourcify no longer supports (Goerli/Ropsten/Rinkeby). Membership was validated against Sourcify's official *Supported Chains* documentation + chain list (checked 2026-10-17). Selection stays **unselected by default** (no silent fallback to any chain), and the selected chain ID is forwarded to the backend and into the Sourcify request path. This resolves `research.md` §16.10 **Q2**. Phases **6.3–6.6 remain plan only** (source retrieval is shown for review but **not** yet fed into the analyzer/model, no proxy/UUPS detection, no drafting). No MCP capability change, no installs. Supporting evidence: `research.md` §16. The app was restarted (single instance) to serve the new code; no duplicate server was started.

**Goal:** optionally let the user point the workbench at a **deployed contract address, read-only**, so the *same* evidence-preserving combined report (deterministic analyzer output + separate local-Qwen guidance) can be produced from publicly verified source/metadata — without ever writing to, upgrading, or transacting with any contract.

**Explicit non-goals:** implementing/simulating an upgrade; sending a transaction; deploy; signing; keys; wallets; modifying any deployed contract; performing an outbound call **without explicit consent**; changing current MCP behavior; installing new tools/libraries in this phase.

**Open inputs this plan depends on** (from `research.md` §16.10): the meaning of **"EPS"**; the **preferred first network/chain ID** (**resolved 2026-10-17**: Ethereum mainnet + the Sourcify-supported Ethereum testnets — see status above); which **source provider** and whether an **explorer API key** is available; whether a **read-only RPC call** is permitted for proxy detection. Until answered, only decision-independent phases proceed.

### Staged phases

**Phase 6.0 — Decisions & guardrails (no code).**
Record: chain-first input (address is meaningless without a chain ID); provider(s); key handling (user-provided, never bundled/logged); RPC read consent; EPS resolution. *Acceptance:* decisions documented here and in `research.md`.

**Phase 6.1 — Chain + address input, consent gate, validation (no network).**
Add a chain selector, an address field, and an **explicit consent checkbox default-off**; parse/normalize per **EIP-55**; refuse invalid; warn on mixed-case checksum mismatch; introduce a provider **interface** (with an offline stub) so nothing is fetched yet. *Acceptance:* invalid address refused; nothing happens without consent; existing pasted-source flow untouched.

**Phase 6.2 — Read-only source retrieval behind consent (MVP crux).**
New backend module (name TBD) implementing the provider interface for one provider first (**Sourcify**, typically no key, then optionally a Blockscout instance or **Etherscan v2** with a user key). Bounded timeout; provider + status returned honestly; **unverified/no-source** returns a clear status and never fabricated source; `429`/over-limit is a status. *Acceptance:* verified address → source + provider/provenance; unverified → clear refusal; no key logged; no arbitrary URL (allowlist).

**Phase 6.3 — Reuse the existing review on fetched source (MVP completion).**
Feed the retrieved source into the *existing* pipeline (solc compile gate + Slither 0.11.6; local Qwen explanation) exactly as pasted source is treated, as an **explicit** action (never automatic). Keep `kind:"tool"` vs Qwen guidance separate; never say "safe". Because the local adapter analyzes a single source unit, **multi-file** verified source is marked **unsupported and not analyzed** (no partial run). *Acceptance:* retrieved source yields the same normalized report shape and provenance as a paste; compile failure/timeout/unavailable handled; multi-file/incomplete/non-Solidity refused with a plain reason.

**Phase 6.4 — Proxy/beacon/admin detection and the "same address" explanation (next).**
Read-only ERC-1967 slot reads (only with RPC consent) and/or explorer "Proxy" metadata; report `UUPS / transparent / beacon / ERC-1167 / unknown / none` with uncertainty. Explain faithfully that *same-address* logic change needs an **already-deployed compatible proxy + valid authority**, and that upgrade tooling needs a project + signer we do not touch. *Acceptance:* detection is evidence-based and uncertain; no claim that an arbitrary contract is upgradeable.

**Phase 6.5 — Versioned Qwen skill for deployed review.**
Add a versioned knowledge module (e.g. `solidity-deployed-review`) that cites evidence (chain, address, time, source URL, provider), separates deterministic findings from hypotheses, and admits uncertainty. Record that prompt/skill files give context and **do not** train/fine-tune. *Acceptance:* module versioned, cited, assembled per task; no model training.

**Phase 6.6 — Optional MCP evolution (separate, later).**
Add the resolve-source capability as a **read-only, consent-gated** MCP tool/resource only if requested; keep the existing read-only server and approval-gated bridge unchanged; retain stdio; no write/sign/broadcast capability. *Acceptance:* consent per use; user-supplied chain/address only; no regression to existing MCP tests.

### MVP cut and acceptance criteria

Implement **6.0–6.3**: accept a chain + address with consent, look up a **public verified** source read-only, and run the **existing** review on it with the same provenance and disclaimers.

- [x] Address accepted only with a chain ID and explicit consent. *(6.1 + 6.2)*
- [x] Verified source retrieved read-only; unverified handled **without** fabrication. *(6.2)*
- [x] Outbound calls are allowlisted, bounded, and consented; no API key is bundled or logged. *(6.2)*
- [x] Same provenance-preserving report; analyzer findings distinct from Qwen guidance; never "safe". *(6.3 — analysis is an explicit action on the retrieved source; single-file only)*
- [x] No download at analysis time; existing tests pass; existing MCP unchanged. *(6.3)*

> **6.3 scope note:** the local analyzer analyzes a single Solidity source unit compiled with the local solc. A **multi-file** verified contract is therefore marked **unsupported in this phase and not analyzed** — no silent single-file partial run and no partial result presented as a full contract audit. Incomplete/truncated and non-Solidity source are refused the same way.

### Risks / unknowns

Provider availability and rate limits; whether a key exists; whether a read-only RPC is permitted; proxy detection accuracy on legacy/odd contracts; verifier-vs-onchain bytecode drift. Each maps to an open question in `research.md` §16.10.

### Recommended next single implementation task

**Phase 6.4 — proxy/beacon/admin detection and the “same address” explanation.** Read-only ERC-1967 slot reads (only with RPC consent) and/or explorer “Proxy” metadata; report `UUPS / transparent / beacon / ERC-1167 / unknown / none` with explicit uncertainty, and explain faithfully that *same-address* logic change needs an **already-deployed compatible proxy + valid authority** (upgrade tooling needs a project + signer we do not touch). Rationale: 6.0–6.3 (consent + validation, read-only verified-source retrieval, and explicit analysis of the retrieved single-file source) are implemented, so the remaining MVP-adjacent gap is evidence-based proxy detection — while never claiming an arbitrary contract is upgradeable.

## Revision history

- **2026-10-17 (Phase 6.3 implemented):** added an explicit **“Analyze retrieved source”** action to the deployed panel that feeds the **verified source retrieved by the current lookup** into the **existing** local pipeline (solc-compile gate + Slither 0.11.6; local Qwen *explanation* only) exactly as pasted source is treated. New backend module `backend/deployed_analysis.py` + `POST /api/deployed/analyze-source` (offline: no lookup, no model); it re-validates the address/chain offline and **refuses** unverified/empty, truncated (incomplete), non-Solidity, and **multi-file** source. **Per the user's explicit decision, multi-file verified source is marked unsupported in this phase and is NOT analyzed** — no silent single-file partial run and no partial result presented as a full contract audit (the adapter analyzes one source unit only; a single-file analysis proceeds **only** when the verified source truly contains exactly one file). Nothing runs on lookup completion, address change, network change, or result load; retrieved-source analysis is cleared/invalidated on any of those. `kind:"tool"` findings remain authoritative and Qwen can only explain an existing finding (never create/alter severity/confidence/location). No proxy/UUPS, drafting, wallet/sign/tx/deploy/modify, new analyzer, or new dependency; chain selection, consent, lookup limits, safe rendering, the pasted-code flow, MCP approval behavior, and report export are unchanged. Focused tests: `tests/test_deployed_analysis.py` (incl. a live adapter single-file smoke); the stale Phase 6.1 "no source route" assertion was updated to a **deployed-route allowlist** guard that still forbids unexpected contract-fetch routes. Verified live against the running app (Sepolia single-file → `ok`; mainnet WETH9 → honest `compiler_error`; multi-file → refused) and in a real browser (no auto-run; one run per explicit click; stale clearing). Detail: `action.md`.

- **2026-10-17 (deployed lookup extended to Ethereum networks):** followed up Phase 6.2 to support **Ethereum mainnet + the Ethereum testnets Sourcify officially supports** (Sepolia 11155111, Holesky 17000, Hoodi 560048) per the user's network decision, and **excluded Base, Arbitrum, and all other non-Ethereum networks** plus deprecated testnets Sourcify no longer supports (Goerli/Ropsten/Rinkeby, `supported: false`). Membership validated against Sourcify's official *Supported Chains* doc + chain list (`https://docs.sourcify.dev/docs/chains/`, `GET https://sourcify.dev/server/chains`), checked 2026-10-17. Selection remains **unselected by default** (no silent fallback) and the chosen chain ID is forwarded into the Sourcify request path; the UI now reads the supported set from `/api/deployed/config` instead of gating on a hard-coded mainnet. Read-only + consent + single-click lookup, the pasted-code flow, and MCP approval behavior are unchanged; no new dependency. Focused tests extended (`tests/test_deployed_lookup.py`, `tests/test_deployed_review.py`). Detail: `action.md`.

- **2026-10-17 (Phase 6.2 implemented):** implemented the consent-gated, read-only verified-source lookup in a new backend module `backend/deployed_source.py` (Sourcify v2 provider behind the existing `SourceProvider` face), the `POST /api/deployed/lookup` route, and the panel's explicit **Retrieve & review** button. **Plan adjustment:** supported network fixed to **Ethereum mainnet only (chain ID 1)** per user decision (removes Sepolia/Base/Arbitrum; resolves `research.md` §16.10 **Q2**). No analyzer/model run on retrieved source (that is Phase 6.3); no proxy/UUPS; no drafting; no MCP change; no new dependency (`httpx` already present). Focused offline tests: `tests/test_deployed_lookup.py`. Detail: `action.md`.

- **2026-10-17 (Phase 6 research + plan):** added the staged, read-only deployed-address review plan above; recorded the security/threat model and open questions in `research.md` §16. **No implementation**, no MCP capability change, no app code change, no installs; `reseach.md` untouched. Recommended next task: Phase 6.1 (chain + address input with consent and EIP-55 validation, no network).

- **2026-10-17 (Phase 4):** consolidated the prompt assets into three versioned, project-owned knowledge modules (`secure-authoring`, `audit-reporting`, `tool-usage`) routed per task (review/chat vs suggest) and assembled in `backend/knowledge.py`; documented an optional, stdlib-only local **MCP bridge** (tools `analyzer_status` + `run_slither` delegating to the existing Slither adapter; read-only knowledge resources; approval-gated). Phase order and the recommended architecture are unchanged — the MCP bridge is an additive component. Detail: `research.md` §14.
- **2026-10-16 (Phase 2a):** researched all nine named tools from primary sources and installed the first local tools. Added Solhint/Surya rows (low-risk local adds), corrected the Echidna row (native Windows zip available since v2.3.3), and added MythX (sunset/hosted - omit) and Securify (deprecated - omit) rows. Detail: `research.md` §12. Qwen seed notes: `notes/qwen-knowledge-notes.md`. Bench notes: `tools/README.md`. No app code changed.
- **2026-10-15 (this pass):** re-verified environment and tool requirements (no code, no installs); refreshed the tool-strategy table; consolidated duplicate four-day and acceptance sections into one each; fixed the architecture recommendation to Option B (Python orchestrator + Vite/React SPA) consistently; added the Foundry `offline`/`solc` note and the solc pre-placement step. Source detail in `research.md` section 0.
- **2026-10-12:** initial multi-tool plan, provenance-preserving combined report, optional-tool setup strategy.
