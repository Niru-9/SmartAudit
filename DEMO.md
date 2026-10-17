# DEMO — Qwen + MCP + Slither + Solhint walkthrough

A 3–5 minute click-through of the one path a judge needs to see: paste the local
demo contract, ask the **local Qwen** assistant to run **Slither**, approve the
tool call, and read the deterministic findings — plus the reject path, the
separate local **Solhint** linter (its own panel), and the downloadable report.

Everything is local. Nothing compiles, deploys, signs, or contacts a blockchain.

---

## 1. Start the app

From `N:\Auditor` (PowerShell):

```powershell
.\run.ps1                      # starts uvicorn on 127.0.0.1:8000 and serves the SPA
# port busy?  .\run.ps1 -Port 8001
# direct:     py -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**

Quick readiness check (all three should answer before you present):

```powershell
curl http://localhost:11434/api/version        # Ollama is up (verified on 0.40.5)
curl http://127.0.0.1:8000/api/health          # status: ok, slither: available, solhint: available
```

The header shows **model** status (e.g. `models: qwen2.5:3b, qwen3:4b`) and
**slither: available 0.11.6 · solhint: available 6.2.5**. The chat panel shows
`MCP: stdio · tools: analyzer_status, run_slither · 3 resource(s)`.

## 2. The demo contract

```
N:\Auditor\tools\slither\samples\ReentrancyVault.sol
```

Open that file and paste its contents into the **Solidity source** box.
(Intentionally vulnerable: a reentrancy flaw in `withdraw` and an unprotected
`selfdestruct` in `cleanup`. Not production code.)

> The **Load sample** button loads a *different* built-in example
> (`VulnerableVault` + `FixedVault`). Either works; this runbook uses
> `ReentrancyVault.sol` so the line numbers below are exact.

## 3. Ask Qwen to run Slither (the approval flow)

1. In the **Follow-up Q&A** panel, tick **“Allow approved local tools (MCP)”**.
   (The toggle is **off by default** — tools are opt-in.)
2. Type this prompt and click **Send**:

   > `Run Slither on this contract now and then explain each finding.`

   *(Alternatively, after running Slither, click a finding card and click **Explain with Qwen** under Slither to get a focused explanation of that single finding. You can also click **Export report** to download a Markdown report of the current per-tool status + findings, the AI explanations, and the separate local-Qwen review.)*

3. A **proposal** appears — *nothing has run yet*:
   > “The model asked to use a local tool — nothing has run yet.”
   > `run_slither: Run the local Slither analyzer…` with **Approve & run run_slither** / **Dismiss**.
   The Static-analysis panel is still empty and no analyzer has been started.

### Reject path (prove nothing runs)

- Click **Dismiss**. The chat logs *“(Tool request dismissed. Nothing was run.)”*,
  the proposal disappears, and the **Static-analysis panel stays empty** — Slither
  did not run.

### Approve path

- Send the prompt again, then click **Approve & run run_slither**.
- “Thinking…” (spinner) shows while the approved call runs; **Send** is disabled
  until it finishes.
- Slither runs locally through the MCP bridge and the findings render in the
  **Static analysis — Slither** panel. Chat then shows Qwen's grounded explanation.

### Optional: plain model review (no tools)

Leave the MCP toggle **off** and click **Review with Qwen** with a prompt such as:

> `Review this contract and cite the exact code evidence for each concern.`

### Optional: run the local Solhint linter (its own panel)

Slither and Solhint are independent local analyzers with their own panels:

1. Click **Run Solhint** in the **Static analysis — Solhint** panel (the button
   is disabled only if the linter is missing). It lints the *same* source and
   renders its own findings (rule id, severity, location) plus raw
   (path-redacted) output.
2. Click **Run Slither** in the Slither panel to compare. The two result sets
   stay **separate** and each finding names the tool that produced it — nothing
   is merged, and neither runs automatically (no run on page load, typing, or
   rendering).
3. A clean Solhint run says **“no findings”**, which is **not** a safety claim.

Click **Export report** to download a Markdown report that lists **each tool's
status** (ran with findings / no findings / unavailable / skipped / error) and
groups the findings **by tool**, with AI explanations and the local-Qwen review
in their own clearly-labelled sections.

## 4. Expected report behavior

Static-analysis panel for `ReentrancyVault.sol` — **4 findings**, each a *tool
finding* with detector, severity, confidence and source location:

| Detector | Severity | Confidence | Location |
|---|---|---|---|
| `reentrancy-eth` | High | Medium | `Submitted.sol:20-26` |
| `suicidal` | High | High | `Submitted.sol:29-31` |
| `reentrancy-events` | Low | Medium | `Submitted.sol:20-26` |
| `low-level-calls` | Informational | High | `Submitted.sol:20-26` |

- The panel is rendered **directly from Slither's output**, never from model text.
  Qwen's chat reply *explains* the findings; it cannot create, erase, or move them.
- Raw (bounded, path-redacted) Slither output appears under the panel when you
  use the direct **Run Slither** button. The MCP approve path shows the same
  normalized findings (rendered from the tool payload, never from model text)
  **without** the raw dump — the bridge payload is intentionally brief. To show
  the raw output on stage, click **Run Slither** (same analyzer, direct path).
- A clean run says **“no findings”**, never “safe”.
- Running **Solhint** separately shows its own lint findings (e.g.
  `compiler-version`, `gas-increment-by-one`, `use-natspec`, `state-visibility`)
  — style/maintainability plus a thin set of security rules; it **complements**
  Slither and is **not** a substitute for it.

**Visible limitations**

- Model output (review + chat) is AI triage and may be incomplete, inconsistent,
  or wrong — the deterministic panel is the source of truth.
- Slither findings are partial — **not** a complete or certified audit.
- Only **Slither + solc + Solhint** are integrated. Mythril, MythX, Securify,
  Surya, Echidna, Aderyn and Foundry are **not** installed/integrated.
- The MCP bridge is one approved tool call per turn; the server is spawned per
  approved call.
- “Suggest safer code” is a **draft** for human review, never verified or applied.

### Optional: read the current findings from a local MCP client

After a run, the app records the **current** analysis for a separate,
**read-only** local MCP server (`py -m backend.mcp_auditor`). An external MCP
client can then read the analyzer status (`analyzer_status` /
`auditor://status/analyzers`) and the current findings (`audit_findings` /
`auditor://findings/current`). Both tools take **no arguments** and can only
**read** — there is no way to run an analyzer, write files, or change code from
this surface. It shows deterministic Slither output only (never Qwen prose), and
answers **“no results available”** before any analysis has run. See README §7 for
the start command and a generic client config.

### Optional: deployed-contract panel (Phase 6.3, read-only verified source)

Below the source editor is a **Deployed contract** panel. The flow:

1. **Network** — the choices are **Ethereum mainnet plus the Ethereum testnets
   Sourcify supports** (Sepolia `11155111`, Holesky `17000`, Hoodi `560048`),
   each shown with its chain ID. **Nothing is selected by default**, and Base,
   Arbitrum, and other non-Ethereum networks are not offered. This is a network
   selection, not a wallet connection.
2. **Contract address** — paste a 20-byte address (e.g.
   `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`, the WETH9 contract on mainnet,
   or the Sourcify docs example `0x2738d13E81e30bC615766A0410e7cF199FD59A83` on
   Sepolia) and see the **offline** validation (normalized + EIP-55 checksummed
   form). Lowercase and uppercase addresses are accepted; a mixed-case address
   with a wrong checksum is **flagged**, not silently changed. Typing only runs
   this local check.
3. **Consent** — the checkbox is **off by default** and ticking it fetches
   nothing; it only enables the button.
4. **Retrieve & review** — the button stays **disabled** until a valid address,
   a supported Ethereum network, and consent are all set. Clicking it makes
   **one read-only** request to fetch the contract's **public verified source +
   basic metadata** from **Sourcify** and shows the match, compiler version,
   deployment block, and the source files.
5. **Analyze retrieved source** *(Phase 6.3)* — when the retrieved source is a
   **single complete Solidity file**, this button becomes available; clicking it
   runs the **same local Slither** used for the pasted editor on *that* retrieved
   source. It runs **only** on the click — never on lookup completion, address
   change, network change, or result load — and the results (deterministic
   `kind:"tool"` findings) are **cleared** if you change the address or network.
   Selecting a finding enables **Explain with Qwen** (local model); Qwen only
   *explains* an existing finding and never creates or changes one. Analysis can
   also return an honest non-success status (e.g. **compiler_error** for an old
   pragma such as WETH9's `0.4.19` under the installed `0.8.37`), and it never
   claims the contract is "safe".

Read-only guarantees: no wallet, key, signature, transaction, deployment, or
on-chain write is involved; the lookup and the analysis are **separate explicit
actions**, and the lookup itself runs **no analyzer or model** (so the source is
shown, then audited only if you click). If the contract is
**unverified/unavailable/rate-limited/timed out**, the panel says so plainly
(e.g. "No verified source was found … Nothing was fabricated") and never invents
source. Nothing is fetched on page load, on typing, or on toggling consent —
only on that explicit click.

**Multi-file limitation:** the local analyzer analyzes a single Solidity source
unit, so a verified contract with **more than one** source file is marked
**unsupported in this phase and is not analyzed** — the panel says so plainly
instead of silently analyzing one file or presenting a partial run. The same
applies to truncated/incomplete and non-Solidity source.

Success demo (mainnet): network **Ethereum Mainnet (1)**, address
`0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2` → *Verified* (single-file) →
**Analyze retrieved source** → honest `compiler_error` (old pragma).
Success demo (Sepolia testnet): network **Ethereum Sepolia Testnet
(11155111)**, address `0x2738d13E81e30bC615766A0410e7cF199FD59A83` → *Verified*
(single file `contracts/1_Storage.sol`) → **Analyze retrieved source** → Slither
`ok` with findings you can then **Explain with Qwen**.
Failure demo: address `0x0000000000000000000000000000000000000001` → *Not
verified (no source)*. Selecting a non-Ethereum network is not possible; sending
one (e.g. Base `8453`) returns `unsupported_chain` without any lookup.

## 5. Recovery steps

| Symptom | Fix |
|---|---|
| Model badge says unavailable / actions disabled | Start Ollama (`ollama serve`) and ensure a model is pulled (`ollama pull qwen2.5:3b`); reload the page. |
| `slither: unavailable` | Confirm `tools\slither\venv\Scripts\slither.exe` and `tools\solc\solc.exe` exist. The Run Slither button and the MCP proposal both need them. |
| `solhint: unavailable` | Confirm `tools\solhint\node_modules\solhint\solhint.js` exists and Node is on `PATH` (`node --version`). The Run Solhint button needs both. |
| App not loading | Restart it: `.\run.ps1` (or `.\run.ps1 -Port 8001`). Check `curl http://127.0.0.1:8000/api/health`. |
| Proposal never appears | Ensure the **“Allow approved local tools (MCP)”** box is ticked, and ask explicitly (e.g. “Run Slither…”). The model *proposes*; it never runs by itself. |
| Slither times out / compiler error | Test the file directly: `tools\slither\venv\Scripts\slither.exe <File.sol> --solc tools\solc\solc.exe`. Long runs are bounded (analyzer timeout 120 s). |
| Port 8000 busy | `.\run.ps1 -Port 8001` and open `http://127.0.0.1:8001`. |

## 6. What NOT to say on stage

Do not claim the tool guarantees safety, performs a complete audit, or fixes
code. Findings are automated triage from the named tools; suggested code is an
unverified draft.
