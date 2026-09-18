# BeatHub

BeatHub — the home of cool beats.

Creator Marketplace Dashboard V6 is deployed from the `main` branch.

## AI Cover Studio

The creator dashboard includes an optional Higgsfield-powered cover generator.
Copy the combined credential shown by Higgsfield and add this server-side
environment variable to enable it:

- `HIGGSFIELD_API_KEY`

BeatHub also supports the separate `HIGGSFIELD_API_KEY_ID` and
`HIGGSFIELD_API_KEY_SECRET` variables for accounts that display the pair
individually.

Optional controls:

- `HIGGSFIELD_IMAGE_RESOLUTION` (`1k` by default)
- `HIGGSFIELD_GENERATIONS_PER_HOUR` (`3` by default)

API credentials must stay in the deployment environment and must never be
placed in browser JavaScript, the mobile app, or committed to this repository.
