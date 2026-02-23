/**
 * NEXUS Tablet UI
 *
 * Auth flow:   Azure AD MSAL → get JWT → attach to all API calls
 * Audio flow:  getUserMedia → AudioWorklet (PCM 16kHz) → WebSocket binary frames
 * Events flow: WebSocket JSON events → update UI panels
 */

// ── Config ────────────────────────────────────────────────────────────────────
const API_BASE = window.location.origin + '/api/v1';
const WS_BASE  = (window.location.protocol === 'https:' ? 'wss' : 'ws') + '://' + window.location.host;

// Azure AD MSAL config — values injected at build time or read from a config endpoint
// For now, read from window.NEXUS_CONFIG (injected by server) or fall back to empty
const MSAL_CONFIG = {
  auth: {
    clientId:    (window.NEXUS_CONFIG && window.NEXUS_CONFIG.clientId)    || 'REPLACE_CLIENT_ID',
    authority:   (window.NEXUS_CONFIG && window.NEXUS_CONFIG.authority)   || 'https://login.microsoftonline.com/REPLACE_TENANT_ID',
    redirectUri: window.location.origin,
  },
};

// ── State ─────────────────────────────────────────────────────────────────────
let _token        = null;
let _sessionId    = null;
let _ws           = null;
let _mediaStream  = null;
let _audioCtx     = null;
let _processor    = null;
let _lastContribId = null;   // for facilitator override

// ── DOM refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const authOverlay    = $('auth-overlay');
const setupOverlay   = $('setup-overlay');
const app            = $('app');
const transcriptFeed = $('transcript-feed');
const interimEl      = $('transcript-interim');
const nexusOutput    = $('nexus-output');
const latencyBadge   = $('latency-badge');
const alertBanner    = $('alert-banner');
const micDot         = $('mic-indicator');
const energyBadge    = $('energy-badge');
const sessionLabel   = $('session-label');
const activityLabel  = $('activity-label');
const overrideRow    = $('override-row');

// ── Auth (MSAL) ───────────────────────────────────────────────────────────────

/**
 * Attempt silent MSAL auth first; fall back to redirect if needed.
 * If MSAL is not available (development / no Azure), fall back to a
 * prompt-based token entry so the UI can still be tested.
 */
async function initAuth() {
  if (typeof msal !== 'undefined') {
    const msalApp = new msal.PublicClientApplication(MSAL_CONFIG);
    await msalApp.initialize();

    // Check if returning from redirect
    const result = await msalApp.handleRedirectPromise();
    if (result) {
      _token = result.accessToken;
      showSetup();
      return;
    }

    // Try silent
    const accounts = msalApp.getAllAccounts();
    if (accounts.length > 0) {
      try {
        const silent = await msalApp.acquireTokenSilent({
          account: accounts[0],
          scopes: [`api://${MSAL_CONFIG.auth.clientId}/Nexus.Facilitator`],
        });
        _token = silent.accessToken;
        showSetup();
        return;
      } catch (_) { /* fall through to redirect */ }
    }

    $('btn-login').addEventListener('click', () => {
      msalApp.loginRedirect({
        scopes: [`api://${MSAL_CONFIG.auth.clientId}/Nexus.Facilitator`],
      });
    });
  } else {
    // Dev mode: prompt for token
    $('btn-login').textContent = 'Enter dev token';
    $('btn-login').addEventListener('click', () => {
      const t = prompt('Paste your Azure AD Bearer token:');
      if (t) { _token = t; showSetup(); }
    });
  }
}

function showSetup() {
  authOverlay.classList.add('hidden');
  setupOverlay.classList.remove('hidden');
}

function showApp(clientName) {
  setupOverlay.classList.add('hidden');
  app.classList.remove('hidden');
  sessionLabel.textContent = clientName.toUpperCase();
}

// ── Session lifecycle ─────────────────────────────────────────────────────────

$('btn-create-session').addEventListener('click', async () => {
  const clientName = $('input-client').value.trim();
  const currentX   = $('input-x').value.trim();
  if (!clientName) { alert('Client name is required.'); return; }

  const session = await api('POST', '/sessions', { client_name: clientName, current_x: currentX || null });
  _sessionId = session.id;

  await api('POST', `/sessions/${_sessionId}/start`);
  showApp(clientName);
  await connectAudio();
  connectWebSocket();
});

$('btn-end-session').addEventListener('click', async () => {
  if (!confirm('End this session? This will trigger synthesis.')) return;
  disconnectAudio();
  if (_ws) _ws.close();
  await api('POST', `/sessions/${_sessionId}/end`);
  await triggerSynthesis();
});

// ── Audio capture ─────────────────────────────────────────────────────────────

async function connectAudio() {
  try {
    _mediaStream = await navigator.mediaDevices.getUserMedia({ audio: {
      channelCount: 1,
      sampleRate: 16000,
      echoCancellation: true,
      noiseSuppression: true,
    }});

    _audioCtx = new AudioContext({ sampleRate: 16000 });
    const source = _audioCtx.createMediaStreamSource(_mediaStream);

    // Use ScriptProcessorNode (widely supported on tablets)
    // Production: replace with AudioWorklet for lower latency
    _processor = _audioCtx.createScriptProcessor(4096, 1, 1);
    _processor.onaudioprocess = (e) => {
      if (!_ws || _ws.readyState !== WebSocket.OPEN) return;
      const float32 = e.inputBuffer.getChannelData(0);
      const pcm16   = float32ToPcm16(float32);
      _ws.send(pcm16.buffer);
    };

    source.connect(_processor);
    _processor.connect(_audioCtx.destination);

    micDot.classList.remove('off');
    micDot.classList.add('active');
  } catch (err) {
    console.error('Microphone access denied:', err);
    alert('Microphone access is required. Please allow microphone access and reload.');
  }
}

function disconnectAudio() {
  if (_processor)    { _processor.disconnect(); _processor = null; }
  if (_audioCtx)     { _audioCtx.close(); _audioCtx = null; }
  if (_mediaStream)  { _mediaStream.getTracks().forEach(t => t.stop()); _mediaStream = null; }
  micDot.classList.remove('active');
  micDot.classList.add('off');
}

function float32ToPcm16(float32Array) {
  const pcm = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const clamped = Math.max(-1, Math.min(1, float32Array[i]));
    pcm[i] = clamped < 0 ? clamped * 32768 : clamped * 32767;
  }
  return pcm;
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

function connectWebSocket() {
  const url = `${WS_BASE}/sessions/${_sessionId}/stream?token=${encodeURIComponent(_token)}`;
  _ws = new WebSocket(url);
  _ws.binaryType = 'arraybuffer';

  _ws.onopen = () => console.log('[NEXUS] WebSocket connected');
  _ws.onclose = () => console.log('[NEXUS] WebSocket closed');
  _ws.onerror = (e) => console.error('[NEXUS] WebSocket error', e);
  _ws.onmessage = (msg) => {
    if (typeof msg.data === 'string') handleServerEvent(JSON.parse(msg.data));
  };

  // Keep-alive ping every 30s
  setInterval(() => {
    if (_ws && _ws.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: 'ping' }));
    }
  }, 30000);
}

// ── Event handlers ────────────────────────────────────────────────────────────

function handleServerEvent(event) {
  switch (event.type) {
    case 'transcript_interim':
      interimEl.textContent = event.text;
      break;

    case 'transcript_final':
      interimEl.textContent = '';
      appendTranscript(event.text, event.speaker);
      break;

    case 'assumption_captured':
      appendStateItem('assumptions', event.assumption, 'assumption', 'ASSUMPTION', event.assumption.text, event.assumption.status);
      break;

    case 'tension_captured':
      appendStateItem('tensions', event.tension, 'tension', 'TENSION', event.tension.description, event.tension.status);
      break;

    case 'key_moment_flagged':
      appendStateItem('moments', event.moment, 'moment', event.moment.moment_type, event.moment.description, null);
      if (event.alert) showAlert(`Key moment: ${event.moment.description}`);
      break;

    case 'energy_updated':
      updateEnergy(event.level);
      break;
  }
}

// ── Transcript ────────────────────────────────────────────────────────────────

function appendTranscript(text, speaker) {
  const el = document.createElement('div');
  el.className = 'transcript-entry';
  el.innerHTML = speaker
    ? `<div class="speaker">${escHtml(speaker)}</div>${escHtml(text)}`
    : escHtml(text);
  transcriptFeed.appendChild(el);
  transcriptFeed.scrollTop = transcriptFeed.scrollHeight;
}

// ── State panels ──────────────────────────────────────────────────────────────

function appendStateItem(listId, data, cssClass, label, text, status) {
  const list = $(`${listId}-list`);
  // Remove empty placeholder
  const empty = list.querySelector('.state-empty');
  if (empty) empty.remove();

  const el = document.createElement('div');
  el.className = `state-item ${cssClass}`;
  el.dataset.id = data.id;
  el.innerHTML = `
    <div class="item-label">${escHtml(label)}</div>
    <div>${escHtml(text)}</div>
    ${status ? `<div class="item-status">${escHtml(status)}</div>` : ''}
  `;
  list.prepend(el);
}

// ── Commands ─────────────────────────────────────────────────────────────────

document.querySelectorAll('.cmd-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const cmd = btn.dataset.cmd;
    btn.classList.add('loading');
    try {
      const result = await api('POST', `/sessions/${_sessionId}/command`, { command: cmd });
      showNexusResponse(cmd, result.output, result.latency_ms);
    } catch (err) {
      showNexusResponse(cmd, `Error: ${err.message}`, null);
    } finally {
      btn.classList.remove('loading');
    }
  });
});

function showNexusResponse(command, output, latencyMs) {
  nexusOutput.innerHTML = `
    <div class="nexus-response">
      <div class="cmd-label">${escHtml(command.toUpperCase())}</div>
      <div class="content">${escHtml(output)}</div>
    </div>
  `;
  if (latencyMs !== null) {
    latencyBadge.textContent = `${latencyMs}ms`;
    latencyBadge.classList.remove('hidden');
  }
  overrideRow.classList.remove('hidden');
}

// ── Facilitator override ──────────────────────────────────────────────────────

$('btn-override').addEventListener('click', async () => {
  const text = $('override-input').value.trim();
  if (!text || !_lastContribId) return;
  await api('POST', `/sessions/${_sessionId}/override`, {
    contribution_id: _lastContribId,
    override_text: text,
  });
  $('override-input').value = '';
  overrideRow.classList.add('hidden');
});

// ── Phase / activity ──────────────────────────────────────────────────────────

const PHASES = ['REFRAME', 'EXPLORE', 'SHAPE'];
let _currentPhase = 'REFRAME';
let _currentActivity = 1;

$('btn-advance').addEventListener('click', () => {
  $('advance-modal').classList.remove('hidden');
});

$('btn-advance-cancel').addEventListener('click', () => {
  $('advance-modal').classList.add('hidden');
});

$('btn-advance-confirm').addEventListener('click', async () => {
  const summary = $('advance-summary').value.trim();
  if (!summary) { alert('Please provide a summary.'); return; }

  await api('POST', `/sessions/${_sessionId}/advance`, { context_summary: summary });
  $('advance-summary').value = '';
  $('advance-modal').classList.add('hidden');

  // Update local display (server is source of truth, but mirror locally for speed)
  if (_currentActivity < 4) {
    _currentActivity++;
  } else if (PHASES.indexOf(_currentPhase) < 2) {
    _currentPhase = PHASES[PHASES.indexOf(_currentPhase) + 1];
    _currentActivity = 1;
  }
  updatePhaseDisplay();
});

function updatePhaseDisplay() {
  document.querySelectorAll('.phase-pill').forEach(p => {
    p.classList.toggle('active', p.dataset.phase === _currentPhase);
  });
  activityLabel.textContent = `Activity ${_currentActivity} of 4`;
}

// ── Energy ────────────────────────────────────────────────────────────────────

function updateEnergy(level) {
  energyBadge.textContent = level;
  energyBadge.className = `energy-badge ${level.toLowerCase()}`;
}

// ── Tabs ──────────────────────────────────────────────────────────────────────

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    $(`tab-${tab.dataset.tab}`).classList.add('active');
  });
});

// ── Add items manually ────────────────────────────────────────────────────────

let _addTarget = null;

document.querySelectorAll('.btn-add').forEach(btn => {
  btn.addEventListener('click', () => {
    _addTarget = btn.dataset.target;
    $('add-modal-title').textContent = `Add ${_addTarget.slice(0, -1)}`;
    $('add-modal-text').value = '';
    $('add-modal-stakeholders').classList.toggle('hidden', _addTarget !== 'tensions');
    $('add-modal').classList.remove('hidden');
  });
});

$('btn-add-cancel').addEventListener('click', () => $('add-modal').classList.add('hidden'));

$('btn-add-confirm').addEventListener('click', async () => {
  const text = $('add-modal-text').value.trim();
  if (!text) return;

  const endpoints = {
    assumptions:  () => api('POST', `/sessions/${_sessionId}/assumptions`, { text }),
    tensions:     () => api('POST', `/sessions/${_sessionId}/tensions`, {
                    description: text,
                    stakeholders: $('add-modal-stakeholders').value.split(',').map(s => s.trim()).filter(Boolean),
                  }),
    directions:   () => api('POST', `/sessions/${_sessionId}/directions`, { description: text }),
    commitments:  () => api('POST', `/sessions/${_sessionId}/commitments`, { owner: 'Facilitator', action: text }),
  };

  if (endpoints[_addTarget]) {
    const item = await endpoints[_addTarget]();
    const cssClass = _addTarget.slice(0, -1);
    appendStateItem(_addTarget, item, cssClass, cssClass.toUpperCase(), text, null);
  }

  $('add-modal').classList.add('hidden');
});

// ── Synthesis ─────────────────────────────────────────────────────────────────

async function triggerSynthesis() {
  await api('POST', `/sessions/${_sessionId}/synthesize`);
  $('synthesis-modal').classList.remove('hidden');
  $('synthesis-output').textContent = 'Generating synthesis document…';

  // Stream the synthesis document
  const resp = await fetch(`${API_BASE}/sessions/${_sessionId}/synthesis/document`, {
    headers: { Authorization: `Bearer ${_token}` },
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let output = '';
  $('synthesis-output').textContent = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    output += decoder.decode(value, { stream: true });
    $('synthesis-output').textContent = output;
  }
}

$('btn-synthesis-close').addEventListener('click', () => $('synthesis-modal').classList.add('hidden'));
$('btn-synthesis-copy').addEventListener('click', () => {
  navigator.clipboard.writeText($('synthesis-output').textContent);
});

// ── Alert banner ──────────────────────────────────────────────────────────────

let _alertTimer = null;

function showAlert(message) {
  alertBanner.textContent = message;
  alertBanner.classList.remove('hidden');
  clearTimeout(_alertTimer);
  _alertTimer = setTimeout(() => alertBanner.classList.add('hidden'), 6000);
}

// ── API helper ────────────────────────────────────────────────────────────────

async function api(method, path, body) {
  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...(  _token ? { Authorization: `Bearer ${_token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(err.detail || `HTTP ${resp.status}`);
  }

  if (resp.status === 204) return null;
  return resp.json();
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── Boot ──────────────────────────────────────────────────────────────────────

initAuth();
