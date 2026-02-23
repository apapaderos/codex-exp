/**
 * NEXUS MVP — tablet frontend
 *
 * Flow:
 *  1. User taps "Start recording"
 *  2. Browser requests microphone → AudioWorklet → PCM 16kHz → WebSocket
 *  3. Server streams transcript events back → rendered in left pane
 *  4. User types a question → POST /chat → response rendered in right pane
 */

// ── Session ───────────────────────────────────────────────────────────────────
// A random ID generated once per page load. Ties together the WebSocket
// audio stream and the chat API calls so the server knows which transcript
// to search against.
const SESSION_ID = crypto.randomUUID();

// ── State ─────────────────────────────────────────────────────────────────────
let _ws          = null;
let _audioCtx    = null;
let _mediaStream = null;
let _processor   = null;
let _recording   = false;
let _selectedLang = 'auto';   // mirrors the active lang-btn

// ── DOM ───────────────────────────────────────────────────────────────────────
const btnRecord     = document.getElementById('btn-record');
const btnRecordLbl  = document.getElementById('btn-record-label');
const btnClear      = document.getElementById('btn-clear');
const statusDot     = document.getElementById('status-dot');
const transcriptEl  = document.getElementById('transcript-body');
const interimEl     = document.getElementById('interim-bar');
const langBadge     = document.getElementById('lang-badge');
const chatBody      = document.getElementById('chat-body');
const chatForm      = document.getElementById('chat-form');
const chatInput     = document.getElementById('chat-input');
const btnSend       = chatForm.querySelector('.btn-send');

// ── Language toggle ───────────────────────────────────────────────────────────
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    // If already recording, ignore — would require reconnect
    if (_recording) return;
    document.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _selectedLang = btn.dataset.lang;
  });
});

// ── Record toggle ─────────────────────────────────────────────────────────────
btnRecord.addEventListener('click', async () => {
  if (_recording) {
    await stopRecording();
  } else {
    await startRecording();
  }
});

async function startRecording() {
  setStatus('connecting');

  try {
    _mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount:    1,
        sampleRate:      16000,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl:  true,
      },
    });
  } catch (err) {
    setStatus('error');
    alert('Microphone access is required. Please allow it and try again.');
    return;
  }

  // Open WebSocket first so audio has somewhere to go
  const wsUrl = buildWsUrl();
  _ws = new WebSocket(wsUrl);
  _ws.binaryType = 'arraybuffer';

  _ws.onopen    = () => { setStatus('recording'); };
  _ws.onclose   = () => { if (_recording) setStatus('idle'); };
  _ws.onerror   = () => setStatus('error');
  _ws.onmessage = (msg) => handleServerEvent(JSON.parse(msg.data));

  // Wait for WebSocket to open before connecting audio graph
  await new Promise((resolve, reject) => {
    _ws.addEventListener('open',  resolve, { once: true });
    _ws.addEventListener('error', reject,  { once: true });
  }).catch(() => {});

  if (_ws.readyState !== WebSocket.OPEN) {
    setStatus('error');
    return;
  }

  await connectAudioGraph();
  _recording = true;

  btnRecord.classList.add('recording');
  btnRecordLbl.textContent = 'Stop recording';
  langBadge.textContent = langLabel(_selectedLang);
  langBadge.classList.remove('hidden');

  // Remove placeholder if present
  const ph = transcriptEl.querySelector('.placeholder');
  if (ph) ph.remove();

  // Keep-alive ping every 25 s
  _pingInterval = setInterval(() => {
    if (_ws?.readyState === WebSocket.OPEN) {
      _ws.send(JSON.stringify({ type: 'ping' }));
    }
  }, 25_000);
}

let _pingInterval = null;

async function stopRecording() {
  _recording = false;
  clearInterval(_pingInterval);

  disconnectAudioGraph();
  _ws?.close();
  _ws = null;

  setStatus('idle');
  btnRecord.classList.remove('recording');
  btnRecordLbl.textContent = 'Start recording';
  langBadge.classList.add('hidden');
  interimEl.textContent = '';
}

// ── AudioWorklet PCM pipeline ─────────────────────────────────────────────────
async function connectAudioGraph() {
  _audioCtx = new AudioContext({ sampleRate: 16000 });
  const source = _audioCtx.createMediaStreamSource(_mediaStream);

  if (_audioCtx.audioWorklet) {
    await _audioCtx.audioWorklet.addModule('/static/pcm-processor.js');
    _processor = new AudioWorkletNode(_audioCtx, 'pcm-processor', {
      numberOfInputs:  1,
      numberOfOutputs: 0,
      channelCount:    1,
    });
    _processor.port.onmessage = (e) => {
      if (_ws?.readyState === WebSocket.OPEN) _ws.send(e.data);
    };
    source.connect(_processor);
    _processor.connect(_audioCtx.destination);
  } else {
    // Legacy fallback
    _processor = _audioCtx.createScriptProcessor(4096, 1, 1);
    _processor.onaudioprocess = (e) => {
      if (_ws?.readyState !== WebSocket.OPEN) return;
      const f32 = e.inputBuffer.getChannelData(0);
      const i16 = new Int16Array(f32.length);
      for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        i16[i] = s < 0 ? s * 32768 : s * 32767;
      }
      _ws.send(i16.buffer);
    };
    source.connect(_processor);
    _processor.connect(_audioCtx.destination);
  }
}

function disconnectAudioGraph() {
  if (_processor) {
    if (_processor.port) _processor.port.onmessage = null;
    _processor.disconnect();
    _processor = null;
  }
  if (_audioCtx)    { _audioCtx.close(); _audioCtx = null; }
  if (_mediaStream) { _mediaStream.getTracks().forEach(t => t.stop()); _mediaStream = null; }
}

// ── Server events ─────────────────────────────────────────────────────────────
function handleServerEvent(event) {
  switch (event.type) {
    case 'interim':
      interimEl.textContent = event.text;
      break;

    case 'final':
      interimEl.textContent = '';
      appendUtterance(event.text, event.lang);
      break;

    case 'error':
      appendSystemMessage(`Error: ${event.message}`);
      break;
  }
}

// ── Transcript rendering ──────────────────────────────────────────────────────
function appendUtterance(text, lang) {
  const wrap = document.createElement('div');
  wrap.className = 'utterance';

  if (lang) {
    const lbl = document.createElement('div');
    lbl.className = 'utterance-lang ' + (lang.startsWith('el') ? 'el' : 'en');
    lbl.textContent = lang.startsWith('el') ? 'Greek' : 'English';
    wrap.appendChild(lbl);
  }

  const p = document.createElement('div');
  p.className = 'utterance-text';
  p.textContent = text;
  wrap.appendChild(p);

  transcriptEl.appendChild(wrap);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function appendSystemMessage(msg) {
  const p = document.createElement('p');
  p.style.cssText = 'color:var(--text-muted);font-size:12px;font-style:italic;';
  p.textContent = msg;
  transcriptEl.appendChild(p);
}

// ── Chat ──────────────────────────────────────────────────────────────────────
chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  chatInput.value = '';
  appendChatBubble(message, 'user');

  const thinking = appendChatBubble('Thinking…', 'nexus thinking');
  btnSend.disabled = true;

  try {
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: SESSION_ID, message }),
    });

    if (!resp.ok) throw new Error(`Server error ${resp.status}`);
    const data = await resp.json();

    thinking.textContent = data.response;
    thinking.classList.remove('thinking');
  } catch (err) {
    thinking.textContent = `Error: ${err.message}`;
    thinking.classList.remove('thinking');
  } finally {
    btnSend.disabled = false;
    chatInput.focus();
    chatBody.scrollTop = chatBody.scrollHeight;
  }
});

function appendChatBubble(text, classes) {
  const div = document.createElement('div');
  div.className = `chat-bubble ${classes}`;
  div.textContent = text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
  return div;
}

// ── Clear session ─────────────────────────────────────────────────────────────
btnClear.addEventListener('click', async () => {
  if (_recording) await stopRecording();

  await fetch(`/session/${SESSION_ID}`, { method: 'DELETE' }).catch(() => {});

  transcriptEl.innerHTML = '<p class="placeholder">Tap <strong>Start recording</strong> to begin.<br>The transcript will appear here.</p>';
  chatBody.innerHTML = '<div class="chat-bubble nexus">Ask me anything about the transcript — summarise it, find a specific point, extract action items, translate a passage.</div>';
  interimEl.textContent = '';
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function buildWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const params = new URLSearchParams({ session_id: SESSION_ID, lang: _selectedLang });
  return `${proto}://${location.host}/stream?${params}`;
}

function setStatus(state) {
  statusDot.className = `status-dot ${state}`;
  statusDot.title = { idle: 'Not recording', connecting: 'Connecting…', recording: 'Recording', error: 'Error' }[state] || state;
}

function langLabel(lang) {
  return { auto: 'AUTO  EL / EN', 'el-GR': 'GREEK', 'en-US': 'ENGLISH' }[lang] || lang;
}
