const SAMPLE = `// Simple reentrancy-vulnerable vault (educational example)
pragma solidity ^0.8.20;

contract VulnerableVault {
    mapping(address => uint256) private _balances;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);

    function deposit() external payable {
        _balances[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    // Vulnerable: external call before state update (checks-interactions-effects order broken)
    function withdraw(uint256 amount) external {
        require(_balances[msg.sender] >= amount, "insufficient balance");
        // State change happens AFTER the external call — unsafe in general.
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        _balances[msg.sender] -= amount;
        emit Withdrawn(msg.sender, amount);
    }

    function balanceOf(address who) external view returns (uint256) {
        return _balances[who];
    }
}

contract FixedVault {
    mapping(address => uint256) private _balances;

    event Deposited(address indexed user, uint256 amount);
    event Withdrawn(address indexed user, uint256 amount);

    function deposit() external payable {
        _balances[msg.sender] += msg.value;
        emit Deposited(msg.sender, msg.value);
    }

    // Safer: checks-effects-interactions (update state before external call)
    function withdraw(uint256 amount) external {
        require(_balances[msg.sender] >= amount, "insufficient balance");
        _balances[msg.sender] -= amount;
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        emit Withdrawn(msg.sender, amount);
    }

    function balanceOf(address who) external view returns (uint256) {
        return _balances[who];
    }
}
`;

let chatHistory = [];
let pendingProposal = null;
let selectedFinding = null; // store the finding object selected by user
let lastSlitherData = null; // hold current slither result for export
let lastSolhintData = null; // hold current solhint result for export
let lastReview = null; // hold latest Qwen review {model, content} for the export's separate AI section
let lastExplain = null; // hold last explanation {model, content, elapsed_ms} if available

async function checkHealth() {
  const badge = document.getElementById('healthBadge');
  const toolBadge = document.getElementById('toolBadge');
  const slitherBtn = document.getElementById('slitherBtn');
  const exportReportBtn = document.getElementById('exportReportBtn');
  const modelSelect = document.getElementById('modelSelect');
  const reviewBtn = document.getElementById('reviewBtn');
  const suggestBtn = document.getElementById('suggestBtn');
  const chatBtn = document.getElementById('chatBtn');

  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('health check failed');
    const data = await res.json();

    // --- Model status (drives Qwen actions only) ---
    if (data.models && Array.isArray(data.models) && data.models.length > 0) {
      badge.textContent = `models: ${data.models.join(', ')} | default: ${data.model_default}`;
      badge.classList.remove('unavailable');
      badge.classList.add('ok');
      modelSelect.innerHTML = '';
      for (const m of data.models) {
        const opt = document.createElement('option');
        opt.value = m;
        opt.textContent = m;
        if (m === data.model_default) opt.selected = true;
        modelSelect.appendChild(opt);
      }
      modelSelect.disabled = false;
      reviewBtn.disabled = false;
      suggestBtn.disabled = false;
      chatBtn.disabled = false;
    } else {
      badge.textContent = `model unavailable — Ollama at ${data.ollama_url}`;
      badge.classList.add('unavailable');
      badge.classList.remove('ok');
      modelSelect.disabled = true;
      reviewBtn.disabled = true;
      suggestBtn.disabled = true;
      chatBtn.disabled = true;
    }

    // --- Tool status (independent of the model): one entry per analyzer ---
    const details = Array.isArray(data.tool_details) ? data.tool_details : [];
    const toolStatusOf = (name) => {
      const d = details.find((x) => x && x.name === name);
      return {
        status: (d && d.status) || (data.tools && data.tools[name]) || 'unavailable',
        version: d && d.version ? ` ${d.version}` : '',
      };
    };
    const sl = toolStatusOf('slither');
    const so = toolStatusOf('solhint');
    slitherAvailable = sl.status === 'available';
    solhintAvailable = so.status === 'available';

    // Header badge: one honest status per known analyzer.
    toolBadge.textContent = `slither: ${sl.status}${sl.version} · solhint: ${so.status}${so.version}`;
    toolBadge.classList.toggle('ok', slitherAvailable || solhintAvailable);
    toolBadge.classList.toggle('unavailable', !slitherAvailable && !solhintAvailable);

    // Per-tool panel badges + per-tool Run buttons (each runs only on its click).
    const setStatusBadge = (el, s) => {
      if (!el) return;
      el.textContent = s.status === 'available' ? `available${s.version}` : s.status;
      el.classList.toggle('ok', s.status === 'available');
      el.classList.toggle('unavailable', s.status !== 'available');
    };
    setStatusBadge(document.getElementById('slitherStatus'), sl);
    setStatusBadge(document.getElementById('solhintStatus'), so);
    slitherBtn.disabled = !slitherAvailable;
    const solhintRunBtn = document.getElementById('solhintBtn');
    if (solhintRunBtn) solhintRunBtn.disabled = !solhintAvailable;
    if (exportReportBtn) exportReportBtn.disabled = !(slitherAvailable || solhintAvailable);

    // Empty-state hint when a tool cannot run and has produced nothing yet.
    const sum = document.getElementById('slitherSummary');
    if (!slitherAvailable && sum && !document.getElementById('slitherFindings').children.length) {
      sum.textContent = 'Slither is not available locally, so it cannot run.';
    }
    const soSum = document.getElementById('solhintSummary');
    if (!solhintAvailable && soSum && !document.getElementById('solhintFindings').children.length) {
      soSum.textContent = 'Solhint is not available locally, so it cannot run.';
    }

    // --- Local MCP bridge info (surfaced from health; no hosted service) ---
    const mcpInfo = document.getElementById('mcpInfo');
    const mcp = data.mcp;
    if (mcpInfo && mcp) {
      const toolNames = Array.isArray(mcp.tools) ? mcp.tools.map((t) => t.name) : [];
      const resCount = Array.isArray(mcp.resources) ? mcp.resources.length : 0;
      mcpInfo.textContent = `MCP: ${mcp.transport} · tools: ${toolNames.join(', ') || 'none'} · ${resCount} resource(s)`;
    } else if (mcpInfo) {
      mcpInfo.textContent = 'MCP: unavailable';
    }
  } catch (e) {
    badge.textContent = 'health check failed';
    badge.classList.add('unavailable');
    toolBadge.textContent = 'slither: unknown · solhint: unknown';
    toolBadge.classList.add('unavailable');
    modelSelect.disabled = true;
    reviewBtn.disabled = true;
    suggestBtn.disabled = true;
    chatBtn.disabled = true;
    slitherBtn.disabled = true;
    slitherAvailable = false;
    const solhintBtnFallback = document.getElementById('solhintBtn');
    if (solhintBtnFallback) solhintBtnFallback.disabled = true;
    solhintAvailable = false;
    if (typeof exportReportBtn !== 'undefined' && exportReportBtn) exportReportBtn.disabled = true;
  }
  // Keep the deployed "Analyze retrieved source" action in sync with tool
  // availability. This only toggles a button; it never runs the analyzer.
  updateDeployedAnalyzeButton();
}

function setError(panelEl, err) {
  const el = panelEl.querySelector('.error');
  if (el) {
    el.textContent = err || 'Request failed';
    el.classList.remove('hidden');
  } else {
    document.getElementById('errorBanner').textContent = err || 'Request failed';
    document.getElementById('errorBanner').classList.remove('hidden');
  }
}

function clearError(panelEl) {
  const el = panelEl.querySelector('.error');
  if (el) el.classList.add('hidden');
  document.getElementById('errorBanner').classList.add('hidden');
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function renderReview(content) {
  const el = document.getElementById('reviewContent');
  el.innerHTML = escapeHtml(content).replace(/\n/g, '<br>');
  el.classList.remove('pretext');
  el.classList.add('pretext');
  document.getElementById('copyReviewBtn').disabled = false;
}

function renderChat() {
  const el = document.getElementById('chatHistory');
  let html = '';
  for (const m of chatHistory) {
    html += `<b>${m.role}:</b> ${escapeHtml(m.content).replace(/\n/g, '<br>')}<br><br>`;
  }
  el.innerHTML = html;
  el.scrollTop = el.scrollHeight;
}

// --- MCP tool-call approval ------------------------------------------------
// A proposal is *only* a request from the model. Nothing runs until the user
// clicks Approve, which is the single action that calls the backend tool
// endpoint. The direct "Run Slither" button is unrelated and stays unchanged.
function clearProposal() {
  pendingProposal = null;
  const el = document.getElementById('chatProposal');
  if (el) {
    el.innerHTML = '';
    el.classList.add('hidden');
  }
}

function renderProposal(p, question) {
  pendingProposal = { ...p, question: question };
  const el = document.getElementById('chatProposal');
  if (!el) return;
  el.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'proposal-head';
  head.textContent = 'The model asked to use a local tool — nothing has run yet.';
  el.appendChild(head);

  const detail = document.createElement('div');
  detail.className = 'proposal-detail';
  const toolName = p.tool || 'unknown';
  detail.textContent = `${toolName}: ${p.summary || ''}`;
  el.appendChild(detail);

  const note = document.createElement('div');
  note.className = 'muted small';
  note.textContent = 'Approving runs the local analyzer on your current source. The model supplies no code, path, or command.';
  el.appendChild(note);

  const actions = document.createElement('div');
  actions.className = 'proposal-actions';

  const approve = document.createElement('button');
  approve.className = 'primary';
  approve.textContent = `Approve & run ${toolName}`;
  approve.addEventListener('click', approveProposal);
  actions.appendChild(approve);

  const dismiss = document.createElement('button');
  dismiss.textContent = 'Dismiss';
  dismiss.addEventListener('click', () => {
    clearProposal();
    chatHistory.push({ role: 'assistant', content: '(Tool request dismissed. Nothing was run.)' });
    renderChat();
  });
  actions.appendChild(dismiss);

  el.appendChild(actions);
  el.classList.remove('hidden');
}

async function approveProposal() {
  if (!pendingProposal) return;
  const input = document.getElementById('solidityInput');
  const modelSelect = document.getElementById('modelSelect');
  const chatBtn = document.getElementById('chatBtn');
  const proposal = pendingProposal;
  clearProposal();

  document.getElementById('chatSpinner').classList.remove('hidden');
  const timer = startElapsed('chatElapsed');
  chatBtn.disabled = true;
  try {
    const res = await fetch('/api/tools/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        source: input.value,
        question: proposal.question,
        history: chatHistory.slice(0, -1),
        model: modelSelect.value,
        tool: proposal.tool,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || 'tool run failed');
    }
    // Show deterministic tool output in the static-analysis panel.
    if (proposal.tool === 'run_slither' && data.tool_result) {
      renderSlither(data.tool_result);
    }
    const prefix = data.tool_ok ? '' : '[tool unavailable] ';
    chatHistory.push({ role: 'assistant', content: `${prefix}${data.reply}` });
    renderChat();
  } catch (e) {
    setError(document.getElementById('chatPanel'), e.message);
  } finally {
    document.getElementById('chatSpinner').classList.add('hidden');
    stopElapsed(timer, 'chatElapsed');
    chatBtn.disabled = false;
    await checkHealth();
  }
}

function severityClass(sev) {
  const s = String(sev || 'unknown').toLowerCase();
  if (s === 'critical') return 'sev-critical';
  if (s === 'high') return 'sev-high';
  if (s === 'medium') return 'sev-medium';
  if (s === 'low') return 'sev-low';
  if (s === 'informational' || s === 'info') return 'sev-info';
  return 'sev-unknown';
}

function locationLabel(f) {
  const file = f.file || 'Submitted.sol';
  if (f.start_line == null) return file;
  if (f.end_line != null && f.end_line !== f.start_line) return `${file}:${f.start_line}-${f.end_line}`;
  return `${file}:${f.start_line}`;
}

// Default selection behaviour for the pasted-code Slither panel.
function selectPastedFinding(f) {
  selectedFinding = f;
  const sel = document.getElementById('selectedFinding');
  if (sel) sel.textContent = `${f.check} @ ${locationLabel(f)} (sev:${f.severity}, conf:${f.confidence}) — selected`;
  const btn = document.getElementById('explainFindingBtn');
  if (btn) btn.disabled = false;
}

// Build one tool-finding card using DOM APIs (never innerHTML on tool text).
// `opts.scopeSel` limits deselection to the owning container; `opts.onSelect`
// overrides the default (pasted-code) selection handler. The default path is
// byte-for-byte the same behaviour as before.
function renderFindingCard(f, opts) {
  const options = opts || {};
  const scopeSel = options.scopeSel || '#slitherFindings';
  const card = document.createElement('div');
  card.className = `finding ${severityClass(f.severity)}`;
  card.dataset.findingId = f.id || f.check || 'finding';
  card.tabIndex = 0;
  card.setAttribute('role', 'button');
  card.setAttribute('aria-pressed', 'false');
  card.addEventListener('click', () => {
    // Update selection
    document.querySelectorAll(`${scopeSel} .finding`).forEach(el => {
      el.classList.remove('selected');
      el.setAttribute('aria-pressed', 'false');
    });
    card.classList.add('selected');
    card.setAttribute('aria-pressed', 'true');
    if (typeof options.onSelect === 'function') {
      options.onSelect(f, card);
    } else {
      selectPastedFinding(f);
    }
  });
  card.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      card.click();
    }
  });

  const head = document.createElement('div');
  head.className = 'finding-head';

  const sev = document.createElement('span');
  sev.className = `sev ${severityClass(f.severity)}`;
  sev.textContent = String(f.severity || 'unknown').toUpperCase();
  head.appendChild(sev);

  const check = document.createElement('span');
  check.className = 'finding-check';
  check.textContent = f.check || 'unknown';
  head.appendChild(check);

  const conf = document.createElement('span');
  conf.className = 'muted small-inline';
  conf.textContent = `confidence: ${f.confidence || 'unknown'}`;
  head.appendChild(conf);

  const kind = document.createElement('span');
  kind.className = 'kind-tag';
  kind.textContent = 'tool finding';
  kind.title = 'Reported by a deterministic analyzer, not the AI model.';
  head.appendChild(kind);

  card.appendChild(head);

  const loc = document.createElement('div');
  loc.className = 'finding-loc';
  loc.textContent = locationLabel(f);
  card.appendChild(loc);

  if (f.title && f.title !== f.check) {
    const title = document.createElement('div');
    title.className = 'finding-title';
    title.textContent = f.title;
    card.appendChild(title);
  }

  if (f.description) {
    const desc = document.createElement('div');
    desc.className = 'finding-desc';
    desc.textContent = f.description;
    card.appendChild(desc);
  }

  if (f.evidence && f.evidence !== f.description) {
    const ev = document.createElement('div');
    ev.className = 'finding-evidence';
    ev.textContent = `evidence: ${f.evidence}`;
    card.appendChild(ev);
  }

  const extraLocs = Array.isArray(f.locations) ? f.locations : [];
  if (extraLocs.length > 0) {
    const chips = document.createElement('div');
    chips.className = 'finding-locs';
    for (const l of extraLocs) {
      const chip = document.createElement('span');
      chip.className = 'loc-chip';
      const name = l.file || 'Submitted.sol';
      chip.textContent = l.start_line == null ? name
        : (l.end_line != null && l.end_line !== l.start_line ? `${name}:${l.start_line}-${l.end_line}` : `${name}:${l.start_line}`);
      chips.appendChild(chip);
    }
    card.appendChild(chips);
  }

  if (f.reference && /^https?:\/\//i.test(f.reference)) {
    const ref = document.createElement('a');
    ref.className = 'finding-ref';
    ref.href = f.reference;
    ref.target = '_blank';
    ref.rel = 'noopener noreferrer';
    ref.textContent = 'detector documentation';
    card.appendChild(ref);
  }

  return card;
}

// Shared renderer for one analyzer's run into its own section (summary, cards,
// raw output). Findings are always attributed to the tool named on the run.
function renderToolRun(prefix, data) {
  const summary = document.getElementById(prefix + 'Summary');
  const cont = document.getElementById(prefix + 'Findings');
  const rawWrap = document.getElementById(prefix + 'RawWrap');
  const raw = document.getElementById(prefix + 'Raw');
  if (!summary || !cont) return;
  const toolLabel = (data && data.tool) || prefix;
  let text = (data && data.summary) || `${toolLabel} finished.`;
  if (data && data.detail) text += `\n\n${data.detail}`;
  summary.textContent = text;

  cont.innerHTML = '';
  const findings = data && Array.isArray(data.findings) ? data.findings : [];
  if (findings.length > 0) {
    // One shared selection scope across every finding in this panel so only one
    // card (from either tool) can be selected at a time.
    for (const f of findings) cont.appendChild(renderFindingCard(f, { scopeSel: '#slitherPanel' }));
    cont.classList.remove('hidden');
  } else {
    cont.classList.add('hidden');
  }
  if (raw && rawWrap) {
    if (data && data.raw_output) {
      raw.textContent = data.raw_output + (data.raw_output_truncated ? '\n\n[... raw output truncated ...]' : '');
      rawWrap.classList.remove('hidden');
    } else {
      raw.textContent = '';
      rawWrap.classList.add('hidden');
    }
  }
}

// Clears any finding selection + the explain surface (called after a fresh run).
function resetFindingSelection() {
  selectedFinding = null;
  const sel = document.getElementById('selectedFinding');
  if (sel) sel.textContent = 'No finding selected. Click a finding card above to select it.';
  const exBtn = document.getElementById('explainFindingBtn');
  if (exBtn) exBtn.disabled = true;
  const exContent = document.getElementById('explainContent');
  if (exContent) { exContent.textContent = ''; exContent.classList.add('hidden'); }
}

function renderSlither(data) {
  renderToolRun('slither', data);
  resetFindingSelection();
}

function renderSolhint(data) {
  renderToolRun('solhint', data);
  resetFindingSelection();
}

function resetSlitherPanel() {
  lastSlitherData = null;
  lastSolhintData = null;
  const summary = document.getElementById('slitherSummary');
  summary.textContent = 'Run Slither to analyze the source with the local analyzer.';
  const cont = document.getElementById('slitherFindings');
  cont.innerHTML = '';
  cont.classList.add('hidden');
  const raw = document.getElementById('slitherRaw');
  const rawWrap = document.getElementById('slitherRawWrap');
  if (raw) raw.textContent = '';
  if (rawWrap) rawWrap.classList.add('hidden');
  // Solhint section carries its own independent state.
  const soSummary = document.getElementById('solhintSummary');
  if (soSummary) soSummary.textContent = 'Run Solhint to lint the source with the local linter.';
  const soCont = document.getElementById('solhintFindings');
  if (soCont) { soCont.innerHTML = ''; soCont.classList.add('hidden'); }
  const soRaw = document.getElementById('solhintRaw');
  const soRawWrap = document.getElementById('solhintRawWrap');
  if (soRaw) soRaw.textContent = '';
  if (soRawWrap) soRawWrap.classList.add('hidden');
  const exBtn = document.getElementById('exportReportBtn');
  if (exBtn) exBtn.disabled = true;
  clearError(document.getElementById('slitherPanel'));
}

function startElapsed(id) {
  let sec = 0;
  const el = document.getElementById(id);
  if (!el) return null;
  el.textContent = `0s elapsed`;
  const timer = setInterval(() => {
    sec++;
    el.textContent = `${sec}s elapsed`;
  }, 1000);
  return timer;
}

function stopElapsed(timer, id) {
  if (timer) clearInterval(timer);
  const el = document.getElementById(id);
  if (el) el.textContent = '';
}

// ---------------------------------------------------------------------------
// Phase 6.2 — deployed-contract review: consent-gated, read-only verified
// source lookup (Ethereum mainnet + Sourcify-supported Ethereum testnets).
//
// Nothing is fetched until the user clicks "Retrieve & review" with consent
// ticked. Typing, toggling consent, network selection, and page load only
// validate the address locally (same-origin /api/deployed/validate-address —
// fully offline). The one outbound request is POST /api/deployed/lookup,
// triggered only by that button. The supported network set (with chain IDs) is
// read from /api/deployed/config, so the UI never hard-codes a chain list and
// never silently defaults to one. No wallet/sign/send/deploy/modify; no
// analyzer or model runs here.
// ---------------------------------------------------------------------------

// `lookup` holds the CURRENT verified lookup payload so the Phase 6.3 analysis
// is provably tied to it; `lookupStale` is set the moment the address or network
// changes, which invalidates the analysis action until the source is retrieved
// again. `analysis`/`selectedFinding` are the retrieved-source analyzer results
// and are cleared whenever the lookup or its inputs change.
const deployedState = {
  chainId: '',
  valid: false,
  consent: false,
  busy: false,
  supportedChainIds: new Set(),
  lookup: null,
  lookupStale: false,
  analysis: null,
  analysisBusy: false,
  selectedFinding: null,
};

// Tracks whether the local Slither analyzer is available (from /api/health) so
// the deployed "Analyze retrieved source" action can mirror the pasted-code
// analyzer's availability gating. It never triggers a run on its own.
let slitherAvailable = false;
let solhintAvailable = false;

function setDeployedFeedback(text, kind) {
  const msg = document.getElementById('addressFeedback');
  if (msg) {
    msg.textContent = text;
    msg.classList.remove('ok', 'warn', 'err');
    if (kind) msg.classList.add(kind);
  }
  const inputEl = document.getElementById('addressInput');
  if (inputEl) {
    inputEl.classList.remove('addr-ok', 'addr-warn', 'addr-invalid');
    if (kind === 'ok') inputEl.classList.add('addr-ok');
    else if (kind === 'warn') inputEl.classList.add('addr-warn');
    else if (kind === 'err') inputEl.classList.add('addr-invalid');
  }
}

// The lookup button is enabled ONLY when a valid address is present, a
// supported Ethereum network (from /api/deployed/config) is explicitly
// selected, and consent is ticked. Toggling anything else does not fetch, and
// an unselected network can never enable the button.
function updateDeployedButton() {
  const btn = document.getElementById('deployedLookupBtn');
  if (!btn) return;
  const ready =
    deployedState.valid &&
    deployedState.chainId !== '' &&
    deployedState.supportedChainIds.has(deployedState.chainId) &&
    deployedState.consent &&
    !deployedState.busy;
  btn.disabled = !ready;
}

async function loadDeployedConfig() {
  const select = document.getElementById('chainSelect');
  const help = document.getElementById('chainHelp');
  if (!select) return;
  // Single, unselected placeholder — the network stays unset until the user picks.
  select.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Select a network…';
  select.appendChild(placeholder);
  select.value = '';
  try {
    const res = await fetch('/api/deployed/config');
    if (!res.ok) throw new Error('config request failed');
    const data = await res.json();
    (data.chains || []).forEach((chain) => {
      const opt = document.createElement('option');
      opt.value = String(chain.chain_id);
      const label = chain.is_testnet ? `${chain.name} (${chain.chain_id}, testnet)` : `${chain.name} (${chain.chain_id})`;
      opt.textContent = label;
      select.appendChild(opt);
    });
    // Supported chain IDs come from the backend config — the UI offers exactly
    // what the provider supports and nothing else (no hard-coded chain list).
    deployedState.supportedChainIds = new Set((data.chains || []).map((c) => String(c.chain_id)));
    select.value = ''; // never auto-select a network
    if (help && data.read_only_notice) help.title = data.read_only_notice;
  } catch (e) {
    if (help) help.textContent = 'Network list unavailable (backend not reachable). Selection stays local.';
  }
  updateDeployedButton();
}

async function validateDeployedAddress(address) {
  deployedState.valid = false;
  if (!address || !address.trim()) {
    setDeployedFeedback('Enter a 20-byte hexadecimal EVM address.', null);
    updateDeployedButton();
    return;
  }
  try {
    const res = await fetch('/api/deployed/validate-address', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({address}),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'validation failed');
    deployedState.valid = data.valid === true;
    if (!data.valid) {
      setDeployedFeedback(data.message, 'err');
    } else if (data.checksum_ok === false) {
      setDeployedFeedback(`${data.message} Expected EIP-55 form: ${data.checksum}`, 'warn');
    } else if (data.checksum_provided === false) {
      setDeployedFeedback(`${data.message} EIP-55 form: ${data.checksum}`, 'ok');
    } else {
      setDeployedFeedback(data.message, 'ok');
    }
  } catch (e) {
    deployedState.valid = false;
    setDeployedFeedback('Address check failed (local backend not reachable). Validation is offline-only.', 'err');
  }
  updateDeployedButton();
}

// --- Result rendering (no untrusted HTML) ---------------------------------

function _deployedText(tag, text, cls) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  el.textContent = text == null ? '' : String(text);
  return el;
}

function _deployedRow(label, value) {
  const row = document.createElement('div');
  row.className = 'deployed-row';
  row.appendChild(_deployedText('span', label, 'deployed-row-label'));
  row.appendChild(_deployedText('span', value, 'deployed-row-value'));
  return row;
}

function renderDeployedResult(data) {
  const box = document.getElementById('deployedResult');
  if (!box) return;

  // A fresh lookup replaces whatever source was associated before: record the
  // current lookup and invalidate any prior retrieved-source analysis so no
  // result from an older lookup can survive a replacement.
  deployedState.lookup = data;
  deployedState.lookupStale = false;
  clearDeployedAnalysis();

  box.innerHTML = '';
  box.hidden = false;

  const head = document.createElement('div');
  head.className = 'deployed-result-head';
  const statusText =
    data.status === 'verified'
      ? 'Verified source retrieved'
      : data.status === 'not_verified'
        ? 'Not verified (no source)'
        : data.status.replace(/_/g, ' ');
  head.appendChild(_deployedText('strong', statusText, data.verified ? 'ok' : 'warn'));
  box.appendChild(head);

  if (data.message) box.appendChild(_deployedText('p', data.message, 'deployed-message'));

  if (data.lookup_performed) {
    box.appendChild(_deployedRow('Network', data.chain_name ? `${data.chain_name} (chain ID ${data.chain_id})` : `chain ${data.chain_id}`));
    box.appendChild(_deployedRow('Address', data.checksum || data.address));
    box.appendChild(_deployedRow('Provider', data.provider));
  }
  if (data.verified) {
    box.appendChild(_deployedRow('Match', data.match === 'exact_match' ? 'exact bytecode match' : (data.match || 'match')));
    if (data.verified_at) box.appendChild(_deployedRow('Verified at', data.verified_at));
    if (data.contract_name) box.appendChild(_deployedRow('Contract', data.contract_name));
    if (data.compiler_version) box.appendChild(_deployedRow('Compiler', data.compiler_version));
    if (data.deployment_block) box.appendChild(_deployedRow('Deployed at block', data.deployment_block));
    box.appendChild(_deployedRow('Source files', `${data.source_file_count} (ABI entries: ${data.abi_entries}, metadata: ${data.metadata_present ? 'yes' : 'no'})`));

    if (data.sources_truncated) {
      box.appendChild(_deployedText('p', 'Source was truncated to fit the size cap; some files are partial.', 'deployed-warn'));
    }

    (data.sources || []).forEach((f) => {
      const det = document.createElement('details');
      det.className = 'deployed-source';
      const sum = document.createElement('summary');
      sum.textContent = `${f.path} (${f.chars} chars)`;
      det.appendChild(sum);
      const pre = document.createElement('pre');
      pre.textContent = f.content || '';
      det.appendChild(pre);
      box.appendChild(det);
    });
  }

  box.appendChild(
    _deployedText(
      'p',
      'Not audited yet: retrieving this source ran no analyzer and no model. Analysis happens only if you explicitly click “Analyze retrieved source” below, and only for a single complete Solidity source file.',
      'deployed-disclaimer'
    )
  );
}

// ---------------------------------------------------------------------------
// Phase 6.3 — analyze the retrieved verified source (explicit action only).
//
// This is a SEPARATE action from the pasted-code audit, reusing the SAME
// backend analyzer. Nothing here runs on lookup completion, address change,
// network change, or result load: the analyzer runs only when the user clicks
// "Analyze retrieved source", mirroring the pasted "Run Slither" button (the
// click is the approval). It analyzes ONLY the sources stored on the current
// `deployedState.lookup`, so a run is provably tied to one verified lookup, and
// `clearDeployedAnalysis()` wipes results the moment that association changes.
//
// Scope honesty: the local adapter analyzes ONE Solidity source unit compiled
// with local solc. Multi-file verified contracts, truncated (incomplete)
// sources, empty sources, and non-Solidity sources are refused with a plain
// explanation and are NEVER partially analyzed — unanalyzed source is never
// rendered as if it had been audited. Qwen only *explains* an already-existing
// `kind:"tool"` finding; it can never create or change a finding, severity,
// confidence, or location.
// ---------------------------------------------------------------------------

const DEPLOYED_ANALYSIS_DEFAULT_NOTE =
  'Retrieve a verified contract’s source first. The analyzers run only when you click their button — never automatically, and never on lookup completion, address change, network change, or result load.';

// Decides whether the CURRENT lookup may be analyzed, and returns the exact
// user-facing note. This is the single place multi-file / truncated / incomplete
// / unsupported source is marked unsupported instead of being partially run.
function deployedAnalysisEligibility() {
  const lk = deployedState.lookup;
  if (!lk) return { eligible: false, note: DEPLOYED_ANALYSIS_DEFAULT_NOTE };
  if (deployedState.lookupStale) {
    return {
      eligible: false,
      note: 'The address or network changed after this lookup, so its source is no longer current. Retrieve the verified source again before analyzing.',
    };
  }
  if (lk.status !== 'verified' || lk.verified !== true || !Array.isArray(lk.sources) || lk.sources.length === 0) {
    return {
      eligible: false,
      note: 'No verified source is loaded for this contract, so there is nothing to analyze. Unverified or unavailable source is never analyzed.',
    };
  }
  if (lk.sources_truncated) {
    return {
      eligible: false,
      note: 'The retrieved source is incomplete (truncated by the size cap), so it was NOT analyzed — partial source is not a valid audit.',
    };
  }
  const lang = lk.language ? String(lk.language).trim().toLowerCase() : 'solidity';
  if (lang !== 'solidity') {
    return {
      eligible: false,
      note: `The verified source is ${lk.language}, not Solidity. The local analyzer is Solidity-only, so it was NOT analyzed.`,
    };
  }
  const files = lk.sources;
  if (files.length > 1) {
    return {
      eligible: false,
      note: `This verified contract has ${files.length} source files. The existing local analyzer analyzes a single Solidity source file and cannot safely compile a multi-file project, so multi-file analysis is unsupported in this phase and will NOT run. No file is analyzed and no partial run is presented as a full contract audit.`,
    };
  }
  if (!files[0] || !String(files[0].content || '').trim()) {
    return {
      eligible: false,
      note: 'The single verified source file is empty, so there is nothing to analyze.',
    };
  }
  if (!slitherAvailable && !solhintAvailable) {
    return {
      eligible: false,
      note: 'Neither local analyzer (Slither, Solhint) is available, so the retrieved source cannot be analyzed.',
    };
  }
  return {
    eligible: true,
    note: 'One verified Solidity source file, complete (not truncated). Click to run the local analyzers on it — the same analyzers used for pasted code. Each tool reports its own status; the model is never invoked here.',
  };
}

// Wipes every retrieved-source analysis artefact (results, raw output,
// selection, explanation). Called whenever the association changes so a stale
// run can never linger.
function clearDeployedAnalysis() {
  deployedState.analysis = null;
  deployedState.selectedFinding = null;
  const box = document.getElementById('deployedAnalysis');
  if (box) { box.classList.add('hidden'); box.innerHTML = ''; }
  const sel = document.getElementById('deployedSelectedFinding');
  if (sel) sel.textContent = 'No finding selected.';
  const exBtn = document.getElementById('explainDeployedFindingBtn');
  if (exBtn) exBtn.disabled = true;
  const exContent = document.getElementById('deployedExplainContent');
  if (exContent) { exContent.textContent = ''; exContent.classList.add('hidden'); }
  const exError = document.getElementById('deployedExplainError');
  if (exError) exError.classList.add('hidden');
  const exPanel = document.getElementById('deployedExplainPanel');
  if (exPanel) exPanel.classList.add('hidden');
  updateDeployedAnalyzeButton();
}

// Syncs the analyze button + its explanatory note with eligibility. Toggling a
// disabled button is never a run.
function updateDeployedAnalyzeButton() {
  const btn = document.getElementById('deployedAnalyzeBtn');
  const note = document.getElementById('deployedAnalyzeNote');
  const info = deployedAnalysisEligibility();
  if (note) note.textContent = info.note;
  if (btn) btn.disabled = !info.eligible || deployedState.analysisBusy;
}

function selectDeployedFinding(f) {
  deployedState.selectedFinding = f;
  const sel = document.getElementById('deployedSelectedFinding');
  if (sel) sel.textContent = `${f.check} @ ${locationLabel(f)} (sev:${f.severity}, conf:${f.confidence}) — selected`;
  const btn = document.getElementById('explainDeployedFindingBtn');
  if (btn) btn.disabled = false;
}

// Renders the analyzer response for the retrieved source. A refusal (nothing
// ran) is shown as text with NO findings; a run shows provenance, each
// analyzer's own status, and the deterministic kind:"tool" findings. SANITIZED:
// text nodes only.
function renderDeployedAnalysis(data) {
  const box = document.getElementById('deployedAnalysis');
  const exPanel = document.getElementById('deployedExplainPanel');
  if (!box) return;
  deployedState.analysis = data;
  deployedState.selectedFinding = null;
  box.classList.remove('hidden');
  box.innerHTML = '';

  // Reset the explain surface.
  const sel = document.getElementById('deployedSelectedFinding');
  if (sel) sel.textContent = 'No finding selected.';
  const exBtn = document.getElementById('explainDeployedFindingBtn');
  if (exBtn) exBtn.disabled = true;
  const exContent = document.getElementById('deployedExplainContent');
  if (exContent) { exContent.textContent = ''; exContent.classList.add('hidden'); }
  const exError = document.getElementById('deployedExplainError');
  if (exError) exError.classList.add('hidden');
  if (exPanel) exPanel.classList.add('hidden');

  // --- Refused: nothing ran. Render the reason only; never any findings. ---
  if (data.allowed !== true) {
    box.appendChild(_deployedText('p', data.reason || 'This retrieved source was not analyzed.', 'deployed-warn'));
    if (data.limitation) box.appendChild(_deployedText('p', data.limitation, 'deployed-disclaimer'));
    box.appendChild(_deployedText('p', 'No analyzer ran. Nothing was fabricated and no partial result is presented.', 'deployed-disclaimer'));
    return;
  }

  // --- Provenance: exactly which source produced this run. ---
  const prov = document.createElement('p');
  prov.className = 'deployed-analysis-prov';
  const where = `${data.chain_name || ('chain ' + data.chain_id)} · ${data.checksum || data.address}`;
  prov.textContent = `Analyzed retrieved source for ${where} — ${data.source_file_count} file: ${data.source_path}. Findings below are deterministic analyzer output (kind:"tool"), not model output.`;
  box.appendChild(prov);

  // --- One block per analyzer that ran (authoritative, per-tool status). ---
  const runs = [data.analyzer, data.solhint].filter((r) => r && typeof r === 'object');
  if (runs.length === 0) {
    box.appendChild(_deployedText('p', 'No analyzer reported a result for this run.', 'deployed-warn'));
  }
  runs.forEach((run) => {
    const okStatus = run.status === 'ok' || run.status === 'no_findings';
    const st = document.createElement('div');
    st.className = `deployed-analysis-analyzer ${okStatus ? 'ok' : 'warn'}`;
    const stStrong = document.createElement('strong');
    stStrong.textContent = `${run.tool || 'analyzer'}${run.tool_version ? ' ' + run.tool_version : ''}: ${run.status}`;
    st.appendChild(stStrong);
    box.appendChild(st);
    if (run.summary) box.appendChild(_deployedText('p', run.summary, 'deployed-message'));
    if (run.detail) box.appendChild(_deployedText('p', run.detail, 'deployed-warn'));
  });

  // --- Findings (kind:"tool" only), grouped by tool inside one selection scope. ---
  const cont = document.createElement('div');
  cont.className = 'findings';
  cont.id = 'deployedFindings';
  let totalFindings = 0;
  runs.forEach((run) => {
    const rf = Array.isArray(run.findings) ? run.findings : [];
    if (rf.length === 0) return;
    const head = document.createElement('div');
    head.className = 'tool-group-head';
    head.textContent = `${run.tool || 'analyzer'} findings`;
    cont.appendChild(head);
    rf.forEach((f) => {
      cont.appendChild(renderFindingCard(f, { scopeSel: '#deployedFindings', onSelect: selectDeployedFinding }));
    });
    totalFindings += rf.length;
  });
  if (totalFindings > 0) {
    box.appendChild(cont);
    if (exPanel) exPanel.classList.remove('hidden');
  } else {
    cont.classList.add('hidden');
  }

  // --- Raw output (bounded, paths redacted), one block per tool. ---
  runs.forEach((run) => {
    if (!run.raw_output) return;
    const det = document.createElement('details');
    det.className = 'raw-wrap';
    const sum = document.createElement('summary');
    sum.textContent = `Raw ${run.tool || 'analyzer'} output (bounded, paths redacted)`;
    det.appendChild(sum);
    const pre = document.createElement('pre');
    pre.className = 'code';
    pre.textContent = run.raw_output + (run.raw_output_truncated ? '\n\n[... raw output truncated ...]' : '');
    det.appendChild(pre);
    box.appendChild(det);
  });

  // --- Tool-specific limitations / disclaimers. ---
  runs.forEach((run) => {
    box.appendChild(_deployedText('p', run.notice || 'Deterministic analyzer output only; a clean run does not prove the contract is safe.', 'deployed-disclaimer'));
  });
}

// The ONLY function that runs the analyzer on retrieved source. Called solely by
// the explicit "Analyze retrieved source" button. It sends the EXACT sources of
// the current lookup (association) and performs no lookup of its own.
async function runDeployedAnalysis() {
  const lk = deployedState.lookup;
  const errEl = document.getElementById('deployedAnalysisError');
  if (errEl) errEl.classList.add('hidden');
  const info = deployedAnalysisEligibility();
  if (!info.eligible || !lk) return;

  deployedState.analysisBusy = true;
  updateDeployedAnalyzeButton();
  const spinner = document.getElementById('deployedAnalysisSpinner');
  if (spinner) spinner.classList.remove('hidden');
  const timer = startElapsed('deployedAnalysisElapsed');
  let ok = true;
  try {
    const res = await fetch('/api/deployed/analyze-source', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        address: lk.checksum || lk.address,
        chain_id: lk.chain_id,
        sources: lk.sources || [],
        sources_truncated: !!lk.sources_truncated,
        language: lk.language,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'retrieved-source analysis failed');
    renderDeployedAnalysis(data);
  } catch (e) {
    ok = false;
    if (errEl) { errEl.textContent = e.message; errEl.classList.remove('hidden'); }
  } finally {
    if (spinner) spinner.classList.add('hidden');
    if (timer) stopElapsed(timer, 'deployedAnalysisElapsed');
    deployedState.analysisBusy = false;
    updateDeployedAnalyzeButton();
    // Refresh health so tool availability stays accurate (mirrors paste flow).
    if (ok) await checkHealth();
  }
}

async function explainDeployedFinding() {
  const lk = deployedState.lookup;
  const f = deployedState.selectedFinding;
  const errEl = document.getElementById('deployedExplainError');
  if (errEl) errEl.classList.add('hidden');
  if (!lk || !f) return;
  const source = Array.isArray(lk.sources) && lk.sources[0] && typeof lk.sources[0].content === 'string'
    ? lk.sources[0].content
    : '';
  if (!source.trim()) {
    if (errEl) { errEl.textContent = 'The retrieved source is not available for explanation.'; errEl.classList.remove('hidden'); }
    return;
  }
  const btn = document.getElementById('explainDeployedFindingBtn');
  const contentEl = document.getElementById('deployedExplainContent');
  const spinEl = document.getElementById('deployedExplainSpinner');
  const modelSelect = document.getElementById('modelSelect');
  if (btn) btn.disabled = true;
  if (contentEl) { contentEl.textContent = ''; contentEl.classList.remove('hidden'); }
  if (spinEl) spinEl.classList.remove('hidden');
  const timer = startElapsed('deployedExplainElapsed');
  try {
    const res = await fetch('/api/explain-finding', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ source, model: modelSelect ? modelSelect.value : null, finding: f }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'explain finding failed');
    if (contentEl) contentEl.textContent = data.content + (data.notice ? '\n\n---\n' + data.notice : '');
    deployedState.selectedFinding = f; // keep association after async completes
  } catch (e) {
    if (errEl) { errEl.textContent = e.message; errEl.classList.remove('hidden'); }
  } finally {
    if (spinEl) spinEl.classList.add('hidden');
    if (btn) btn.disabled = !deployedState.selectedFinding;
    if (timer) stopElapsed(timer, 'deployedExplainElapsed');
    await checkHealth();
  }
}

// The ONLY function that performs the outbound lookup. Called solely by the
// explicit button, and only when consent + Ethereum + a valid address are set.
async function runDeployedLookup() {
  const addressInput = document.getElementById('addressInput');
  const status = document.getElementById('deployedStatus');
  const box = document.getElementById('deployedResult');
  if (!addressInput) return;
  const address = addressInput.value.trim();

  deployedState.busy = true;
  // Starting a new lookup invalidates the previous lookup's association and
  // clears any analysis produced from it (nothing is analyzed during the fetch).
  deployedState.lookup = null;
  clearDeployedAnalysis();
  updateDeployedButton();
  updateDeployedAnalyzeButton();
  if (status) status.textContent = 'Retrieving verified source from Sourcify…';
  if (box) {
    box.hidden = false;
    box.innerHTML = '';
    box.appendChild(_deployedText('p', 'Retrieving…', 'deployed-message'));
  }
  try {
    const res = await fetch('/api/deployed/lookup', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        address: address,
        chain_id: deployedState.chainId ? Number(deployedState.chainId) : null,
        consent: deployedState.consent,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'lookup failed');
    renderDeployedResult(data);
    if (status) {
      status.textContent = data.lookup_performed
        ? 'Lookup complete (read-only). No analyzer or model ran — analysis is a separate, explicit action.'
        : 'Nothing was retrieved. Check the message above.';
    }
  } catch (e) {
    if (box) {
      box.hidden = false;
      box.innerHTML = '';
      box.appendChild(_deployedText('p', 'Lookup failed (backend not reachable). Nothing was retrieved.', 'deployed-warn'));
    }
    if (status) status.textContent = 'Lookup failed. Nothing was retrieved.';
  } finally {
    deployedState.busy = false;
    updateDeployedButton();
  }
}

function initDeployedPanel() {
  const select = document.getElementById('chainSelect');
  const addressInput = document.getElementById('addressInput');
  const consent = document.getElementById('deployedConsent');
  const lookupBtn = document.getElementById('deployedLookupBtn');
  if (!select && !addressInput && !consent && !lookupBtn) return;

  loadDeployedConfig();

  if (select) {
    select.addEventListener('change', () => {
      deployedState.chainId = select.value;
      // Changing network invalidates the current lookup's association, so any
      // retrieved-source analysis is cleared and cannot be attributed to the
      // newly selected network. No request is made and no analyzer runs.
      deployedState.lookupStale = true;
      clearDeployedAnalysis();
      updateDeployedButton();
    });
  }

  if (addressInput) {
    let debounce = null;
    addressInput.addEventListener('input', () => {
      const value = addressInput.value;
      // Changing the address invalidates the current lookup's association.
      deployedState.lookupStale = true;
      clearDeployedAnalysis();
      if (debounce) clearTimeout(debounce);
      debounce = setTimeout(() => { validateDeployedAddress(value); }, 250);
    });
  }

  // Consent is UI state only here. Ticking it performs NO request — it only
  // enables the explicit Retrieve & review button.
  if (consent) {
    consent.addEventListener('change', () => {
      deployedState.consent = consent.checked;
      const status = document.getElementById('deployedStatus');
      if (status) {
        status.textContent = consent.checked
          ? 'Consent recorded (local UI state only). Click Retrieve & review to fetch the verified source.'
          : 'Consent is off. Nothing is fetched until you tick consent and click Retrieve & review.';
      }
      updateDeployedButton();
    });
  }

  if (lookupBtn) {
    lookupBtn.addEventListener('click', runDeployedLookup);
  }

  // Phase 6.3 — explicit actions on the retrieved source. These are the ONLY
  // entry points that run the analyzer / model for a deployed contract.
  const analyzeBtn = document.getElementById('deployedAnalyzeBtn');
  if (analyzeBtn) {
    analyzeBtn.addEventListener('click', runDeployedAnalysis);
  }
  const explainDeployedBtn = document.getElementById('explainDeployedFindingBtn');
  if (explainDeployedBtn) {
    explainDeployedBtn.addEventListener('click', explainDeployedFinding);
  }

  updateDeployedButton();
  updateDeployedAnalyzeButton();
}

document.addEventListener('DOMContentLoaded', () => {
  checkHealth();
  initDeployedPanel();

  const input = document.getElementById('solidityInput');
  const sampleBtn = document.getElementById('sampleBtn');
  const clearBtn = document.getElementById('clearBtn');
  const reviewBtn = document.getElementById('reviewBtn');
  const suggestBtn = document.getElementById('suggestBtn');
  const modelSelect = document.getElementById('modelSelect');
  const charCount = document.getElementById('charCount');

  input.addEventListener('input', () => {
    charCount.textContent = `${input.value.length} chars`;
  });

  sampleBtn.addEventListener('click', () => {
    input.value = SAMPLE;
    charCount.textContent = `${input.value.length} chars`;
  });

  clearBtn.addEventListener('click', () => {
    input.value = '';
    charCount.textContent = '0 chars';
    document.getElementById('reviewContent').textContent = 'Run a review to see results here.';
    document.getElementById('reviewContent').classList.remove('hidden');
    document.getElementById('copyReviewBtn').disabled = true;
    document.getElementById('suggestNotes').textContent = '';
    document.getElementById('suggestCode').textContent = '';
    document.getElementById('suggestCode').classList.add('hidden');
    document.getElementById('copySuggestBtn').disabled = true;
    chatHistory = [];
    renderChat();
    clearProposal();
    resetSlitherPanel();
    lastExplain = null; // the source is gone, so any explanation no longer applies
    lastReview = null; // ditto for the Qwen review used by the export
    clearError(document.getElementById('reviewPanel'));
    clearError(document.getElementById('suggestPanel'));
    clearError(document.getElementById('chatPanel'));
  });

  // Run Slither — independent of the Qwen review flow. On any failure the
  // source text is never modified (only Load sample / Clear change it).
  const slitherBtn = document.getElementById('slitherBtn');
  const exportReportBtn = document.getElementById('exportReportBtn');
  slitherBtn.addEventListener('click', async () => {
    clearError(document.getElementById('slitherPanel'));
    if (!input.value.trim()) {
      setError(document.getElementById('slitherPanel'), 'Enter Solidity source first.');
      return;
    }
    slitherBtn.disabled = true;
    if (typeof exportReportBtn !== 'undefined' && exportReportBtn) exportReportBtn.disabled = true;
    const summary = document.getElementById('slitherSummary');
    const cont = document.getElementById('slitherFindings');
    summary.textContent = '';
    cont.innerHTML = '';
    cont.classList.add('hidden');
    document.getElementById('slitherRaw').textContent = '';
    document.getElementById('slitherRawWrap').classList.add('hidden');
    document.getElementById('slitherSpinner').classList.remove('hidden');
    const timer = startElapsed('slitherElapsed');
    lastSlitherData = null;
    lastExplain = null;
    try {
      const res = await fetch('/api/slither', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source: input.value}),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Slither request failed');
      }
      lastSlitherData = data;
      const exBtn = document.getElementById('exportReportBtn');
      if (exBtn) exBtn.disabled = false; // export works even when findings are empty
      renderSlither(data);
    } catch (e) {
      summary.textContent = 'The Slither run could not be completed. Your source is unchanged.';
      setError(document.getElementById('slitherPanel'), e.message);
      const exBtn = document.getElementById('exportReportBtn');
      if (exBtn) exBtn.disabled = false;
    } finally {
      document.getElementById('slitherSpinner').classList.add('hidden');
      stopElapsed(timer, 'slitherElapsed');
      await checkHealth();
    }
  });

  // Run Solhint — a second, independent local analyzer. Like Slither it runs
  // only on this explicit click, on a temp copy of the source, and never
  // modifies the source text. Its state is independent of Slither's.
  const solhintBtn = document.getElementById('solhintBtn');
  if (solhintBtn) {
    solhintBtn.addEventListener('click', async () => {
      clearError(document.getElementById('slitherPanel'));
      if (!input.value.trim()) {
        setError(document.getElementById('slitherPanel'), 'Enter Solidity source first.');
        return;
      }
      solhintBtn.disabled = true;
      if (exportReportBtn) exportReportBtn.disabled = true;
      const soSummary = document.getElementById('solhintSummary');
      const soCont = document.getElementById('solhintFindings');
      soSummary.textContent = '';
      soCont.innerHTML = '';
      soCont.classList.add('hidden');
      document.getElementById('solhintRaw').textContent = '';
      document.getElementById('solhintRawWrap').classList.add('hidden');
      document.getElementById('slitherSpinner').classList.remove('hidden');
      const timer = startElapsed('slitherElapsed');
      lastSolhintData = null;
      lastExplain = null;
      try {
        const res = await fetch('/api/solhint', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({source: input.value}),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Solhint request failed');
        }
        lastSolhintData = data;
        renderSolhint(data);
        if (exportReportBtn) exportReportBtn.disabled = false;
      } catch (e) {
        soSummary.textContent = 'The Solhint run could not be completed. Your source is unchanged.';
        setError(document.getElementById('slitherPanel'), e.message);
        if (exportReportBtn) exportReportBtn.disabled = false;
      } finally {
        document.getElementById('slitherSpinner').classList.add('hidden');
        stopElapsed(timer, 'slitherElapsed');
        await checkHealth();
      }
    });
  }

  reviewBtn.addEventListener('click', async () => {
    clearError(document.getElementById('reviewPanel'));
    if (!input.value.trim()) {
      setError(document.getElementById('reviewPanel'), 'Enter Solidity source first.');
      return;
    }
    reviewBtn.disabled = true;
    suggestBtn.disabled = true;
    chatBtn.disabled = true;
    document.getElementById('reviewContent').textContent = '';
    document.getElementById('reviewSpinner').classList.remove('hidden');
    document.getElementById('copyReviewBtn').disabled = true;
    const timer = startElapsed('reviewElapsed');
    try {
      const res = await fetch('/api/review', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source: input.value, model: modelSelect.value}),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'review failed');
      }
      renderReview(data.content);
      lastExplain = {model: data.model, content: data.content, elapsed_ms: data.elapsed_ms};
      lastReview = {model: data.model, content: data.content}; // separate AI section in the export
    } catch (e) {
      setError(document.getElementById('reviewPanel'), e.message);
    } finally {
      document.getElementById('reviewSpinner').classList.add('hidden');
      stopElapsed(timer, 'reviewElapsed');
      reviewBtn.disabled = false;
      suggestBtn.disabled = false;
      chatBtn.disabled = false;
      await checkHealth();
    }
  });

  suggestBtn.addEventListener('click', async () => {
    clearError(document.getElementById('suggestPanel'));
    if (!input.value.trim()) {
      setError(document.getElementById('suggestPanel'), 'Enter Solidity source first.');
      return;
    }
    suggestBtn.disabled = true;
    reviewBtn.disabled = true;
    chatBtn.disabled = true;
    document.getElementById('suggestNotes').textContent = '';
    document.getElementById('suggestCode').textContent = '';
    document.getElementById('suggestCode').classList.add('hidden');
    document.getElementById('copySuggestBtn').disabled = true;
    document.getElementById('suggestSpinner').classList.remove('hidden');
    const timer = startElapsed('suggestElapsed');
    try {
      const reviewText = document.getElementById('reviewContent').textContent;
      const res = await fetch('/api/suggest', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source: input.value, model: modelSelect.value, review: reviewText && reviewText !== 'Run a review to see results here.' ? reviewText : null}),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'suggest failed');
      }
      document.getElementById('suggestNotes').innerHTML = escapeHtml(data.notes).replace(/\n/g, '<br>');
      if (data.suggested_code) {
        document.getElementById('suggestCode').textContent = data.suggested_code;
        document.getElementById('suggestCode').classList.remove('hidden');
        document.getElementById('copySuggestBtn').disabled = false;
      } else {
        document.getElementById('suggestCode').classList.add('hidden');
        document.getElementById('copySuggestBtn').disabled = true;
      }
    } catch (e) {
      setError(document.getElementById('suggestPanel'), e.message);
    } finally {
      document.getElementById('suggestSpinner').classList.add('hidden');
      stopElapsed(timer, 'suggestElapsed');
      suggestBtn.disabled = false;
      reviewBtn.disabled = false;
      chatBtn.disabled = false;
      await checkHealth();
    }
  });

  document.getElementById('copyReviewBtn').addEventListener('click', async () => {
    const text = document.getElementById('reviewContent').textContent;
    try { await navigator.clipboard.writeText(text); } catch (e) {}
  });

  document.getElementById('copySuggestBtn').addEventListener('click', async () => {
    const text = document.getElementById('suggestCode').textContent;
    if (text) {
      try { await navigator.clipboard.writeText(text); } catch (e) {}
    }
  });

  document.getElementById('clearChatBtn').addEventListener('click', () => {
    chatHistory = [];
    renderChat();
    clearProposal();
    clearError(document.getElementById('chatPanel'));
  });

  const chatBtn = document.getElementById('chatBtn');
  const chatQuestion = document.getElementById('chatQuestion');

  chatBtn.addEventListener('click', async () => {
    clearError(document.getElementById('chatPanel'));
    if (!input.value.trim()) {
      setError(document.getElementById('chatPanel'), 'Enter Solidity source first.');
      return;
    }
    const q = chatQuestion.value.trim();
    if (!q) {
      setError(document.getElementById('chatPanel'), 'Ask a question.');
      return;
    }
    clearProposal();
    chatHistory.push({role: 'user', content: q});
    renderChat();
    chatQuestion.value = '';
    chatBtn.disabled = true;
    reviewBtn.disabled = true;
    suggestBtn.disabled = true;
    document.getElementById('chatSpinner').classList.remove('hidden');
    const timer = startElapsed('chatElapsed');
    const toolsEnabled = !!(document.getElementById('mcpToggle') || {}).checked;
    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({source: input.value, question: q, history: chatHistory.slice(0, -1), model: modelSelect.value, tools_enabled: toolsEnabled}),
      });
      const data = await res.json();
      if (!res.ok) {
        // Remove the optimistic user turn on error.
        chatHistory.pop();
        renderChat();
        throw new Error(data.detail || 'chat failed');
      }
      if (data.tool_proposal) {
        // Model wants a tool: show the approval prompt, do not run anything.
        renderProposal(data.tool_proposal, q);
      } else {
        chatHistory.push({role: 'assistant', content: data.reply});
        renderChat();
      }
    } catch (e) {
      setError(document.getElementById('chatPanel'), e.message);
    } finally {
      document.getElementById('chatSpinner').classList.add('hidden');
      stopElapsed(timer, 'chatElapsed');
      chatBtn.disabled = false;
      reviewBtn.disabled = false;
      suggestBtn.disabled = false;
    }
  });

  // Enter in chat sends (Shift+Enter newline; Ctrl+Enter/Meta+Enter send)
  chatQuestion.addEventListener('keydown', (ev) => {
    if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') {
      ev.preventDefault();
      chatBtn.click();
    }
  });

  // Explain finding button
  const explainBtn = document.getElementById('explainFindingBtn');
  if (explainBtn) {
    explainBtn.addEventListener('click', async () => {
      const errEl = document.getElementById('explainError');
      if (errEl) errEl.classList.add('hidden');
      if (!selectedFinding) {
        setError(document.getElementById('explainPanel') || document.getElementById('reviewPanel'), 'No finding selected.');
        return;
      }
      if (!input.value.trim()) {
        setError(document.getElementById('explainPanel') || document.getElementById('reviewPanel'), 'Enter Solidity source first.');
        return;
      }
      explainBtn.disabled = true;
      const contentEl = document.getElementById('explainContent');
      const spinEl = document.getElementById('explainSpinner');
      if (contentEl) { contentEl.textContent = ''; contentEl.classList.remove('hidden'); }
      if (spinEl) spinEl.classList.remove('hidden');
      const timer = startElapsed ? startElapsed('explainElapsed') : null;
      try {
        const res = await fetch('/api/explain-finding', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({source: input.value, model: modelSelect.value, finding: selectedFinding}),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'explain finding failed');
        }
        if (contentEl) {
          contentEl.textContent = data.content + (data.notice ? '\n\n---\n' + data.notice : '');
        }
        lastExplain = data;
      } catch (e) {
        if (errEl) { errEl.textContent = e.message; errEl.classList.remove('hidden'); }
      } finally {
        if (spinEl) spinEl.classList.add('hidden');
        explainBtn.disabled = false;
        if (timer && stopElapsed) stopElapsed(timer, 'explainElapsed');
        await checkHealth();
      }
    });
  }

  // Export report button
  const exportBtn = document.getElementById('exportReportBtn');
  if (exportBtn) {
    exportBtn.addEventListener('click', async () => {
      clearError(document.getElementById('slitherPanel'));
      if (!input.value.trim()) {
        setError(document.getElementById('slitherPanel'), 'Enter Solidity source first.');
        return;
      }
      const findings = lastSlitherData && Array.isArray(lastSlitherData.findings) ? lastSlitherData.findings : [];
      const explanations = {};
      if (lastExplain && selectedFinding) {
        const fid = selectedFinding.id || selectedFinding.check;
        if (fid) explanations[fid] = lastExplain.content;
      }
      // One entry per analyzer: the tool that ran (with its real status and
      // findings) or an explicit "not run for this report" record — never a
      // silent omission and never a merged finding list.
      const toRun = (d) => ({
        tool: d.tool,
        status: d.status,
        tool_version: d.tool_version || null,
        summary: d.summary || '',
        findings: Array.isArray(d.findings) ? d.findings : [],
      });
      const notRun = (tool) => ({
        tool,
        status: 'skipped',
        tool_version: null,
        summary: `${tool} was not run for this report, so no results are attributed to it.`,
        findings: [],
      });
      const tool_runs = [
        lastSlitherData ? toRun(lastSlitherData) : notRun('slither'),
        lastSolhintData ? toRun(lastSolhintData) : notRun('solhint'),
      ];
      try {
        const res = await fetch('/api/export-report', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            source: input.value,
            findings,
            explanations,
            tool_runs,
            qwen_review: lastReview ? lastReview.content : null,
            qwen_model: lastReview ? (lastReview.model || null) : null,
            scope: 'pasted source (submitted in the UI)',
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'export report failed');
        }
        const blob = new Blob([data.report], {type: 'text/markdown'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = data.filename || 'auditor-report.md';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      } catch (e) {
        setError(document.getElementById('slitherPanel'), e.message);
      }
    });
  }

  // Initial char count
  charCount.textContent = '0 chars';
  renderChat();
});
