#!/bin/zsh
# Exit immediately if a command exits with a non-zero status
set -e

# Configured Project ID
PROJECT_ID=${1:-"project-377b9806-bb2a-4919-82a"}

echo "============================================================"
echo "Setting up Google Cloud & Capstone Project environment..."
echo "============================================================"

# 1. Select Google Cloud Project
echo "\n1. Configuring project: ${PROJECT_ID}..."
gcloud config set project "${PROJECT_ID}"

# 2. Authenticate to Google Cloud
echo "\n2. Authenticating Google Cloud..."
echo "Authenticating gcloud user account..."
gcloud auth login

echo "\nAuthenticating Application Default Credentials (ADC)..."
gcloud auth application-default login

# 3. Enable Generative Platform APIs
echo "\n3. Enabling necessary Google Cloud APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  cloudtrace.googleapis.com \
  cloudbuild.googleapis.com \
  agentregistry.googleapis.com

# 4. Initialize Local Python Environment
echo "\n4. Synchronizing local dependencies using uv..."
if command -v uv &> /dev/null; then
  uv sync
else
  echo "uv is not installed. Skipping local dependency sync."
fi

echo "\n============================================================"
echo "Environment setup successfully completed!"
echo "============================================================"
