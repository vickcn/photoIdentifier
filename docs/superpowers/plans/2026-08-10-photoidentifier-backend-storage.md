# PhotoIdentifier Backend Storage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated backend service account and temporary exports bucket for PhotoIdentifier, validate access, and prepare a no-downtime Vercel credential migration path.

**Architecture:** Keep the current production Firestore credential as rollback while introducing a new backend service account with least-privilege access to Firestore and a single temporary exports bucket. Update server-side credential resolution so the backend can prefer a new service-account env var and safely fall back to the current Firestore env var during migration.

**Tech Stack:** Python, FastAPI, pytest, Google Cloud IAM, Cloud Storage, Firestore, Vercel CLI

## Global Constraints

- Current date: `2026-08-10`.
- Preserve Traditional Chinese text encoding correctly.
- Follow the minimal-change principle.
- Do not delete or de-authorize `photoidentifier-firestore@vision-493709.iam.gserviceaccount.com`.
- Do not grant project-level `Storage Admin`.
- Scope Storage IAM to `gs://vision-493709-photoidentifier-exports` only.
- Do not write private keys, full JSON credentials, or secrets into the repo, logs, or final response.
- Do not modify current Firestore collections or schema for this task.

---

### Task 1: Add Backend Credential Env Fallback

**Files:**
- Create: `tests/test_batch_state_store.py`
- Modify: `src/batch_state_store.py`
- Modify: `.env.example`
- Modify: `USAGE.md`

**Interfaces:**
- Produces: `get_backend_service_account_json() -> str`
- Produces: `create_batch_state_store()` recognizes `PHOTOIDENTIFIER_BACKEND_SERVICE_ACCOUNT_JSON` first and `FIRESTORE_SERVICE_ACCOUNT_JSON` second.

- [ ] Write failing tests for env precedence and store bootstrap conditions.
- [ ] Run the targeted pytest command and verify the new tests fail for the expected reason.
- [ ] Implement the smallest helper-based change in `src/batch_state_store.py`.
- [ ] Update env documentation to describe the preferred and fallback variables.
- [ ] Re-run the targeted pytest command and confirm it passes.

### Task 2: Provision Backend GCP Resources

**Files:**
- Create: `docs/gcp/photoidentifier-backend-storage.md`
- Create: `scripts/provision_photoidentifier_backend_storage.sh`

**Interfaces:**
- Produces: idempotent CLI for service account, bucket, lifecycle, public-access prevention, and bucket-scoped IAM.

- [ ] Create the backend service account in project `vision-493709`.
- [ ] Grant `roles/datastore.user` to the new service account.
- [ ] Create or reconcile bucket `gs://vision-493709-photoidentifier-exports`.
- [ ] Enforce `asia-east1`, uniform bucket-level access, public access prevention, and age-1-day delete lifecycle.
- [ ] Grant bucket-scoped `roles/storage.objectAdmin` to the new service account only on the exports bucket.
- [ ] Write the exact CLI flow into the script and deployment note.

### Task 3: Validate Access And Prepare Migration

**Files:**
- Modify: `docs/gcp/photoidentifier-backend-storage.md`

**Interfaces:**
- Produces: verified Firestore and Cloud Storage access results for the new backend service account.
- Produces: production migration checklist without switching production credentials yet.

- [ ] Prepare a local credential file for the new backend service account outside the repo.
- [ ] Validate Firestore write, read, and delete with the new service account.
- [ ] Validate Cloud Storage upload, read, and delete with the new service account.
- [ ] Verify bucket lifecycle configuration and confirm no public IAM bindings exist.
- [ ] Record which Vercel env vars remain manual and which production steps are still pending.

