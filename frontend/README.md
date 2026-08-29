# FollowThrough — dashboard

The control-room UI for FollowThrough: a live view of every commitment
extracted from a meeting, the agent timeline, the escalation approval gate, and
Murph's voice/chat bar.

React 19 + Vite 8, no UI framework, no state library. The backend is the only
source of truth; this app polls it every 4 seconds and renders what it finds.

## Run it

```bash
npm install
cp .env.example .env    # VITE_API_BASE=http://localhost:8080
npm run dev             # http://localhost:5173
```

```powershell
# Windows
npm install
Copy-Item .env.example .env
npm run dev
```

The backend must be running (see [../README.md](../README.md)). With
`OFFLINE_MODE=true` on the backend, the header shows an `OFFLINE` chip so the
UI never implies a real GitHub issue was filed when it was not.

`npm run lint` (oxlint) and `npm run build` should both be clean before you
commit.

## What each piece does

| File | Responsibility |
|---|---|
| `App.jsx` | shell, 4s poll loop, state summary, demo-controls toggle |
| `api.js` | every backend call, in one place |
| `MeetingUpload.jsx` | transcript paste + "Use sample" |
| `CommitmentPipeline.jsx` | per-commitment stage rail; BLOCKED/ESCALATED render as an exception branch rather than a rail position |
| `ApprovalPanel.jsx` | the human-in-the-loop gate — the only place an escalation email can be authorised |
| `DemoControls.jsx` | per-commitment demo actions (label blocked, clear it, merge the PR); off by default |
| `EventTicker.jsx` | live agent timeline |
| `MurphBar.jsx` | text + Web Speech voice input |

### About the approval panel

Murph can *draft* an escalation email but cannot send one. The backend refuses
to send any escalation that is not already `APPROVED`, and no agent has a tool
that can approve one — `POST /escalations/{id}/approve` exists only as a human
action, and this panel is the only thing in the product that calls it. The gate
lives in backend state, not in a prompt, so it holds even if the model is
talked into trying.

### Voice input

`MurphBar` uses the Web Speech API (`webkitSpeechRecognition`). That is
Chrome/Edge only; other browsers fall back to typing and Murph says so rather
than failing silently.

## Deploying

The production build is a static bundle — any static host works.

**Firebase Hosting** (config is in `firebase.json` at the repo root):
```bash
npm run build
npx firebase-tools deploy --only hosting --project YOUR_PROJECT_ID
```

**Cloud Run / any container host** (`Dockerfile` in this directory):
```bash
gcloud run deploy followthrough-frontend --source . --region us-central1 --allow-unauthenticated
```

**Vercel / Netlify**: point at `frontend/`, build `npm run build`, publish
`dist`.

Whichever you pick, two things must line up or the dashboard loads but shows
nothing:

1. `VITE_API_BASE` must be the deployed backend URL. It is baked in at build
   time, so set it *before* `npm run build`.
2. The backend's `CORS_ALLOWED_ORIGINS` must include this app's URL. Re-run
   `backend/deploy.sh` with `CORS_ALLOWED_ORIGINS=https://your-frontend-url`.
