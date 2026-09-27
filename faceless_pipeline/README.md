# BeatHub Faceless Multi-Channel Engine

Reusable orchestration service for faceless media channels.

## Production flow
research -> analytics -> experiment -> script/prompt -> generation -> render -> technical QC -> audio -> packaging -> creative QC -> human approval -> schedule -> publish -> analytics ingest -> learning.

## Formats
- Short-form: YouTube Shorts, TikTok, Instagram Reels.
- Long-form: YouTube-first scripted multi-scene production.

## Multi-channel isolation
Every job carries a channel_id. Channel branding, niche, prompts, experiments and analytics remain isolated. BeatHub is Channel 01; additional niches reuse the engines without sharing channel-specific learning.

## 48-hour upgrade loop
A scheduled audit checks for meaningful new capabilities every two days. New models/features enter as candidates only. Production promotion requires compatibility checks and two regression passes. Discovery alone never replaces a working component.

## Safety and reliability
- Human approval remains mandatory before publication.
- Audio QC is mandatory before publication.
- Production media URL is mandatory before scheduling.
- Diagnostic fixtures must never be published.
- Instagram remains disabled until connected.
- Publishing uses idempotency keys to support duplicate protection in the distribution adapter.
- Failed upgrade candidates leave the current production adapter untouched.

## Current phase
Phase 10 upgrade intelligence and Phase 11 adapter gates are represented by the upgrade candidate API and the external 48-hour audit. Phase 12 multi-channel structure is active. Phase 13 monitoring/hardening continues at the deployment layer.
