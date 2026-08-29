import DemoControls from './DemoControls';

const STAGE_COLOR = {
  CREATED: 'var(--state-created)',
  PLANNED: 'var(--state-planned)',
  IN_PROGRESS: 'var(--state-progress)',
  WAITING: 'var(--state-waiting)',
  BLOCKED: 'var(--state-blocked)',
  ESCALATED: 'var(--state-escalated)',
  COMPLETED: 'var(--state-completed)',
  VERIFIED: 'var(--state-verified)',
};

// Only show the "main line" stages on the rail; BLOCKED/ESCALATED render
// as a branch/badge since they're exception states, not always visited.
const MAIN_LINE = ['CREATED', 'PLANNED', 'IN_PROGRESS', 'WAITING', 'COMPLETED', 'VERIFIED'];

function CommitmentRail({ commitment, showDemoControls, offlineMode, onAction }) {
  const { description, owner, deadline, state, blocked_reason, github_issue_url } = commitment;
  const isException = state === 'BLOCKED' || state === 'ESCALATED';
  const currentIndex = MAIN_LINE.indexOf(state);

  return (
    <div className="rail-card">
      <div className="rail-card__header">
        <div>
          <div className="rail-card__desc">{description}</div>
          <div className="rail-card__meta">
            <span className="mono">{owner}</span>
            <span className="dot">·</span>
            <span className="mono">{deadline}</span>
            {github_issue_url && (
              <>
                <span className="dot">·</span>
                <a href={github_issue_url} target="_blank" rel="noreferrer" className="mono link">
                  issue ↗
                </a>
              </>
            )}
          </div>
        </div>
        <span
          className="state-badge"
          style={{ color: STAGE_COLOR[state], borderColor: STAGE_COLOR[state] }}
        >
          {state}
        </span>
      </div>

      {isException ? (
        <div className="rail-exception" style={{ borderColor: STAGE_COLOR[state] }}>
          <span className="rail-exception__dot" style={{ background: STAGE_COLOR[state] }} />
          {blocked_reason || 'Escalated — awaiting response'}
        </div>
      ) : (
        <div className="rail-track">
          {MAIN_LINE.map((stage, i) => (
            <div key={stage} className="rail-step">
              <div
                className={`rail-step__dot ${i <= currentIndex ? 'is-filled' : ''}`}
                style={i <= currentIndex ? { background: STAGE_COLOR[stage], borderColor: STAGE_COLOR[stage] } : {}}
              />
              {i < MAIN_LINE.length - 1 && (
                <div
                  className={`rail-step__line ${i < currentIndex ? 'is-filled' : ''}`}
                  style={i < currentIndex ? { background: STAGE_COLOR[stage] } : {}}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {showDemoControls && (
        <DemoControls
          commitment={commitment}
          offlineMode={offlineMode}
          onAction={onAction}
        />
      )}
    </div>
  );
}

export default function CommitmentPipeline({ commitments, showDemoControls, offlineMode, onAction }) {
  if (!commitments.length) {
    return (
      <div className="empty-state">
        No commitments yet. Upload a meeting transcript to get started.
      </div>
    );
  }

  return (
    <div className="pipeline">
      {commitments.map((c) => (
        <CommitmentRail
          key={c.id}
          commitment={c}
          showDemoControls={showDemoControls}
          offlineMode={offlineMode}
          onAction={onAction}
        />
      ))}
    </div>
  );
}
