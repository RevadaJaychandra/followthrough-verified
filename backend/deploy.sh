#!/bin/bash
# Deploy backend to Cloud Run.
# Run from the backend/ directory: bash deploy.sh
set -e

PROJECT_ID=${GCP_PROJECT_ID:-followthrough-hack}
REGION=${GCP_REGION:-us-central1}
SERVICE_NAME=followthrough-backend

echo "Deploying $SERVICE_NAME to project $PROJECT_ID in $REGION..."

gcloud run deploy $SERVICE_NAME \
  --source . \
  --project $PROJECT_ID \
  --region $REGION \
  --allow-unauthenticated \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,GCP_REGION=$REGION,GOOGLE_GENAI_USE_VERTEXAI=True,GEMINI_MODEL=gemini-3.5-flash" \
  --set-secrets "GITHUB_TOKEN=github-token:latest,GMAIL_APP_PASSWORD=gmail-app-password:latest" \
  --memory 1Gi \
  --min-instances 0 \
  --max-instances 3

echo "Deployed. Fetching URL..."
gcloud run services describe $SERVICE_NAME --region $REGION --project $PROJECT_ID --format 'value(status.url)'
