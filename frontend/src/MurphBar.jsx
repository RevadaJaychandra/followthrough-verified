import { useState, useRef } from 'react';
import { api } from './api';

// Inline SVG rather than the microphone emoji: emoji glyphs are missing in
// some environments (headless Chromium, a few Linux desktops) and render as
// an empty box, which looked broken in the demo screenshots.
function MicIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="2" width="6" height="11" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <line x1="12" y1="17" x2="12" y2="21" />
    </svg>
  );
}

export default function MurphBar({ onReply }) {
  const [text, setText] = useState('');
  const [listening, setListening] = useState(false);
  const [busy, setBusy] = useState(false);
  const sessionRef = useRef(null);

  const send = async (query) => {
    if (!query.trim()) return;
    setBusy(true);
    onReply({ role: 'user', text: query });
    try {
      const res = await api.askMurph(query, sessionRef.current);
      sessionRef.current = res.session_id;
      onReply({ role: 'murph', text: res.reply });
    } catch (e) {
      onReply({ role: 'murph', text: `Error: ${e.message}` });
    } finally {
      setBusy(false);
      setText('');
    }
  };

  const startVoice = () => {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      onReply({ role: 'murph', text: 'Voice input is not supported in this browser — try Chrome.' });
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = 'en-US';
    recognition.interimResults = false;
    recognition.onstart = () => setListening(true);
    recognition.onend = () => setListening(false);
    recognition.onresult = (event) => {
      const transcript = event.results[0][0].transcript;
      send(transcript);
    };
    recognition.start();
  };

  return (
    <form
      className="murph-bar"
      onSubmit={(e) => {
        e.preventDefault();
        send(text);
      }}
    >
      <span className="murph-bar__label mono">MURPH</span>
      <input
        className="murph-bar__input"
        placeholder="Ask what's blocked, or say 'ask Rahul for staging access'..."
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={busy}
      />
      <button
        type="button"
        className={`murph-bar__mic ${listening ? 'is-listening' : ''}`}
        onClick={startVoice}
        aria-label="Voice input"
        title="Voice input"
      >
        {listening ? <span className="murph-bar__pulse" /> : <MicIcon />}
      </button>
      <button type="submit" className="murph-bar__send" disabled={busy || !text.trim()}>
        {busy ? '...' : 'Ask'}
      </button>
    </form>
  );
}
