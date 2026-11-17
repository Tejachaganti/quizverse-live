# QuizVerse Live — Google Cloud Deployment Guide

This guide details the procedure for deploying QuizVerse Live to Google Cloud using Cloud Run, Cloud SQL for PostgreSQL, and Secret Manager.

---

## 1. Prerequisites

1. Google Cloud Project with Billing enabled.
2. Google Cloud SDK (`gcloud`) installed and authenticated:
   ```bash
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   ```
3. Required APIs enabled:
   ```bash
   gcloud services enable \
       run.googleapis.com \
       sqladmin.googleapis.com \
       secretmanager.googleapis.com \
       artifactregistry.googleapis.com \
       cloudbuild.googleapis.com
   ```

---

## 2. Cloud SQL PostgreSQL Provisioning

1. Create a Cloud SQL PostgreSQL instance (v16 recommended):
   ```bash
   gcloud sql instances create quizverse-pg-instance \
       --database-version=POSTGRES_16 \
       --tier=db-f1-micro \
       --region=us-central1 \
       --storage-auto-increase \
       --backup-start-time=03:00
   ```
2. Create the production database and user:
   ```bash
   gcloud sql databases create quizverse --instance=quizverse-pg-instance
   gcloud sql users set-password postgres \
       --instance=quizverse-pg-instance \
       --password="STRONG_DB_PASSWORD"
   ```

---

## 3. Secret Manager Configuration

Store sensitive credentials securely:
```bash
# Database URL (using Cloud SQL Unix socket path for Cloud Run)
echo -n "postgresql+psycopg://postgres:STRONG_DB_PASSWORD@/quizverse?host=/cloudsql/YOUR_PROJECT_ID:us-central1:quizverse-pg-instance" | \
    gcloud secrets create quizverse-database-url --data-file=-

# Gemini AI API Key (optional, for AI generation features)
echo -n "YOUR_GEMINI_API_KEY" | \
    gcloud secrets create quizverse-gemini-api-key --data-file=-
```

---

## 4. Database Schema Migration

Before directing live user traffic, apply all 13 Alembic migrations against the database:
```bash
# Using Cloud SQL Auth Proxy locally or via Cloud Build task:
cloud_sql_proxy -instances=YOUR_PROJECT_ID:us-central1:quizverse-pg-instance=tcp:5432 &
export DATABASE_URL="postgresql+psycopg://postgres:STRONG_DB_PASSWORD@127.0.0.1:5432/quizverse"
alembic upgrade head
```

---

## 5. Cloud Run Single-Instance Deployment

Deploy using `gcloud run deploy`:
```bash
gcloud run deploy quizverse-live \
    --source . \
    --region us-central1 \
    --platform managed \
    --allow-unauthenticated \
    --min-instances 1 \
    --max-instances 1 \
    --concurrency 200 \
    --timeout 3600 \
    --cpu 2 \
    --memory 2Gi \
    --set-env-vars="ENVIRONMENT=production,HOST=0.0.0.0,PORT=8000,LOG_LEVEL=INFO,ALLOWED_ORIGINS=https://your-domain.com,https://quizverse-live.a.run.app,capacitor://localhost" \
    --set-secrets="DATABASE_URL=quizverse-database-url:latest,GEMINI_API_KEY=quizverse-gemini-api-key:latest" \
    --add-cloudsql-instances YOUR_PROJECT_ID:us-central1:quizverse-pg-instance
```

> [!IMPORTANT]
> **Single-Instance Constraint (`--max-instances 1`)**:
> The in-memory authoritative `GameEngine` and presence state are local to the running process. Running more than 1 instance without external state centralization would split multiplayer rooms across different instances.

---

## 6. Custom Domain & SSL Verification

1. Map your custom domain:
   ```bash
   gcloud beta run domain-mappings create \
       --service quizverse-live \
       --domain your-domain.com \
       --region us-central1
   ```
2. Add the provided DNS `A` or `CNAME` records at your domain registrar.
3. Google Cloud automatically provisions and renews managed SSL/TLS certificates.

---

## 7. Rollback Procedure

If a deployed revision exhibits unexpected issues:
```bash
# List previous revisions
gcloud run revisions list --service=quizverse-live --region=us-central1

# Instantly route 100% of traffic back to the prior stable revision
gcloud run services update-traffic quizverse-live \
    --to-revisions=quizverse-live-PREVIOUS_REV=100 \
    --region=us-central1
```
