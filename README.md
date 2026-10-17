# Solidity Security Tools Bench — Qwen review + local Slither & Solhint (+ optional local MCP)

Local-first workbench. You paste Solidity, get an AI-assisted review from a
**local Qwen** model (Ollama), run the **local Slither** analyzer and the
**local Solhint** linter (each in its own panel), ask follow-up
questions, and see suggested safer code in a separate panel that **never**
overwrites your source. An optional, **local-only MCP bridge** lets the model
*propose* a tool call that runs **only after you approve it**. A second,
**read-only** local MCP server lets an external client read the analyzer status
and the **current** findings — with no run/write capability.

**Important:** This is automated triage only. Model output may be incomplete or
incorrect, and tool findings are partial — this is **not** a complete or
certified security audit. Nothing here compiles, deploys, signs, or contacts a
blockchain. No wallet/RPC/keys/deploy/sign code exists, and nothing is sent off
this machine.

## 1. Prerequisites

- **Ollama** running at `http://localhost:11434` with `qwen2.5:3b` and/or
  `qwen3:4b` (this project was last verified on Ollama **0.40.5**).
- **Python 3.11.9** via `py` (note: bare `python` may be a broken Store alias;
  Python `Scripts` may be off PATH).
- **Node.js v20+** on `PATH` — required only to run the local **Solhint** linter
  (verified on Node **22.22.0**).
- **Local analyzers** already placed under `tools/`:
  - Slither **0.11.6** — `tools\slither\venv\Scripts\slither.exe`
  - solc **0.8.37** — `tools\solc\solc.exe`
  - Solhint **6.2.5** — `tools\solhint\node_modules\solhint\solhint.js`, launched
    as `node <solhint.js> <file>` with the bundled `solhint-recommended` ruleset
    (local npm prefix install; no global install, no app dependency)

Check the model:
```powershell
curl http://localhost:11434/api/version
curl http://localhost:11434/api/tags
```

## 2. Start the app

From `N:\Auditor`:
```powershell
.\run.ps1
# or: .\run.ps1 -Port 8001   /   .\run.ps1 -Port 8001 -BindHost 127.0.0.1
```
Starts `uvicorn backend.main:app` on `127.0.0.1:8000` and serves the static
frontend at `/`. (The bind parameter is `-BindHost`, not `-Host`, because `$Host`
is read-only in PowerShell.) Open http://127.0.0.1:8000

## 3. What you can do

1. **Paste Solidity** or click **Load sample**.
2. **Review with Qwen** — explanation-only review with code evidence,
   preconditions/impact, severity/uncertainty, and remediation notes.
3. **Run Slither** — the local analyzer runs on a temp copy; findings show
   detector, severity, confidence, location, and raw (path-redacted) output.
4. **Run Solhint** — the local linter runs on a temp copy and shows Solidity
   lint/style/security-rule findings (rule id, severity, location) with raw
   (path-redacted) output. Slither and Solhint render in **separate** panels and
   are never merged; each finding is attributed to the tool that produced it, and
   each run reports its own honest status (`ok` / `no findings` / `unavailable` /
   `skipped` / `error`). A clean Solhint run is **not** a safety statement.
5. **Export report (Markdown)** — download the current report: a per-tool
   **run summary** (status + finding counts), the deterministic findings grouped
   **by tool**, AI explanations **separately**, and the local-Qwen review as its
   own clearly-labelled section, plus source provenance/scope and a
   human-review/limitations disclaimer. Secrets, environment values, local
   machine paths, and unsafe raw process output are excluded.
6. **Ask follow-ups** — chat with the source + recent history as context.
7. **Suggest safer code** — notes + one `solidity` block in a separate panel
   (never applied automatically).
8. **(Optional) Allow approved local tools (MCP)** — tick the checkbox in the
   chat panel. When the model *proposes* a tool (e.g. run Slither), nothing runs
   until you click **Approve**. You can always **Dismiss**.
9. **Deployed contract (read-only verified source, Phase 6.3)** — pick a
   network (**Ethereum mainnet or a Sourcify-supported Ethereum testnet** —
   Sepolia, Holesky, Hoodi; **nothing is selected by default** and Base,
   Arbitrum, and other non-Ethereum networks are not offered), paste a 20-byte
   EVM address (validated per EIP-55, lowercase/uppercase accepted), and tick
   the **default-off** consent box. Then click **Retrieve & review** to fetch
   the contract's **public verified source + basic metadata** from **Sourcify**
   and display it. Nothing is fetched until that explicit click with consent
   ticked — typing, choosing a network, toggling consent, and page load perform
   no lookup (they only run local EIP-55 validation), and retrieving the source
   runs **no analyzer and no model**. Read-only: no
   wallet/keys/sign/send/deploy/modify. If source is
   unverified/unavailable/rate-limited, the panel says so plainly and never
   fabricates source. When the verified source is a **single complete Solidity
   file**, an **Analyze retrieved source** button becomes available; clicking it
   runs the **same local Slither and Solhint** used for pasted code on *that*
   retrieved source (Slither and Solhint results stay separate and attributed),
   and each `kind:"tool"` finding can be explained by local Qwen (Qwen
   only explains an existing finding — it never creates or changes one). Analysis
   runs **only** on your explicit click — never on lookup completion, address
   change, network change, or result load — and its results are cleared when the
   address or network changes. **Multi-file** verified contracts are **not**
   analyzed in this phase (the analyzers work on a single Solidity source unit);
   the panel says so plainly instead of silently analyzing one file or presenting
   a partial run. Truncated/incomplete and non-Solidity source are refused the
   same way.

## 4. API endpoints

- `GET  /api/health` — model status, models, real tool status, **knowledge
  modules**, and the MCP descriptor.
- `GET  /api/models` — available model IDs.
- `POST /api/review` — `{source, model?}` → review text.
- `POST /api/suggest` — `{source, model?, review?}` → notes + optional `suggested_code`.
- `POST /api/chat` — `{source, question, history?, model?, tools_enabled?}` →
  reply, or a `tool_proposal` when `tools_enabled` is true and the model asks for
  a tool (**nothing runs**).
- `POST /api/tools/run` — `{source, question, history?, model?, tool}` → runs an
  **approved** allowlisted tool (`run_slither` | `analyzer_status`) and returns
  the tool result + a grounded model reply.
- `POST /api/slither` — `{source}` → the direct analyzer report (unchanged;
  independent of the MCP flow).
- `POST /api/solhint` — `{source}` → the direct **Solhint** linter report
  (added 2026-10-17). Independent of the Slither run; every finding is
  attributed to `solhint` and is never merged with another tool's findings.
- `POST /api/analyze` — `{source, tools?}` → run each available local analyzer
  **separately** (Slither and Solhint), one run each, with its own status,
  version, and findings. `tools` optionally restricts the run; any known
  analyzer not listed is reported `skipped` (never implied to have run).
- `POST /api/export-report` — the downloadable Markdown report: a per-tool run
  summary and findings grouped **by tool**, AI explanations **separately**, and
  the separate local-Qwen review, with source provenance and a
  human-review/limitations disclaimer. No secrets, environment values, or local
  paths are included.
- `GET  /api/mcp/status` — live probe of the local MCP server (tools + resources).
- `GET  /api/deployed/config` — Phase 6.2: the chain list (**Ethereum mainnet
  plus the Sourcify-supported Ethereum testnets** — Sepolia, Holesky, Hoodi;
  no non-Ethereum network), consent text, and read-only notice
  (`lookup_available: true`; the network stays unselected by default).
- `POST /api/deployed/validate-address` — `{address, chain_id?, consent?}` →
  **offline** 20-byte hex + EIP-55 validation (normalized + checksummed form).
  It never fetches a contract; `lookup_performed` is always `false`.
- `POST /api/deployed/lookup` — `{address, chain_id?, consent}` → the
  **consent-gated, read-only** verified-source lookup. It performs **no** network
  request unless `consent` is `true` **and** `chain_id` is a supported Ethereum
  network (mainnet `1`, Sepolia `11155111`, Holesky `17000`, Hoodi `560048` —
  never chosen implicitly) **and** the address is valid; otherwise it returns a
  refusal status (`consent_required` / `unsupported_chain` / `invalid_address`,
  and the message names the supported networks rather than switching to one).
  On success it returns the verified source and metadata from **Sourcify** with
  an honest `status` (`verified` | `not_verified` | `rate_limited` | `timeout` |
  `provider_error` | `too_large`). This is the **only** endpoint that makes an
  outbound request; it is bounded by a timeout and a response-size cap, uses an
  allowlisted provider URL, and never carries a provider key.
- `POST /api/deployed/analyze-source` — `{address, chain_id?, sources,
  sources_truncated?, language?}` → Phase 6.3: run the **existing** local
  analyzer on the **retrieved** verified source of the current lookup. It is
  reached only from the explicit **Analyze retrieved source** button, performs
  **no** network request, and runs **no** model. It re-validates the
  address/chain **offline** and returns `allowed: true` with the unchanged
  normalized analyzer report **only** for a single, complete (non-truncated),
  non-empty **Solidity** source file; otherwise `allowed: false` with a plain
  `reason` and `analyzer: null` — including **multi-file** verified source,
  which is marked unsupported in this phase and **not** analyzed (no partial
  run).

Phase 6.2 configuration (server-side environment only):

| Variable | Default | Purpose |
|---|---|---|
| `AUDITOR_SOURCIFY_BASE_URL` | `https://sourcify.dev/server` | Allowlisted provider base URL (fixed; never user-supplied). |
| `AUDITOR_SOURCIFY_API_KEY` | *(empty)* | **Optional** provider key. Sourcify reads need no key; if set it is sent as a bearer header and is **never logged or returned**. |
| `AUDITOR_DEPLOYED_LOOKUP_TIMEOUT` | `20` | Per-lookup wall-clock timeout (seconds). |
| `AUDITOR_DEPLOYED_MAX_RESPONSE_BYTES` | `524288` | Provider response byte cap. |
| `AUDITOR_DEPLOYED_MAX_SOURCE_BYTES` | `262144` | Budget for verified source returned to the UI. |

All model responses include a `notice` stating AI-assisted triage and no audit
completeness.

## 5. Knowledge modules (Goal A)

Versioned, project-owned guidance under `prompts/`, assembled by
`backend/knowledge.py` and routed per task (so no request loads unnecessary
text):

| Module | Version | Used by |
|---|---|---|
| `prompts/solidity-secure-authoring.md` | 1.1.0 | suggest |
| `prompts/solidity-audit-reporting.md` | 1.1.0 | review, chat |
| `prompts/solidity-tool-usage.md` | 1.0.0 | review, chat |

Each file has a metadata header, a model-facing body between
`<!-- prompt:begin -->` / `<!-- prompt:end -->`, and a `## Sources` footer that
is **cited but stripped** before sending. These are guidance prompts — **no
model fine-tuning or training happens.**

## 6. Local MCP bridge (Goal B)

A **stdlib-only** MCP server (`backend/mcp_server.py`) speaking newline-delimited
JSON-RPC 2.0 over stdio, driven by `backend/mcp_client.py`.

- Tools: `analyzer_status` (read-only availability) and `run_slither` (delegates
  to the existing `slither_adapter.run_slither`).
- Resources: `auditor://knowledge/{secure-authoring,audit-reporting,tool-usage}`.
- Both tools take **no arguments**: the model never supplies code, a path, or a
  command. The source under review is handed to the server out-of-band via a temp
  file (bounded; always cleaned up).
- The official `mcp` SDK is **not** installed (it requires `pydantic>=2.12.0` /
  `httpx2`, which would risk the app). See `research.md` §14.

## 7. Read-only MCP server (current analysis)

A second, **outward-facing, read-only** MCP server (`backend/mcp_auditor.py`)
lets an external MCP client read the app's analyzer status and the **current**
audit findings. It is separate from the approval-gated bridge in §6 and exposes
**no** run/write/edit/deploy/wallet/sign/transaction capability: both tools take
**no arguments**, and the store it reads holds analyzer output only — never
model/Qwen text, raw tool output (`raw_output`/`detail`), or filesystem paths.

Start it (stdio, local only — no socket, no hosted service):

```powershell
py -m backend.mcp_auditor
```

It speaks newline-delimited JSON-RPC 2.0 over stdio (protocol `2024-11-05`).

- Tools (**no arguments**):
  - `analyzer_status` — availability + version of the app's analyzers (Slither,
    solc); everything else is reported `not_integrated`. This read-only MCP
    surface is **unchanged** by the Solhint integration: Solhint runs in the app
    for the paste and deployed-source flows but is deliberately **not** exposed
    over MCP, so no new MCP capability is added.
  - `audit_findings` — the findings of the **most recent** analysis (direct
    `POST /api/slither` or the approved MCP path), or a clear
    **"no results available"** response when nothing has run yet.
- Resources:
  - `auditor://status/analyzers` — the same status payload.
  - `auditor://findings/current` — the same findings payload.

The app records the current analysis to `AUDITOR_MCP_STATE_FILE` (default
`<system temp>\auditor-work\mcp-current-analysis.json`) after each run; the
read-only server only ever reads it. Findings are labeled as **deterministic
analyzer output** and stay distinct from any model text.

Generic MCP client config (placeholders — adapt the keys to your client's
schema; nothing here changes your global OpenCode/other config):

```json
{
  "mcpServers": {
    "auditor-readonly": {
      "command": "py",
      "args": ["-m", "backend.mcp_auditor"],
      "cwd": "<absolute path to the Auditor repo>"
    }
  }
}
```

> This surface is **read-only by design**. To run Slither *from the model*, use
> the approval-gated bridge in §6 — nothing runs without an explicit **Approve**.

## 8. Boundaries

- **Local-first by default.** Only `http://localhost:11434` and local `tools/`
  executables are used. No hosted model, no source exfiltration. The single
  deliberate exception is the Phase 6.2 **read-only** Sourcify lookup (below),
  which occurs only on explicit user action with consent.
- **No blockchain writes.** No RPC/wallet/keys/deploy/sign/transaction/account/DB.
  The Phase 6.2 lookup connects **no wallet**, **signs nothing**, **sends nothing**,
  and **modifies nothing**; it reads only a contract's **public verified source**
  from the allowlisted provider. The Phase 6.3 analysis is fully **offline** — it
  never fetches anything (the client posts the retrieved source back) and it is an
  explicit action, not an automatic one.
- **Deployed-address review is read-only and consent-gated (Phase 6.2–6.3).** The
  address panel validates input offline; the verified-source lookup runs only via
  the explicit **Retrieve & review** button with the **default-off** consent box
  ticked. Only **Ethereum networks** are offered — mainnet and the Ethereum
  testnets Sourcify officially supports (Sepolia, Holesky, Hoodi); Base,
  Arbitrum, and other non-Ethereum networks are excluded, no network is selected
  by default, and the chosen chain ID is sent to the backend and used in the
  provider request. The provider base URL is fixed configuration (never
  user-supplied, so
  no arbitrary URL/SSRF) and the response is time- and size-bounded. **Analysis of
  the retrieved source is a separate explicit click** and reuses the existing
  local Slither; `kind:"tool"` findings stay authoritative and local Qwen may
  **only explain** an existing finding (it can never create or change a finding,
  its severity, confidence, or location). Nothing runs on lookup completion,
  address change, network change, or result load, and retrieved-source analysis is
  cleared when the address/network changes. **Multi-file** verified source is
  **not** analyzed in this phase (marked unsupported — no silent single-file
  partial run and no partial result presented as a full audit); incomplete/
  truncated and non-Solidity source are refused the same way. There is
  still **no proxy/UUPS analysis, no code drafting, and no analyzer auto-run**.
  An optional provider key is read **only** from server-side environment
  (`AUDITOR_SOURCIFY_API_KEY`); Sourcify reads need no key, and a configured key
  is never logged or returned. Unverified/unavailable/rate-limited outcomes are
  reported plainly and never fabricated.
- **Allowlisted tool execution.** Fixed executable + fixed argv + timeout +
  output caps + isolated temp dir + cleanup. Operator commands from source or
  model output are never executed.
- **Provenance.** Deterministic tool findings are `kind:"tool"` and are never
  created, altered, or re-attributed by the model; model concerns are hypotheses.
- A clean tool run means "no issues found by that tool", **never** "safe".

## 9. Tests

```powershell
py tests/test_parsing.py            # response parsing (offline)
py tests/test_error_paths.py        # error handling via TestClient (offline)
py tests/test_slither_integration.py# Slither normalizer + live runs
py tests/test_solhint_integration.py# Solhint normalizer + live lint runs + per-tool report/export
py tests/test_mcp_bridge.py         # knowledge modules + MCP bridge + approval gate
py tests/test_mcp_auditor.py        # read-only MCP server + current-analysis store
py tests/test_deployed_review.py    # Phase 6.1: EIP-55 validation + consent-off + no-network
py tests/test_deployed_lookup.py    # Phase 6.2: Ethereum networks + consent gate + provider success/failure (offline)
py tests/test_deployed_analysis.py  # Phase 6.3: retrieved-source analysis gating + multi-file refusal + no-auto-run (offline + live single-file smoke)
```

## 10. Troubleshooting

- "model unavailable" badge → Ollama not running or no models pulled
  (`ollama pull qwen2.5:3b`).
- Long waits on slow hardware are normal (request timeout 180s; analyzer timeout 120s).
- Port busy → `.\run.ps1 -Port 8001`.
- Frontend not loading → ensure `frontend/index.html` exists (mounted at `/`).
- Prompt/knowledge load errors → ensure all three files under `prompts/` exist.
- Slither not available → check `tools\slither\venv\Scripts\slither.exe` and
  `tools\solc\solc.exe`.

## 11. Layout

- `backend/` — FastAPI app: `config`, `schemas`, `ollama_client`, `prompts`,
  `knowledge`, `slither_adapter`, `solhint_adapter` (local Solhint linter —
  explicit-action only), `mcp_server`, `mcp_client`, `mcp_protocol`
  (shared stdio JSON-RPC framing), `analysis_store` (current-analysis record),
  `mcp_auditor` (read-only MCP server), `deployed` (offline chain list + EIP-55
  validation), `deployed_source` (Phase 6.2 consent-gated Sourcify provider —
  the only outbound lookup), `deployed_analysis` (Phase 6.3 explicit, offline
  analysis of the retrieved single-file source — no lookup, no model), `main`.
- `frontend/` — static SPA (`index.html`, `styles.css`, `app.js`).
- `prompts/` — the three versioned knowledge modules.
- `tools/` — local Slither + solc + Solhint installs (`tools/README.md`).
- `tests/` — parsing, error-path, Slither-integration, MCP-bridge, read-only MCP
  server, and deployed-review/lookup/analysis tests.
- `plan.md`, `research.md`, `action.md` — plan, research, and execution log.
  (`reseach.md` is a superseded draft, preserved untouched.)
- `DEMO.md` — 3–5 minute demo runbook (startup, exact demo contract, prompts,
  reject/approve paths, expected findings, recovery).

**Note:** a vanilla static frontend is used instead of Vite/React to keep the
demo to one local process with no build step; the backend API contract is stable
so a React frontend can be layered on later.
