#!/bin/zsh
# Exit immediately if a command exits with a non-zero status
set -e

# Detect or configure Project ID
ACTIVE_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
PROJECT_ID=${1:-$ACTIVE_PROJECT}
PROJECT_ID=${PROJECT_ID:-"project-377b9806-bb2a-4919-82a"}

REGION="us-central1"

echo "============================================================"
echo "Rebuilding and deploying SignalSense AI Frontends..."
echo "============================================================"
echo "Project: $PROJECT_ID"
echo "Region:  $REGION"
echo "============================================================"

# Ensure gcloud is pointing to the correct project
gcloud config set project "$PROJECT_ID"

# 1. Deploy Member Ambassador App
echo "\n🚀 1/2. Deploying Member Ambassador App..."
# Copy signalsense_agent into member_ambassador_app to enable local fallback runner in container
rm -rf member_ambassador_app/signalsense_agent
cp -r signalsense_enterprise/signalsense_agent member_ambassador_app/
cp .env member_ambassador_app/

gcloud run deploy member-app \
  --source member_ambassador_app \
  --region "$REGION" \
  --allow-unauthenticated \
  --quiet

rm -rf member_ambassador_app/signalsense_agent
rm -f member_ambassador_app/.env

# 2. Deploy Operations Dashboard App
echo "\n🚀 2/2. Deploying Operations Dashboard App..."
cp .env operations_dashboard/
gcloud run deploy ops-dashboard \
  --source operations_dashboard \
  --region "$REGION" \
  --allow-unauthenticated \
  --quiet

rm -f operations_dashboard/.env

echo "\n============================================================"
echo "🎉 Frontend applications successfully redeployed!"
echo "============================================================"
