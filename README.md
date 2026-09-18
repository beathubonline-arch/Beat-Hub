# BeatHub

BeatHub — the home of cool beats.

Creator Marketplace Dashboard V6 is deployed from the `main` branch.

## AI Cover Studio

The creator dashboard includes an optional Higgsfield-powered cover generator.
Add these server-side environment variables to enable it:

- `HIGGSFIELD_API_KEY_ID`
- `HIGGSFIELD_API_KEY_SECRET`

Optional controls:

- `HIGGSFIELD_IMAGE_RESOLUTION` (`1k` by default)
- `HIGGSFIELD_GENERATIONS_PER_HOUR` (`3` by default)

API credentials must stay in the deployment environment and must never be
placed in browser JavaScript, the mobile app, or committed to this repository.
