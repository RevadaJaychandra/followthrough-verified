const KIND_ICON = {
  meeting_uploaded: '◆',
  commitment_created: '＋',
  github_issue_created: '⎘',
  state_change: '→',
  blocker_detected: '⚠',
  blocker_cleared: '⟲',
  escalation_drafted: '✉',
  escalation_approved: '✓',
  escalation_rejected: '⊘',
  escalation_sent: '✉',
  escalation_failed: '✗',
  deadline_missed: '⏱',
  dependency_resolved: '⇢',
  pr_merged: '⎇',
  verified: '✓',
  execution_failed: '✗',
};

function formatTime(ts) {
  if (!ts) return '';
  const d = ts._seconds ? new Date(ts._seconds * 1000) : new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function EventTicker({ events }) {
  return (
    <div className="ticker">
      <div className="ticker__header">Live timeline</div>
      <div className="ticker__list">
        {events.length === 0 && (
          <div className="empty-state empty-state--small">No events yet</div>
        )}
        {events.map((e) => (
          <div key={e.id} className="ticker__row">
            <span className="ticker__icon">{KIND_ICON[e.kind] || '•'}</span>
            <span className="ticker__time mono">{formatTime(e.timestamp)}</span>
            <span className="ticker__msg">{e.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
