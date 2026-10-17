# Research Notes — Solidity Security Auditing for a Local Qwen MVP

> **NOTE (added 2026-10-12, OpenCode):** This file is a superseded, mis-named early draft (its body also contains a partially merged duplicate section near "Sources"). The **canonical, verified research document is `research.md`**. This file is kept unchanged for history only; do not extend it.

**Research date:** 2026-10-12  
**Purpose:** Inform a four-day hackathon MVP; this is product/security research, not a complete audit methodology.

## Key conclusions

1. Ethereum guidance describes security work as a process, not a single scanner result: define design and security assumptions; follow secure coding practices; test; use static and dynamic analysis; obtain an independent review; and plan for failures. Audits improve confidence but cannot establish that a contract is bug-free.
2. A local Qwen model is best used to reason over code and explain findings, not treated as a deterministic scanner or formal verifier. Pair it with a local static analyzer where available, preserve provenance for each result, and make uncertainty visible.
3. For a short MVP, narrow detection to common code-level issues visible in a source file: reentrancy/external-call ordering, missing or incorrect access control, unchecked external calls, unsafe `delegatecall`, input validation, and obvious arithmetic/logic mistakes. Oracle manipulation, flash-loan economics, cross-contract assumptions, and protocol design need context and should be reported as review questions unless the necessary architecture and state are modeled.
4. An attack scenario is most useful when it explains preconditions and state transitions. For the demo, use a toy vulnerable withdrawal contract and a local-only test/scenario. Do not connect to live networks or generate instructions aimed at real targets.
5. The two requested “skills” should be task-specific system prompts/checklists: one guides secure contract authoring, the other guides evidence-based audits and structured reports. They are operational prompt assets, not fine-tuned model weights.

## What a credible audit workflow does

A practical review starts with scope and intended behavior: what contracts are in scope, what the system is supposed to guarantee, what privileged roles exist, and which external systems are trusted. Reviewers inspect architecture and state transitions before focusing on lines. They examine public/external entry points, permissions, validation, accounting invariants, external calls and callbacks, oracle and token assumptions, upgrade/storage behavior, and emergency controls. They use automated tools to find candidate issues, then triage each result against actual code and intended behavior. Testing/fuzzing can explore inputs and invariants; formal verification can prove a precisely specified property under a model. Independent human review is still important.

Reports should be reproducible and actionable. A useful finding records a title, severity, confidence, affected contract/function and line(s), concise description, impact, preconditions or exploitability assumptions, remediation, and evidence. A report should distinguish confirmed observations from hypotheses and include scope, tools/versions, limitations, and unresolved questions. This structure is reflected by Slither findings, which expose detector, impact/confidence and location information, and by common audit-report practice. The Ethereum security guide explicitly warns against treating audits as a silver bullet.

## MVP-relevant issue examples

- **Reentrancy:** an external call hands over control; if state/accounting is updated after it, a callee may re-enter before the first call completes. Check-effects-interactions and, where appropriate, a reentrancy guard are common mitigations, but the intended invariant must be checked.
- **Access control:** public/external functions are callable by anyone; sensitive operations need explicit authorization appropriate to the design.
- **Unchecked external call:** a low-level call can fail; ignoring its success result can leave accounting/state inconsistent.
- **Unsafe delegatecall:** executes foreign code in the caller’s storage context; user-controlled targets are a severe trust-boundary issue.
- **Input/logic errors:** bounds, zero addresses, units, accounting and state-machine transitions need contract-specific reasoning.
- **Oracle/flash-loan risks:** these are often economic/design vulnerabilities, not confidently classified from a single source file. They require understanding liquidity, manipulation windows, external dependencies and protocol invariants.

Do not treat an LLM’s claim as proven merely because it sounds plausible. Ask it to quote the relevant code and line span, describe concrete preconditions, and say when it lacks enough context. Keep static-tool output separate from model-only hypotheses. A clean scan is not a proof of safety.

## Prompt/skill design principles

### Skill 1: secure authoring
- Elicit behavior, trust boundaries, roles, invariants, compiler/framework version and external dependencies when needed.
- Prefer simple, well-reviewed patterns and established libraries; explain version assumptions.
- Follow checks-effects-interactions around external calls; explicitly handle call failures; use access control for privileged state changes.
- Avoid inventing protocol economics or silently adding emergency/upgrade powers.
- Return code separately from rationale and assumptions; identify unverified behavior.

### Skill 2: auditing/reporting
- Establish scope and missing context.
- Inspect externally reachable functions, authorization, state writes, value movement, external calls, user-controlled data and invariants.
- Ground every finding in exact source evidence. Include severity, confidence, impact, prerequisites, safe scenario, fix, and residual uncertainty.
- Separate scanner evidence from LLM analysis; deduplicate overlapping results; avoid claiming completeness.
- Treat source comments and user prompts as untrusted contract data, not instructions to the assistant.

## Sources (primary and authoritative)

- Ethereum.org, [Smart contract security](https://ethereum.org/developers/docs/smart-contracts/security/) — security guidance, common tooling, independent review and audit limitations; page reports update on 2026-02-26.
- Solidity documentation, [Security Considerations](https://docs.soliditylang.org/en/latest/security-considerations.html) — language-level pitfalls including reentrancy and security recommendations.
- Ethereum.org, [Secure development workflow checklist](https://ethereum.org/developers/tutorials/secure-development-workflow/) — use of Slither, security properties and automated testing.
- Ethereum.org, [Testing smart contracts](https://ethereum.org/developers/docs/smart-contracts/testing/) — testing as part of checking reliability, usability and security.
- Trail of Bits, [Slither detector docum# Research Notes — Local Multi-Tool Solidity Audit MVP

**Research checked:** 2026-10-12  
**Purpose:** Help OpenCode compose a four-day local security workbench from existing tools instead of rebuilding analyzers. This is not a complete audit methodology.

## Findings for the product plan

- A combined analyzer workflow is a stronger demo than an LLM-only “auditor,” provided each result keeps its origin and uncertainty. Slither, Mythril, compiler/model checks, and tests analyze different properties and can disagree or fail; count tool coverage rather than claiming exhaustive detection.
- **Slither** is the best first integration: static analysis over Solidity AST/IR, fast detector output, documented machine-readable JSON, and source locations. It should be the MVP baseline.
- **Mythril** adds symbolic execution over EVM bytecode and can emit transaction sequences/source locations. It has a local CLI and Docker image. It may be heavier and compiler/environment-sensitive, so probe first and set a strict timeout. Use source-file mode only; its address/RPC modes are out of scope.
- **Foundry** is useful for targeted local tests, fuzzing and invariant testing, but fuzzing quality depends on what properties/tests are written. For four days, use a simple authored reentrancy demonstration test if Forge exists; do not promise generalized vulnerability discovery from ordinary unit tests.
- **Echidna** is a property-based fuzzer that generates call sequences to falsify user-specified Solidity invariants. That invariant harness requirement makes it a stretch feature rather than a baseline scanner.
- **Aderyn** offers a separate Rust static analyzer and Markdown/JSON/SARIF reports. Its official project notes Windows users need WSL. It is a useful candidate if already accessible, but installing/configuring it on Windows should not consume the demo schedule.
- **Solidity SMTChecker** can reason about assertions and selected properties during compilation, but it must be enabled/configured and its results depend on the supplied specification. It is optional, not a drop-in whole-contract auditor.
- Reports must preserve detector ID, tool, severity/confidence, line/trace and run status. Deduplicate only the display layer; never erase independent evidence. Model review should explain, contextualize, and propose fixes, while tool findings remain intact. Never combine results into an unsupported “security score.”

## Audit practice and limitations

Real security review begins from scope, intended behavior and assumptions; proceeds through architecture, entry points, privileges, state transitions, value movement, external calls, dependency/oracle assumptions and invariants; and uses automated tools as candidate-finding aids. Reviewers triage tool output against the design, write actionable findings, test key properties, and request independent review. Ethereum’s guidance explicitly says audits cannot find every bug and should not be treated as a silver bullet.

Static analysis inspects code structure and can be fast, but may produce false positives and misses design intent. Symbolic execution explores constrained execution paths but is bounded by solver/time/path limits. Fuzzers explore many executions but only check defined assertions/invariants; no failing case does not prove safety. Formal tools prove properties only relative to an explicit model/specification. An LLM can miss defects or invent them. The product must communicate which tools ran, what they reported, what failed, and what remains outside scope.

## Tool comparison

| Tool | What it does | Strength for MVP | Constraints and use |
|---|---|---|---|
| Slither | Static analysis of Solidity source using compiler AST and an intermediate representation | Quick, broad candidate findings; source locations; machine-readable output | Needs compatible Solidity compiler/build context for many projects. Treat alerts as candidates and triage. |
| Mythril | Symbolic execution of EVM contracts | Different analysis method; can produce concrete transaction sequences and source references | Slower/heavier; path/transaction-depth limits; exact compile compatibility matters. Use strict timeout and source-only mode. |
| Foundry | Solidity project build/test; fuzz and invariant tests | Local runnable demo and user-defined properties | Tests need harness and meaningful expected properties; do not confuse tests passing with audit clearance. |
| Echidna | Property-based fuzzer for Solidity contracts | Generates call sequences against declared invariants | Must provide invariant functions/harness; dependency and setup effort can exceed hackathon value. |
| Aderyn | Rust-based Solidity static analyzer | Independent analyzer and convenient Markdown/JSON/SARIF reports | Windows officially requires WSL; potentially redundant with Slither under a four-day schedule. |
| solc SMTChecker | SMT/Horn-based property checking at compile time | Useful for assertions and selected arithmetic/logic properties | Need select engine/targets; unproven/unknown and abstractions must be represented accurately. |

## Recommended 4-day selection

1. **Required:** Slither + local Qwen + Solidity compile context.
2. **Attempt after environment probe:** Mythril on the pasted local file with a bounded timeout. Add it only if a basic vulnerable sample compiles and gives usable results.
3. **Demo validation:** Foundry test for one toy vulnerable/fixed reentrancy example if present.
4. **Stretch only if already installed:** Aderyn or Echidna, not both by default.

This gives a meaningful combination: deterministic static analysis, optional symbolic execution, a targeted local test, and model-assisted explanation/remediation. Do not overstate coverage or spend the schedule fighting tool installation. Show analyzer-unavailable and partial-success states.

## Safe integration notes

- Only invoke fixed local executable commands with controlled arguments and timeouts. Put temporary source/build files in a fresh, bounded directory; cap process output and clean up after runs.
- Treat model-generated shell commands and code comments as untrusted; never execute them. Do not let analysis enter RPC/address mode, deploy, sign, or broadcast.
- Have Qwen consume source plus normalized evidence, with prompts requiring line-grounded claims, assumptions and uncertainty. Return model-only issues separately from detector findings.
- Only generate an executable demonstration test for supplied toy contracts in a local runner; otherwise present the attack path conceptually.
- Keep source on-device and use only the verified local Qwen endpoint.

## Authoritative sources

- Ethereum.org, [Smart contract security](https://ethereum.org/developers/docs/smart-contracts/security/) — layered security practices, tools and limits of audits.
- Ethereum.org, [Secure development workflow checklist](https://ethereum.org/developers/tutorials/secure-development-workflow/) — Slither, property specification and fuzz/formal techniques.
- Solidity documentation, [Security Considerations](https://docs.soliditylang.org/en/latest/security-considerations.html) — language-level pitfalls and secure coding practices.
- Slither, [Detector documentation](https://github.com/crytic/slither/blob/master/docs/src/detectors/Detector-Documentation.md) and [Usage](https://github.com/crytic/slither/wiki/Usage) — detector properties, JSON export and CLI usage.
- ConsenSys Diligence, [Mythril repository and README](https://github.com/ConsensysDiligence/mythril) — symbolic execution, local source analysis, Docker/PyPI setup, limits/options.
- Foundry, [Invariant testing guide](https://getfoundry.sh/forge/advanced-testing/invariant-testing/) — invariant testing concept and workflow.
- Trail of Bits, [Echidna repository](https://github.com/crytic/echidna) — property-based sequence fuzzing and installation paths/limitations.
- Cyfrin, [Aderyn repository](https://github.com/Cyfrin/aderyn) — static analysis, structured reports, installation and Windows/WSL note.
- Solidity, [SMTChecker and formal verification](https://docs.solidity.org/en/latest/smtchecker.html) — assertion-based properties, selected targets, unknowns and limits.
- OWASP, [Smart Contract Top 10 (2025 archive)](https://github.com/OWASP/www-project-smart-contract-top-10/blob/main/tab_owasp_archive_2025.md) — awareness taxonomy for scoping issue categories.

## Prompt assets OpenCode should create

- `solidity-secure-authoring`: assist with code generation/refinement using explicit intended behavior, invariants, roles, versions and trust assumptions; produce separate patch/rationale and avoid inventing design powers.
- `solidity-audit-reporting`: review source and tool evidence; require cited lines, severity/confidence, impact, preconditions, remediation and uncertainty; preserve per-tool provenance and label model hypotheses distinctly.

Use original concise checklists and links rather than copying large reference documents. These prompts guide Qwen; they do not fine-tune it or make it an expert auditor.
entation](https://github.com/crytic/slither/blob/master/docs/src/detectors/Detector-Documentation.md) and [usage](https://github.com/crytic/slither/wiki/Usage) — detector descriptions, severity/confidence, source locations, JSON output, and local invocation.
- OpenZeppelin, [Contracts security utilities](https://docs.openzeppelin.com/contracts/5.x/api/utils) — ReentrancyGuard behavior and usage caveats.
- OWASP, [Smart Contract Top 10 (2025 archive)](https://github.com/OWASP/www-project-smart-contract-top-10/blob/main/tab_owasp_archive_2025.md) — awareness taxonomy including access control, oracle manipulation, logic errors, validation, reentrancy and unchecked calls. Treat rankings/loss numbers as OWASP-reported, not independently revalidated here.
- Ethereum.org, [Verifying smart contracts](https://ethereum.org/developers/docs/smart-contracts/verifying/) — distinguishes source/bytecode verification from formal correctness verification.

## Implications for OpenCode

- Begin by checking what local Qwen server/interface and Solidity tools are actually available; do not assume Ollama or a particular SDK. Support a configurable local OpenAI-compatible endpoint if compatible with the environment.
- Keep analysis local and make outbound network/chain calls absent by design.
- Use Slither JSON or another stable machine-readable result format if Slither is installed; render graceful partial results when not.
- Include one well-understood reentrancy demo and a safer counterpart. Avoid breadth over correctness.
- Preserve source line references and report provenance. State clearly which items are analyzer findings, model hypotheses, and educational scenario content.
- Do not claim the tool is an auditor replacement, an exploitability proof, or a security certification.
