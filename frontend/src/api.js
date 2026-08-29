const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8080';

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json();
}

export const api = {
  uploadMeeting: (title, transcript) =>
    request('/meetings', {
      method: 'POST',
      body: JSON.stringify({ title, transcript }),
    }),

  getCommitments: (meetingId) =>
    request(`/commitments${meetingId ? `?meeting_id=${meetingId}` : ''}`),

  getEvents: (meetingId) =>
    request(`/events${meetingId ? `?meeting_id=${meetingId}` : ''}`),

  askMurph: (query, sessionId) =>
    request('/murph', {
      method: 'POST',
      body: JSON.stringify({ query, session_id: sessionId }),
    }),

  labelCommitment: (commitmentId, label) =>
    request(`/commitments/${commitmentId}/label`, {
      method: 'POST',
      body: JSON.stringify({ label }),
    }),
};
