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

The first commit left `app.py` unchanged. The follow-on commit now adds an opt-in signed webhook path; it is disabled unless `MKULIMA_FEEDBACK_ENABLED=1`. No real WhatsApp messages have been sent during these tests. Farmer feedback is not yet being collected by the live app. Render's configuration page requested sign-in, so existing secret presence could not be verified. Never request secret values in chat or copy them into a commit/log.

Next bounded milestone:

1. Verify the live service has the existing Supabase backend configuration and Meta app secret, without displaying values.
2. Verify `X-Hub-Signature-256` before processing Meta requests. HMAC-sign actor IDs with a stable server secret. Add durable message processing/state with retry-safe delivery handling; the current `/tmp` SQLite path is not durable.
3. Store each delivered answer and its WhatsApp message ID. Resolve feedback to the correct original answer with actor ownership, including explicit reply context. Ask for actual outcomes separately from usefulness votes.
4. Connect reviewed retrieval only after crop/coarse region/problem are known. Preserve precise landmarks in private operational data only. Do not claim all crops or free-form agricultural diagnosis are supported by the current maize-sale app.
5. Test two signed, mocked-delivery webhook flows and restart persistence before enabling the live feature. Real WhatsApp sends require explicit authorization.

## Video worker

Kaggle notebook version 10 contains the model-and-worker restart cell. Live output confirmed `MKULIMA_MODEL_READY`, `BEATHUB_PREFLIGHT_PASS: pipeline, decoder, database`, and `BEATHUB_KAGGLE_WORKER_READY`. Existing two generated MP4s remain in QC; this does not claim publishable visual quality. Kaggle sessions remain temporary and subject to free GPU availability/quota.

## Signed webhook milestone — 28 September, 05:52 EAT

Implemented in this PR, not deployed: raw-body Meta HMAC verification, batch message iteration, durable per-farmer state, stored replies before sending, delivery leases, retry after failed sends, duplicate exclusion and feedback linked to original outbound reply with actor ownership. English and Swahili feedback prompts are included. Existing behavior remains selected while the feature flag is unset. Logs no longer print recipient numbers, message content or arbitrary upstream error bodies.

Applied follow-on Supabase migration `mkulima_durable_webhook_state`; exact DDL in `webhook_state.sql`. Eight local tests passed, including two independent signed Flask conversations with mocked storage/delivery (English with numeric follow-ups and duplicate feedback; Swahili with failed delivery/retry and negative feedback). Another live Supabase transaction test passed for leases, durable state, staged-answer reuse, reply ownership and linked feedback, then rolled back. These are separate component/contract checks, not proof of live Render-to-Meta integration.

Known delivery limitation: if Meta accepts a send but the process dies before the database marks it sent, retry may send a duplicate reply. There is no claim of exactly-once external delivery. An unfinished message blocks later messages from that actor until retried successfully; operational recovery/retention is still needed for permanently undeliverable messages.

Live activation is blocked on verifying existing Render configuration: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, WHATSAPP_APP_SECRET, WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_VERIFY_TOKEN. These are names only; do not paste values in chat. The Meta app secret also produces stable HMAC actor IDs; rotating it requires an identity migration. Enable the feature flag only after signed live integration validation. Reviewed knowledge retrieval is available in the backend but is not yet injected into farmer answers.

Kaggle's fresh restart was verified at this milestone: Qwen 179.56s, DiT 68.75s, VAE 7.12s, followed by model-ready, preflight-pass and worker-ready output with the execution still running.
