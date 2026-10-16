#!/usr/bin/env bash
# RampGuard AI — Cloud Run deployment script
# Run from the project root: bash deploy.sh
# Requires: gcloud CLI logged in, project set to rampguard-ai
set -e

PROJECT="rampguard-ai-511716"
REGION="us-central1"
SERVICE="rampguard-ai"
REPO="rampguard"
GCS_BUCKET="rampguard-ai-511716-media"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/$SERVICE:latest"

echo "=== Step 1: Enable required APIs ==="
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  --project="$PROJECT"

echo "=== Step 2: Create Artifact Registry repo (ok if it already exists) ==="
gcloud artifacts repositories create "$REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --project="$PROJECT" 2>/dev/null || echo "  (repo already exists)"

echo "=== Step 3: Create GCS bucket for evidence (ok if it already exists) ==="
gsutil mb -p "$PROJECT" -l "$REGION" "gs://$GCS_BUCKET" 2>/dev/null || echo "  (bucket already exists)"

echo "=== Step 4: Build and push image via Cloud Build ==="
gcloud builds submit \
  --tag "$IMAGE" \
  --project="$PROJECT"

echo "=== Step 5: Deploy to Cloud Run ==="
gcloud run deploy "$SERVICE" \
  --image "$IMAGE" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GCS_BUCKET=$GCS_BUCKET" \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1 \
  --timeout 300 \
  --port 8080 \
  --project="$PROJECT"

echo "=== Step 6: Grant Vertex AI + GCS permissions to Cloud Run service account ==="
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo "  Service account: $SA"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" \
  --role="roles/aiplatform.user" --quiet

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA" \
  --role="roles/storage.objectAdmin" --quiet

echo ""
echo "=== Done! ==="
SERVICE_URL=$(gcloud run services describe "$SERVICE" --platform managed --region "$REGION" --format 'value(status.url)' --project="$PROJECT")
echo "  URL: $SERVICE_URL"
echo ""
echo "  Test it: open $SERVICE_URL in your browser"
echo "  If you see a 403/error: wait 60s for IAM propagation and reload"
