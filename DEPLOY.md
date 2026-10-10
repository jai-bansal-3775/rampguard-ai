# Cloud Run Deployment

## Prerequisites

- Google Cloud SDK installed and authenticated (`gcloud auth login`)
- Active project set: `gcloud config set project YOUR_PROJECT_ID`
- Cloud Run API enabled: `gcloud services enable run.googleapis.com`

## Quick deploy

```bash
gcloud run deploy rampguard-ai \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars GEMINI_API_KEY=your_key_here \
  --memory 1Gi \
  --cpu 1 \
  --max-instances 10
```

## Production notes

| Component | Prototype | Production |
|---|---|---|
| Database | SQLite (ephemeral) | Firestore / Cloud SQL |
| Evidence images | Container filesystem | Cloud Storage (GCS) |
| API key | `--set-env-vars` | Cloud Run secret (`--set-secrets`) |

> **Important:** The Cloud Run container filesystem is reset on every deploy and restart.
> SQLite data and evidence images are lost between deployments.
> For a persistent production deployment, migrate to Firestore (events/history) and GCS (evidence images).

## Using a secret for the API key (recommended)

```bash
echo -n "your_key_here" | gcloud secrets create GEMINI_API_KEY --data-file=-
gcloud run deploy rampguard-ai \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets GEMINI_API_KEY=GEMINI_API_KEY:latest \
  --memory 1Gi
```
