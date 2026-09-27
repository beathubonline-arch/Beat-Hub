# Farmer feedback foundation — 28 September 2026 (Africa/Nairobi)

## Implemented and verified

The existing Supabase project `beixlzqbuascruzhcety` now contains isolated `mkulima_interactions`, `mkulima_feedback`, and `mkulima_knowledge` tables and three backend-only RPC functions. Migration name: `mkulima_feedback_foundation`. No marketplace tables were changed. `feedback_schema.sql` records the exact applied DDL; do not run it again against this project.

`feedback.py` is a standard-library-only backend adapter. It captures crop, private location context, language, problem, recommendation, optional confidence, answer version and question. Farmer IDs use HMAC, not raw phone numbers. It supports explicit English/Swahili feedback commands and an optional outcome after a colon. No new paid services or dependencies.

Votes never create validated knowledge. A trusted reviewer prepares a de-identified candidate with a coarse region and reviewed content, then calls `mkulima_review_knowledge`. Promotion requires an actual outcome, reviewer, evidence, future review date and explicit de-identification attestation. This remains a human review responsibility; the database cannot determine whether evidence is true or whether free text contains identifying details.

Retrieval returns only validated, current entries matching crop, region, language and problem. A later Wrong/Still problem report atomically changes linked validated knowledge to needs_review. Revoked and expired knowledge is excluded. Deleting an interaction cascades to its feedback and derived knowledge.

## Verification evidence

- Five local Python tests passed: multilingual command parsing, opaque stable identity, REST/idempotency contract, ownership parameters, and error redaction. The REST transport in these tests is mocked; this is not a Render-to-Supabase integration test.
- Live Supabase path 1 passed under `service_role`: capture, repeated feedback deduplication, rejection of missing evidence, validation, retrieval, crop/region boundaries and revocation.
- Live Supabase path 2 passed under `service_role`: cross-farmer rejection, thumb-only validation rejection, actual outcome, negative feedback pause and expiry.
- Test rows were transactional and rolled back. `test_feedback_database.sql` contains both reproducible paths.
- All three tables have RLS enabled; anon and authenticated have no SELECT access. RPC execution is backend-only. Advisor INFO `rls_enabled_no_policy` is expected for this backend-only design; no client policies should be added simply to silence it. Reference: https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy

## Not yet connected

This branch does not modify `app.py` or send WhatsApp messages. Farmer feedback is not yet being collected by the live app. Render's configuration page requested sign-in, so existing secret presence could not be verified. Never request secret values in chat or copy them into a commit/log.

Next bounded milestone:

1. Verify the live service has the existing Supabase backend configuration and Meta app secret, without displaying values.
2. Verify `X-Hub-Signature-256` before processing Meta requests. HMAC-sign actor IDs with a stable server secret. Add durable message processing/state with retry-safe delivery handling; the current `/tmp` SQLite path is not durable.
3. Store each delivered answer and its WhatsApp message ID. Resolve feedback to the correct original answer with actor ownership, including explicit reply context. Ask for actual outcomes separately from usefulness votes.
4. Connect reviewed retrieval only after crop/coarse region/problem are known. Preserve precise landmarks in private operational data only. Do not claim all crops or free-form agricultural diagnosis are supported by the current maize-sale app.
5. Test two signed, mocked-delivery webhook flows and restart persistence before enabling the live feature. Real WhatsApp sends require explicit authorization.

## Video worker

Kaggle notebook version 10 contains the model-and-worker restart cell. Live output confirmed `MKULIMA_MODEL_READY`, `BEATHUB_PREFLIGHT_PASS: pipeline, decoder, database`, and `BEATHUB_KAGGLE_WORKER_READY`. Existing two generated MP4s remain in QC; this does not claim publishable visual quality. Kaggle sessions remain temporary and subject to free GPU availability/quota.
