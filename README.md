# FollowThrough

**An autonomous post-meeting execution agent.** Extracts commitments from
meeting transcripts, creates and tracks GitHub issues, monitors progress,
detects blockers, escalates with human approval, and verifies completion —
built for the All Things Agentic Hackathon (Taskmaster track).

## Quickstart — verify everything works with ZERO credentials (do this first)

Every external call (Gemini, Firestore, Pub/Sub, GitHub, Gmail) has an
offline-mode fallback, so you can prove the whole architecture works
before anyone touches an API key.

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
bash smoke_test.sh
```

This boots the real FastAPI server, uploads a real transcript, watches
the real dependency-aware execution logic run, waits for the real
monitoring loop to detect a blocker, and drives the full Murph
escalation conversation — all in-memory, no GCP/GitHub/Gmail needed.
If `smoke_test.sh` prints `ALL PASSED`, the architecture is verified
sound and every remaining step is just plugging in real credentials.

To see it in the dashboard instead of curl:
```bash
# terminal 1
cd backend && OFFLINE_MODE=true GCP_PROJECT_ID=dev uvicorn main:app --reload --port 8080
# terminal 2
cd frontend && npm install && VITE_API_BASE=http://localhost:8080 npm run dev
```
Open `http://localhost:5173`, click **"Use sample"** → **Process meeting**,
watch the pipeline rail update live.

---

## What YOU need to add (credentials — never paste these in chat, only in your local `.env`)

Copy `backend/.env.example` to `backend/.env` and fill in:

| Variable | Where to get it | Needed for |
|---|---|---|
| `GCP_PROJECT_ID` | Run the GCP setup below, this is the project you create | Vertex AI, Firestore, Pub/Sub |
| `GITHUB_TOKEN` | https://github.com/settings/tokens — classic token, `repo` scope | Creating/monitoring real GitHub issues |
| `GITHUB_REPO` | A throwaway repo you create, e.g. `yourname/followthrough-demo` | Same as above |
| `GMAIL_SENDER` / `GMAIL_APP_PASSWORD` | https://myaccount.google.com/apppasswords | Escalation emails |
| `GEMINI_API_KEY` (optional, alternative to Vertex) | https://aistudio.google.com/apikey | Faster path to test extraction alone before full GCP IAM is set up |

Run `python3 config.py` after filling in `.env` — it tells you exactly
which values are still missing or still placeholders, with no network
call and no risk of leaking anything.

Once `.env` is filled in, run the app **without** `OFFLINE_MODE=true` —
everything switches to real Gemini/Firestore/Pub/Sub/GitHub/Gmail calls
with no code changes needed.

---

## Architecture

```
Transcript
    │
    ▼
Meeting Intelligence Agent (Gemini)  ──► Firestore (commitments)
    │
    ▼ Pub/Sub: meeting-processed
    │
Execution Agent (ADK LlmAgent + GitHub tool) ──► GitHub Issues
    │
    ▼
Monitoring Agent (polling loop) ──► checks GitHub state, detects blockers,
    │                                verifies merged PRs
    ▼
Murph (ADK LlmAgent, voice/chat) ──► queries Firestore, drafts + sends
                                       approved escalation emails
```

**Google Cloud stack used:** Vertex AI (Gemini), ADK, Firestore, Pub/Sub,
Cloud Run.

## Repo layout

```
backend/    FastAPI app, ADK agents, Firestore/Pub/Sub/GitHub integration
frontend/   React + Vite dashboard (control-room UI) + Murph voice bar
```

## Full local setup (real credentials, not offline mode)

### Prerequisites
- Python 3.11+
- Node 18+
- `gcloud` CLI, authenticated (`gcloud auth login && gcloud auth application-default login`)
- A GCP project with Vertex AI, Firestore, and Pub/Sub APIs enabled (see below)
- A GitHub personal access token with `repo` scope, and a demo repo to point at
- A Gmail account + [app password](https://myaccount.google.com/apppasswords) for escalation email

### 1. GCP project setup (one team member does this, share the project ID)

```bash
gcloud projects create followthrough-hack --name="FollowThrough"
gcloud config set project followthrough-hack
gcloud billing projects link followthrough-hack --billing-account=YOUR_BILLING_ID
gcloud services enable aiplatform.googleapis.com firestore.googleapis.com \
  pubsub.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  cloudscheduler.googleapis.com secretmanager.googleapis.com
gcloud firestore databases create --location=us-central1
```

Everyone on the team should then run:
```bash
gcloud auth login
gcloud config set project followthrough-hack
gcloud auth application-default login
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in GITHUB_TOKEN, GITHUB_REPO, GMAIL_*
python3 config.py           # confirms nothing is missing/placeholder
uvicorn main:app --reload --port 8080
```

Visit `http://localhost:8080/docs` for the interactive API docs.

### 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env   # VITE_API_BASE=http://localhost:8080
npm run dev
```

Visit `http://localhost:5173`.

## Demo flow

1. Open the dashboard, click **"Use sample"** in the upload panel, then **Process meeting**.
2. Watch the pipeline: commitments appear, GitHub issues get created automatically (event ticker on the right shows every step).
3. To simulate a blocker live: `POST /commitments/{id}/label {"label": "blocked"}` (or add the `blocked` label to the GitHub issue directly) — the monitoring loop picks it up within ~30s and the rail flips to BLOCKED.
4. Ask Murph: *"What's blocked?"* — it reads live Firestore state and answers.
5. Say *"Ask [owner] about it"* — Murph drafts an escalation and asks for confirmation before sending.
6. Merge the linked PR on GitHub — monitoring loop detects it, marks VERIFIED.

## Recommended order for switching from offline to real credentials

Don't flip everything at once — bring up one integration at a time so
any failure is easy to isolate:

1. **GCP + Firestore first.** Set `GCP_PROJECT_ID`, run with
   `OFFLINE_MODE=false` but no `GITHUB_TOKEN` yet — the GitHub calls
   will fail (expected), but you'll confirm Firestore reads/writes work
   and commitments persist across a server restart.
2. **GitHub next.** Add `GITHUB_TOKEN` + `GITHUB_REPO`. Re-run
   `smoke_test.sh` logic manually (or just re-upload the sample meeting)
   and confirm a real issue appears in your GitHub repo.
3. **Gemini extraction.** Either set `GEMINI_API_KEY` (fastest to test
   in isolation) or confirm Vertex AI IAM is correct, then try a
   transcript that isn't the bundled sample — the offline extractor is
   rule-based and only handles simple "Name: sentence" transcripts, so
   this is the point where real transcripts start working.
4. **Gmail last.** Add `GMAIL_SENDER`/`GMAIL_APP_PASSWORD`, trigger the
   escalation flow through Murph, confirm the email actually arrives.
5. **Pub/Sub.** This one rarely needs debugging since `ensure_topics_and_subscriptions()`
   creates topics/subscriptions idempotently on startup — but if the
   Execution Agent doesn't seem to run after upload, check the server
   log for `[pubsub] listening on meeting-processed` at startup.

At every step, `python3 config.py` tells you what's still missing.

## Deployment (Cloud Run)

```bash
cd backend
bash deploy.sh
```

Then set `VITE_API_BASE` in the frontend to the deployed Cloud Run URL and
deploy the frontend (Firebase Hosting, Vercel, or `gcloud run deploy` with a
static-serving Dockerfile — whichever is fastest for the team).

## What's real vs. simplified for the hackathon timeline

- **Real**: Gemini extraction, ADK agents (Execution + Murph), Firestore state, Pub/Sub decoupling between meeting-processed → execution, GitHub issue/PR lifecycle, human-approval-gated escalation email.
- **Simplified**: Monitoring runs as a polling loop (not Cloud Scheduler → Eventarc → Cloud Run) — same visible behavior, less deploy complexity for the timeline. Dependency detection is single-level (task A blocks task B) rather than a general graph solver. Calendar integration is out of scope.
- **Offline mode** (`OFFLINE_MODE=true`): every external call (Gemini, Firestore, Pub/Sub, GitHub, Gmail) has an in-memory fallback behind the exact same function signatures, so the full pipeline is testable and demoable with zero credentials. This is a dev/testing aid, not part of the submission's live architecture — the real submission runs with `OFFLINE_MODE=false` and every call hitting the real Google Cloud + GitHub + Gmail services, which is what the architecture diagram and demo video should show.
