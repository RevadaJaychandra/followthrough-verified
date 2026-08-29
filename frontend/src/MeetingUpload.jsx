import { useState } from 'react';
import { api } from './api';

const SAMPLE_TRANSCRIPT = `Kartikeya: I'll finish the authentication API by Friday.
Rahul: I'll review it once it's ready.
Kartikeya: Sounds good. After that we deploy to staging.`;

export default function MeetingUpload({ onUploaded }) {
  const [title, setTitle] = useState('');
  const [transcript, setTranscript] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    if (!transcript.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.uploadMeeting(title || 'Untitled meeting', transcript);
      onUploaded(res.meeting_id);
      setTranscript('');
      setTitle('');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="upload-panel" onSubmit={submit}>
      <div className="upload-panel__header">
        <span className="upload-panel__title">New meeting</span>
        <button
          type="button"
          className="upload-panel__sample"
          onClick={() => setTranscript(SAMPLE_TRANSCRIPT)}
        >
          Use sample
        </button>
      </div>
      <input
        className="upload-panel__input"
        placeholder="Meeting title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <textarea
        className="upload-panel__textarea"
        placeholder="Paste transcript here..."
        rows={6}
        value={transcript}
        onChange={(e) => setTranscript(e.target.value)}
      />
      {error && <div className="upload-panel__error">{error}</div>}
      <button type="submit" className="upload-panel__submit" disabled={busy}>
        {busy ? 'Extracting commitments...' : 'Process meeting'}
      </button>
    </form>
  );
}
