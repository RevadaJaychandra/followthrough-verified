import { useEffect, useState, useCallback } from 'react';
import { api } from './api';
import CommitmentPipeline from './CommitmentPipeline';
import EventTicker from './EventTicker';
import MurphBar from './MurphBar';
import MeetingUpload from './MeetingUpload';

const POLL_INTERVAL_MS = 4000;

export default function App() {
  const [commitments, setCommitments] = useState([]);
  const [events, setEvents] = useState([]);
  const [murphLog, setMurphLog] = useState([]);
  const [showUpload, setShowUpload] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const [c, e] = await Promise.all([api.getCommitments(), api.getEvents()]);
      setCommitments(c);
      setEvents(e);
    } catch (err) {
      console.error('poll failed', err);
    }
  }, []);

  useEffect(() => {
    refresh();
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

      <main className="app-main">
        <div className="app-main__left">
          {showUpload && <MeetingUpload onUploaded={handleUploaded} />}
          <CommitmentPipeline commitments={commitments} />
        </div>
        <div className="app-main__right">
          <EventTicker events={events} />
        </div>
      </main>
    </div>
  );
}
