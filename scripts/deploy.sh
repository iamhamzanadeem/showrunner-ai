#!/usr/bin/env bash
# ==============================================================================
# Showrunner AI - Google Cloud Run Deployment Script
# Deploys Showrunner AI to Cloud Run with GCS FUSE volume mounting for /data.
# ==============================================================================

set -euo pipefail

# ------------------------------------------------------------------------------
# Configuration Variables
# ------------------------------------------------------------------------------
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "")}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-showrunner-ai}"
SA_NAME="${SA_NAME:-showrunner-sa}"
MODE="${MODE:-vertex}"          # Options: 'vertex' (ADC via Workload Identity) or 'apikey' (Secret Manager)
DEMO_MOCK="${DEMO_MOCK:-0}"     # Set to 1 to deploy in mock mode (zero paid API cost)

if [[ -z "${PROJECT_ID}" ]]; then
  echo "ERROR: PROJECT_ID is required. Set PROJECT_ID=<your-project-id> or run 'gcloud config set project <id>'." >&2
  exit 1
fi

BUCKET="${BUCKET:-${PROJECT_ID}-showrunner-media}"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=== Showrunner AI Deployment ==="
echo "Project ID : ${PROJECT_ID}"
echo "Region     : ${REGION}"
echo "Service    : ${SERVICE}"
echo "Bucket     : gs://${BUCKET}"
echo "Service Acc: ${SA_EMAIL}"
echo "Mode       : $( [[ "${DEMO_MOCK}" == "1" ]] && echo "DEMO_MOCK" || echo "${MODE}" )"
echo "================================="

# ------------------------------------------------------------------------------
# 1. Enable Required GCP APIs
# ------------------------------------------------------------------------------
echo "==> Enabling Google Cloud APIs..."
gcloud services enable \
  run.googleapis.com \
  aiplatform.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}"

# ------------------------------------------------------------------------------
# 2. GCS Bucket for Media Assets (/data mount)
# ------------------------------------------------------------------------------
echo "==> Ensuring GCS storage bucket gs://${BUCKET} exists..."
if ! gcloud storage buckets describe "gs://${BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://${BUCKET}" \
    --project="${PROJECT_ID}" \
    --location="${REGION}" \
    --uniform-bucket-level-access
  echo "    Bucket gs://${BUCKET} created."
else
  echo "    Bucket gs://${BUCKET} already exists."
fi

# ------------------------------------------------------------------------------
# 3. Dedicated Service Account & IAM Bindings
# ------------------------------------------------------------------------------
echo "==> Ensuring service account ${SA_EMAIL} exists..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Showrunner AI Cloud Run Service Account" \
    --project="${PROJECT_ID}"
  echo "    Service account ${SA_NAME} created."
else
  echo "    Service account ${SA_NAME} already exists."
fi

echo "==> Binding IAM roles to service account..."
# Vertex AI Platform User
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --condition=None \
  --quiet >/dev/null

# GCS Storage Object Admin on bucket
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin" \
  --quiet >/dev/null

# ------------------------------------------------------------------------------
# 4. Mode-specific Environment & Secret Configuration
# ------------------------------------------------------------------------------
DEPLOY_SECRETS_ARG=()

if [[ "${DEMO_MOCK}" == "1" ]]; then
  echo "==> Configuring DEMO_MOCK mode (zero model cost)..."
  DEPLOY_ENV_VARS="SHOWRUNNER_PROVIDER=mock,DATA_DIR=/data,PORT=8080"
elif [[ "${MODE}" == "apikey" ]]; then
  echo "==> Configuring API Key mode via Secret Manager..."
  SECRET_NAME="gemini-api-key"
  if ! gcloud secrets describe "${SECRET_NAME}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    if [[ -z "${GEMINI_API_KEY:-}" ]]; then
      echo "ERROR: GEMINI_API_KEY env var must be set locally to create secret '${SECRET_NAME}'." >&2
      exit 1
    fi
    printf "%s" "${GEMINI_API_KEY}" | gcloud secrets create "${SECRET_NAME}" \
      --project="${PROJECT_ID}" \
      --data-file=- \
      --replication-policy="automatic"
    echo "    Secret '${SECRET_NAME}' created."
  fi

  # Grant secretAccessor to service account
  gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
    --project="${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet >/dev/null

  DEPLOY_ENV_VARS="SHOWRUNNER_PROVIDER=google,DATA_DIR=/data,PORT=8080"
  DEPLOY_SECRETS_ARG=( "--set-secrets=GEMINI_API_KEY=${SECRET_NAME}:latest" )
else
  echo "==> Configuring Vertex AI mode with Workload Identity..."
  DEPLOY_ENV_VARS="SHOWRUNNER_PROVIDER=google,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,VERTEX_LOCATION_MEDIA=us-central1,DATA_DIR=/data,PORT=8080"
fi

# ------------------------------------------------------------------------------
# 5. Cloud Run Service Deployment
# ------------------------------------------------------------------------------
echo "==> Deploying Showrunner AI service to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT_ID}" \
  --region "${REGION}" \
  --service-account "${SA_EMAIL}" \
  --allow-unauthenticated \
  --memory 4Gi \
  --cpu 2 \
  --timeout 3600 \
  --no-cpu-throttling \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 20 \
  --set-env-vars "${DEPLOY_ENV_VARS}" \
  ${DEPLOY_SECRETS_ARG[@]+"${DEPLOY_SECRETS_ARG[@]}"} \
  --add-volume "name=media,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount "volume=media,mount-path=/data"

# ------------------------------------------------------------------------------
# 6. Output Endpoint URL
# ------------------------------------------------------------------------------
SERVICE_URL="$(gcloud run services describe "${SERVICE}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"
echo ""
echo "=========================================================================="
echo "🎉 Showrunner AI successfully deployed!"
echo "Service URL : ${SERVICE_URL}"
echo "Health Check: ${SERVICE_URL}/api/health"
echo "Web Studio  : ${SERVICE_URL}/"
echo "=========================================================================="
