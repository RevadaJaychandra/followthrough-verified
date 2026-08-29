import { useState } from 'react';
import { api } from './api';

// The human-in-the-loop gate, made visible.
//
// Murph can draft an escalation email but cannot send one: the backend refuses
// to send anything that is not already APPROVED, and approving is not exposed
// to any agent as a tool. This panel is the only place that approval happens,
// which is why it sits above the pipeline rather than tucked away.
export default function ApprovalPanel({ escalations, onDecision }) {
  const [busyId, setBusyId] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);

  const pending = escalations.filter((e) => e.status === 'PENDING_APPROVAL');
  if (pending.length === 0) return null;

  const decide = async (escalationId, action) => {
    setBusyId(escalationId);
    setError(null);
    try {
      if (action === 'approve') await api.approveEscalation(escalationId, 'dashboard-user');
      else await api.rejectEscalation(escalationId, 'dashboard-user');
      onDecision();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="approval">
      <div className="approval__header">
        <span className="approval__title">Awaiting your approval</span>
        <span className="approval__note mono">
          Murph drafted {pending.length === 1 ? 'this email' : 'these emails'} but cannot send{' '}
          {pending.length === 1 ? 'it' : 'them'}
        </span>
      </div>

      {pending.map((e) => {
        const open = expandedId === e.id;
        return (
          <div key={e.id} className="approval__card">
            <div className="approval__meta">
              <span className="approval__to mono">to {e.recipient}</span>
              <span className="dot">·</span>
              <span className="approval__reason">{e.reason}</span>
            </div>
            <div className="approval__subject">{e.subject}</div>

            <button
              type="button"
              className="approval__toggle mono"
              onClick={() => setExpandedId(open ? null : e.id)}
            >
              {open ? 'hide draft' : 'read draft'}
            </button>
            {open && <pre className="approval__body">{e.body}</pre>}

            {error && <div className="approval__error">{error}</div>}

            <div className="approval__actions">
              <button
                type="button"
                className="approval__reject"
                disabled={busyId === e.id}
                onClick={() => decide(e.id, 'reject')}
              >
                Reject
              </button>
              <button
                type="button"
                className="approval__approve"
                disabled={busyId === e.id}
                onClick={() => decide(e.id, 'approve')}
              >
                {busyId === e.id ? 'Sending...' : 'Approve & send'}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
