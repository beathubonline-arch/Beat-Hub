# Mkulima farmer feedback and knowledge loop

Adopted product architecture, 27 September 2026. This document is a requirement, not a claim that the loop is deployed.

Farmer question → Mkulima answer → farmer feedback/outcome → structured interaction → evaluation → validated knowledge → Mkulima Knowledge Base → context for future answers.

## Capture

Offer Helpful, Wrong, and Still problem feedback linked to the original answer. Keep a separate outcome description: Helpful is a usefulness signal, not proof of agricultural correctness. Wrong and Still problem must trigger review/follow-up, not silently become positive training examples.

Persist interaction ID, inbound message ID (unique for retry safety), answer version, crop, location context, language, problem, recommendation, outcome, confidence (nullable until assessed), timestamps, feedback and evidence provenance. Preserve the farmer's language. Unknown fields stay unknown. Support later outcomes and corrections without overwriting the original record.

## Evaluate and promote

Use pending → under_review → validated / rejected, with revocation of previously validated knowledge. Validation needs a named reviewer, supporting evidence/source, review date, geographic/crop applicability and expiry/review date. Never validate solely from a thumb vote or the model's confidence. Contradictory outcomes pause reuse and reopen review. Keep confidence meaning and its evidence explicit; do not fabricate numeric certainty.

Only validated, current, non-revoked entries are eligible for retrieval. Match crop, region, language and problem; provide sources and uncertainty in future answers. Knowledge-base retrieval improves answer context; it is not automatic model-weight retraining.

## Privacy and durability

Keep phone numbers, exact locations and raw conversations in restricted operational records. Publish only reviewed, de-identified knowledge; do not copy landmarks or phone numbers into shared entries. Support deletion and provenance-aware removal. Use backend-only writes with row-level security if Supabase is the durable store; never expose service-role credentials to clients or notebook output.

The inspected app currently uses DB_PATH defaulting to /tmp/mkulima.db for conversations and processed_messages. That is ephemeral on the free Render service. Durable feedback storage must be wired and verified before claiming the loop works. Confirm the app's intended Supabase project and existing secret configuration without displaying secret values. Do not assume the video-jobs database is automatically the farmer-data database.

## Zero-budget implementation and acceptance

Use existing free-tier infrastructure. No new paid model API, service or database is authorized by this architecture. Keep the Kaggle video worker separate from farmer-answer validation.

Test two independent end-to-end paths before declaring the learning loop operational:

1. Question → answer → Helpful plus actual outcome → durable record → evidence/reviewer validation → future matching answer retrieves the validated entry. Duplicate delivery creates no extra interaction. Restart must preserve it.
2. Question → answer → Wrong or Still problem → follow-up and review. Unvalidated material is never retrieved. Reject or revoke the entry and prove future answers exclude it, including cross-region/crop mismatch and expired knowledge.

Use synthetic farmer records and mocked outbound WhatsApp delivery for development. Sending real user messages requires explicit authorization. Report separately what is implemented, deployed, and observed working.
