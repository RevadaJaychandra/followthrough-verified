#!/bin/bash
# Deploy the FollowThrough backend to Cloud Run.
# Run from the backend/ directory: bash deploy.sh
set -euo pipefail

PROJECT_ID=${GCP_PROJECT_ID:-followthrough-hack}
REGION=${GCP_REGION:-us-central1}
SERVICE_NAME=${SERVICE_NAME:-followthrough-backend}
GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.5-flash}

# These must be set in your environment (or backend/.env, exported) before
# deploying. Cloud Run has no .env file, so anything not passed here simply
# does not exist on the deployed service -- previously GITHUB_REPO,
# GMAIL_SENDER and ESCALATION_RECIPIENT were dropped, and every GitHub call
# on the deployed service failed with an empty repo name.
: "${GITHUB_REPO:?set GITHUB_REPO (e.g. yourname/followthrough-demo)}"
: "${GMAIL_SENDER:?set GMAIL_SENDER (the Gmail account escalations send from)}"
: "${ESCALATION_RECIPIENT:?set ESCALATION_RECIPIENT (default escalation target)}"

# Where the deployed dashboard is served from. CORS rejects anything else.
CORS_ALLOWED_ORIGINS=${CORS_ALLOWED_ORIGINS:-http://localhost:5173}

echo "==> Enabling required APIs (idempotent)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  firestore.googleapis.com \
  pubsub.googleapis.com \
  secretmanager.googleapis.com \
  --project "$PROJECT_ID"

# Secrets live in Secret Manager, never in env vars or the image. Create them
# on first run; --set-secrets below fails with a confusing IAM error if they
# do not already exist.
ensure_secret() {
  local name=$1 env_var=$2
  if gcloud secrets describe "$name" --project "$PROJECT_ID" >/dev/null 2>&1; then
    echo "==> Secret $name already exists"
  else
    if [ -z "${!env_var:-}" ]; then
      echo "ERROR: secret $name does not exist and \$$env_var is not set." >&2
      echo "       Export $env_var and re-run, or create the secret manually." >&2
      exit 1
    fi
    echo "==> Creating secret $name from \$$env_var"
    printf '%s' "${!env_var}" | gcloud secrets create "$name" \
      --data-file=- --replication-policy=automatic --project "$PROJECT_ID"
  fi
}

ensure_secret github-token GITHUB_TOKEN
ensure_secret gmail-app-password GMAIL_APP_PASSWORD

echo "==> Granting the Cloud Run runtime service account access to the secrets"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for secret in github-token gmail-app-password; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role=roles/secretmanager.secretAccessor \
    --project "$PROJECT_ID" >/dev/null
done

echo "==> Creating Firestore composite indexes (idempotent)"
# Filtering events by meeting_id while ordering by timestamp needs this.
# store.py falls back to a client-side sort without it, so a failure here is
# not fatal -- it just means /events?meeting_id= is slower than it should be.
gcloud firestore indexes composite create \
  --collection-group=events \
  --field-config=field-path=meeting_id,order=ascending \
  --field-config=field-path=timestamp,order=descending \
  --project "$PROJECT_ID" 2>/dev/null || echo "    (index already exists or is still building)"

echo "==> Deploying $SERVICE_NAME to project $PROJECT_ID in $REGION"
# --min-instances 1 and --no-cpu-throttling are load-bearing, not tuning.
# The monitoring loop and the Pub/Sub pull subscriber are background threads.
# Cloud Run's default (scale to zero, CPU throttled outside a request) stops
# both of them dead between HTTP calls, so blockers would never be detected
# and no commitment would ever be verified.
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,GCP_REGION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=True,GEMINI_MODEL=$GEMINI_MODEL,GITHUB_REPO=$GITHUB_REPO,GMAIL_SENDER=$GMAIL_SENDER,ESCALATION_RECIPIENT=$ESCALATION_RECIPIENT,CORS_ALLOWED_ORIGINS=$CORS_ALLOWED_ORIGINS,OFFLINE_MODE=false" \
  --set-secrets "GITHUB_TOKEN=github-token:latest,GMAIL_APP_PASSWORD=gmail-app-password:latest" \
  --memory 1Gi \
  --cpu 1 \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 3 \
  --timeout 300

echo "==> Deployed. Service URL:"
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
  --region "$REGION" --project "$PROJECT_ID" --format 'value(status.url)')
echo "$SERVICE_URL"

echo
echo "Next steps:"
echo "  1. Set VITE_API_BASE=$SERVICE_URL in frontend/.env"
echo "  2. Deploy the frontend (see frontend/README.md)"
echo "  3. Re-run this script with CORS_ALLOWED_ORIGINS=<frontend-url> so the"
echo "     deployed dashboard is allowed to call this API"
echo "  4. Check the startup log for the model preflight line:"
echo "     gcloud run services logs read $SERVICE_NAME --region $REGION --project $PROJECT_ID --limit 50"
