import { useEffect, useState, useCallback } from 'react';
import { api } from './api';
import CommitmentPipeline from './CommitmentPipeline';
import EventTicker from './EventTicker';
import MurphBar from './MurphBar';
import MeetingUpload from './MeetingUpload';
import ApprovalPanel from './ApprovalPanel';

const POLL_INTERVAL_MS = 4000;

export default function App() {
  const [commitments, setCommitments] = useState([]);
  const [events, setEvents] = useState([]);
  const [escalations, setEscalations] = useState([]);
  const [murphLog, setMurphLog] = useState([]);
  const [showUpload, setShowUpload] = useState(true);
  const [showDemoControls, setShowDemoControls] = useState(false);
  const [health, setHealth] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [c, e, esc] = await Promise.all([
        api.getCommitments(),
        api.getEvents(),
        api.getEscalations(),
      ]);
      setCommitments(c);
      setEvents(e);
      setEscalations(esc);
    } catch (err) {
      console.error('poll failed', err);
    }
  }, []);

  useEffect(() => {
    // The backend is the external system here: agents mutate commitment
    // state on their own schedule, so the dashboard polls rather than
    // deriving this during render. The immediate refresh() avoids a blank
    // first paint while we wait out the first interval.
    // oxlint-disable-next-line react/set-state-in-effect
    refresh();
    api.getHealth().then(setHealth).catch(() => setHealth(null));
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const handleUploaded = () => {
    setShowUpload(false);
    refresh();
  };

  const summary = commitments.reduce((acc, c) => {
    acc[c.state] = (acc[c.state] || 0) + 1;
    return acc;
  }, {});

  return (
    <div className="app">
      <header className="app-header">
        <div className="app-header__brand">
          <span className="app-header__mark">FT</span>
          <div>
            <div className="app-header__title">FollowThrough</div>
            <div className="app-header__subtitle mono">post-meeting execution, autonomous</div>
          </div>
        </div>
        <div className="app-header__summary mono">
          {Object.entries(summary).map(([state, count]) => (
            <span key={state} className="summary-chip">
              {count} {state}
            </span>
          ))}
          {health?.offline_mode && (
            <span
              className="summary-chip summary-chip--offline"
              title="Every external call is an in-memory stub. No real GitHub issue, Gemini call, or email."
            >
              OFFLINE
            </span>
          )}
          <button
            className={`app-header__demo ${showDemoControls ? 'is-on' : ''}`}
            onClick={() => setShowDemoControls((s) => !s)}
            title="Show per-commitment controls that drive the real pipeline"
          >
            Demo controls
          </button>
          <button className="app-header__new" onClick={() => setShowUpload((s) => !s)}>
            {showUpload ? 'Hide' : '+ New meeting'}
          </button>
        </div>
      </header>

      <MurphBar onReply={(msg) => setMurphLog((log) => [...log, msg])} />

      {murphLog.length > 0 && (
        <div className="murph-log">
          {murphLog.slice(-4).map((m, i) => (
            <div key={i} className={`murph-log__entry murph-log__entry--${m.role}`}>
              <span className="mono murph-log__role">{m.role === 'murph' ? 'MURPH' : 'YOU'}</span>
              {m.text}
            </div>
          ))}
        </div>
      )}

      <ApprovalPanel escalations={escalations} onDecision={refresh} />

      <main className="app-main">
        <div className="app-main__left">
          {showUpload && <MeetingUpload onUploaded={handleUploaded} />}
          <CommitmentPipeline
            commitments={commitments}
            showDemoControls={showDemoControls}
            offlineMode={health?.offline_mode ?? false}
            onAction={refresh}
          />
        </div>
        <div className="app-main__right">
          <EventTicker events={events} />
        </div>
      </main>
    </div>
  );
}
