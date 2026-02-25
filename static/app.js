/**
 * NEXUS MVP — browser logic
 *
 * Transcription : Web Speech API (built into Chrome, no account needed)
 * Chatbot       : Ollama running locally, proxied through /chat on this server
 */

// ── State ─────────────────────────────────────────────────────────────────────
let _recognition  = null;   // SpeechRecognition instance
let _recording    = false;
let _lang         = 'el-GR';       // currently selected language
let _transcript   = [];            // list of finalised utterance strings
let _chatHistory  = [];            // [{role, content}, ...] for follow-ups

// ── DOM ───────────────────────────────────────────────────────────────────────
const btnRecord      = document.getElementById('btn-record');
const btnClear       = document.getElementById('btn-clear');
const statusDot      = document.getElementById('status-dot');
const statusLabel    = document.getElementById('status-label');
const transcriptBody = document.getElementById('transcript-body');
const interimBar     = document.getElementById('interim-bar');
const chatBody       = document.getElementById('chat-body');
const chatForm       = document.getElementById('chat-form');
const chatInput      = document.getElementById('chat-input');
const btnSend        = document.getElementById('btn-send');

// ── Language toggle ───────────────────────────────────────────────────────────
document.querySelectorAll('.lang-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.lang-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _lang = btn.dataset.lang;

    // If currently recording, restart with new language
    if (_recording) {
      _recognition.stop();  // onend will restart
    }
  });
});

// ── Record button ─────────────────────────────────────────────────────────────
btnRecord.addEventListener('click', () => {
  if (_recording) {
    stopRecording();
  } else {
    startRecording();
  }
});

function startRecording() {
  // Check browser support
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    alert(
      'Speech recognition is not supported in this browser.\n\n' +
      'Please use Google Chrome on your Mac.'
    );
    return;
  }

  // Remove the placeholder text on first use
  const ph = transcriptBody.querySelector('.placeholder');
  if (ph) ph.remove();

  _recognition = new SpeechRecognition();
  _recognition.lang            = _lang;
  _recognition.continuous      = true;   // keep listening without stopping
  _recognition.interimResults  = true;   // show words as they're spoken

  // ── Callbacks ────────────────────────────────────────────────────────────

  _recognition.onstart = () => {
    setStatus('recording', 'Recording…');
  };

  _recognition.onresult = (event) => {
    let interim = '';

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        const text = result[0].transcript.trim();
        if (text) {
          _transcript.push(text);
          appendUtterance(text);
          interimBar.textContent = '';
        }
      } else {
        interim += result[0].transcript;
      }
    }

    interimBar.textContent = interim;
  };

  // Auto-restart if recognition stops unexpectedly while still in recording mode
  _recognition.onend = () => {
    if (_recording) {
      _recognition.lang = _lang;   // pick up any language change
      _recognition.start();
    }
  };

  _recognition.onerror = (event) => {
    if (event.error === 'not-allowed') {
      alert(
        'Microphone access was denied.\n\n' +
        'Please allow Chrome to use your microphone:\n' +
        '  Chrome menu → Settings → Privacy and security → Site settings → Microphone'
      );
      stopRecording();
    }
    // Other errors (network, no-speech) are non-fatal — onend will restart
  };

  _recording = true;
  _recognition.start();

  btnRecord.textContent = '■ Stop recording';
  btnRecord.classList.add('recording');
}

function stopRecording() {
  _recording = false;
  if (_recognition) {
    _recognition.stop();
    _recognition = null;
  }
  interimBar.textContent = '';
  setStatus('ok', 'Stopped');
  btnRecord.textContent = '▶ Start recording';
  btnRecord.classList.remove('recording');
}

// ── Transcript rendering ──────────────────────────────────────────────────────
function appendUtterance(text) {
  const flagText = _lang === 'el-GR' ? '🇬🇷 Greek' : '🇬🇧 English';

  const wrap = document.createElement('div');
  wrap.className = 'utterance';

  const flag = document.createElement('div');
  flag.className = 'utterance-flag';
  flag.textContent = flagText;

  const p = document.createElement('div');
  p.className = 'utterance-text';
  p.textContent = text;

  wrap.appendChild(flag);
  wrap.appendChild(p);
  transcriptBody.appendChild(wrap);
  transcriptBody.scrollTop = transcriptBody.scrollHeight;
}

// ── Chat ──────────────────────────────────────────────────────────────────────
chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const message = chatInput.value.trim();
  if (!message) return;

  chatInput.value = '';
  appendBubble(message, 'user');

  const thinkingBubble = appendBubble('Thinking…', 'nexus thinking');
  btnSend.disabled = true;

  try {
    const response = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message,
        transcript: _transcript.join('\n'),
        history: _chatHistory,
      }),
    });

    if (!response.ok) throw new Error(`Server error ${response.status}`);
    const data = await response.json();
    const reply = data.response || 'No response received.';

    thinkingBubble.textContent = reply;
    thinkingBubble.classList.remove('thinking');

    // Store turn in history so follow-up questions work
    _chatHistory.push({ role: 'user',      content: message });
    _chatHistory.push({ role: 'assistant', content: reply   });

    // Keep history bounded (last 20 turns)
    if (_chatHistory.length > 40) {
      _chatHistory = _chatHistory.slice(-40);
    }

  } catch (err) {
    thinkingBubble.textContent = `Error: ${err.message}`;
    thinkingBubble.classList.remove('thinking');
  } finally {
    btnSend.disabled = false;
    chatInput.focus();
    chatBody.scrollTop = chatBody.scrollHeight;
  }
});

function appendBubble(text, type) {
  const div = document.createElement('div');
  div.className = `bubble bubble-${type}`;
  div.textContent = text;
  chatBody.appendChild(div);
  chatBody.scrollTop = chatBody.scrollHeight;
  return div;
}

// ── Clear ─────────────────────────────────────────────────────────────────────
btnClear.addEventListener('click', () => {
  if (_recording) stopRecording();

  _transcript   = [];
  _chatHistory  = [];

  transcriptBody.innerHTML = '<p class="placeholder">Press <strong>Start recording</strong> to begin.</p>';
  chatBody.innerHTML       = '<div class="bubble bubble-nexus">Session cleared. Start a new recording when ready.</div>';
  interimBar.textContent   = '';
});

// ── Helpers ───────────────────────────────────────────────────────────────────
function setStatus(state, label) {
  statusDot.className  = `status-dot ${state}`;
  statusLabel.textContent = label;
}
