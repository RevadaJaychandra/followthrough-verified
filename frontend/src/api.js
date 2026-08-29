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
  getHealth: () => request('/health'),

  // OFFLINE_MODE only. In real mode you merge the PR on GitHub and the
  // monitoring agent notices on its next pass.
  mockMerge: (commitmentId) =>
    request(`/commitments/${commitmentId}/mock-merge`, { method: 'POST' }),

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

  unlabelCommitment: (commitmentId, label) =>
    request(`/commitments/${commitmentId}/label/${label}`, { method: 'DELETE' }),

  getEscalations: (status) =>
    request(`/escalations${status ? `?status=${status}` : ''}`),

  // Approving is deliberately the only way an escalation email can be sent.
  // No agent has a tool for this; it exists solely as a human action.
  approveEscalation: (escalationId, approvedBy) =>
    request(`/escalations/${escalationId}/approve`, {
      method: 'POST',
      body: JSON.stringify({ approved_by: approvedBy }),
    }),

  rejectEscalation: (escalationId, approvedBy) =>
    request(`/escalations/${escalationId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ approved_by: approvedBy }),
    }),
};
