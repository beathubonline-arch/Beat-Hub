# BeatHub Distribution — MVP

A native BeatHub feature for artists to prepare releases for music distribution. This is **not** an active Spotify/Apple Music delivery integration.

## Artist workflow
1. Create a draft release: single, EP, or album.
2. Provide release title, primary artist, label, genre, explicit-content flag, planned date, territory and copyright ownership details.
3. Add tracks with title, version, featured artists, writers, composers, ISRC (optional until assigned), language, explicit flag and audio source.
4. Upload square cover artwork and master audio. Validate format and metadata on the server; do not treat an existing BeatHub marketplace upload as automatically licensed for distribution.
5. Confirm rights to masters, compositions, samples, artwork and artist names.
6. Submit to an internal review queue. The only supported statuses before a partner is integrated are DRAFT, NEEDS_CHANGES, IN_REVIEW, APPROVED_FOR_PARTNER, and REJECTED.
7. A release must never display DELIVERED, LIVE, streaming royalties, or store links without verified distributor acknowledgements and reporting.

## Operational requirements
- Require signed-in creator ownership on every API and object-storage operation.
- Separate distribution rights/permissions from BeatHub beat licensing and purchases.
- Keep audio private, serve time-limited download URLs to authorized reviewers.
- Add content moderation, copyright dispute/takedown procedures, duplicate release checks and audit trails.
- No distribution charges or royalties until a real distributor contract, payment verification and statements reconciliation exist.
- Partner adapters must be server-side with encrypted credentials, idempotency keys, retries and webhook signature validation.

## Acceptance tests
- Creator A cannot view or edit creator B's drafts, masters or artwork (test twice).
- Missing rights confirmation or invalid track metadata blocks review submission.
- A draft cannot be marked live by a creator or by unverified partner callbacks.
- Marketplace checkout, album pages, uploads and withdrawals remain unchanged.

## Integration status
**Scaffold only.** No production routes, database migrations, partner integrations, payouts or actual store delivery are enabled by this change.
