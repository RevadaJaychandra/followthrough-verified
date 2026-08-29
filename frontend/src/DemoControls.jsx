import { useState } from 'react';
import { api } from './api';

// Demo controls.
//
// Everything here drives the *real* pipeline through the real API — adding the
// 'blocked' label is the same call a person makes on GitHub, and the monitoring
// agent reacts to it exactly the same way. Nothing is faked for the camera.
//
// They are off by default and hidden once a commitment reaches VERIFIED, so a
// recorded demo can be driven from one screen instead of a second terminal.
export default function DemoControls({ commitment, offlineMode, onAction }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const { id, state, github_issue_number } = commitment;

  if (!github_issue_number) return null;
  if (state === 'VERIFIED' || state === 'COMPLETED') return null;

  const run = async (label, fn) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      onAction();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(null);
    }
  };

  const isBlocked = state === 'BLOCKED' || state === 'ESCALATED';

  return (
    <div className="demo-controls">
      <span className="demo-controls__label mono">simulate</span>

      {isBlocked ? (
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run('unblock', () => api.unlabelCommitment(id, 'blocked'))}
        >
          {busy === 'unblock' ? '...' : 'remove blocked label'}
        </button>
      ) : (
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run('block', () => api.labelCommitment(id, 'blocked'))}
        >
          {busy === 'block' ? '...' : 'label blocked'}
        </button>
      )}

      {offlineMode && (
        <button
          type="button"
          disabled={busy !== null}
          onClick={() => run('merge', () => api.mockMerge(id))}
        >
          {busy === 'merge' ? '...' : 'merge linked PR'}
        </button>
      )}

      {error && <span className="demo-controls__error">{error}</span>}
    </div>
  );
}
