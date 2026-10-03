# Kenya Pulse release handoff — 3 October 2026

## Verified production evidence

- Repository branch `kenya-pulse-mvp`, deployed commit `1888918680161c204e4ef95cf8a1b253532abbff`.
- Render deployment `dep-db033lukemhc73eekepg` is recorded as live. Startup logs show Gunicorn booting and HTTP 200 requests. This establishes deployment, not application correctness.
- Service `srv-davqqdjtqb8s73df54ug` follows that branch with automatic deploy enabled. Build: `pip install -r kenya-pulse/requirements.txt`; start: `gunicorn --chdir kenya-pulse app:app --bind 0.0.0.0:$PORT`.
- PostgreSQL `kenya-pulse-db` (`dpg-davqq7navr4c73d2vbbg-a`) exists and is available in Frankfurt. Its external IP allowlist is empty; the connector cannot query it. Preserve this network isolation.
- Render reports that the free database expires **1 November 2026 at 13:09 UTC**. A durable launch requires a retention/paid-plan decision before then. No plan was changed.
- Environment values were not accessible through the available connector. The running database type is **unverified**. The old code falls back to `/tmp/kenya-pulse.db` when `DATABASE_URL` is absent.
- Public health retrieval failed through search; browser returned `ERR_BLOCKED_BY_CLIENT`; the terminal request timed out. No live user journey, live health payload, domain certificate or mobile screenshot has been verified.

## Prepared changes

- Additive registry migration, permanent candidate-ID results, verified canonical identities and aliases, explicit confirmation/ambiguous selection, no fuzzy merges. Existing rows and unresolved historical labels remain intact.
- A separate `identity_verified` flag defaults to false. Existing registry records require explicit review against source evidence before they can accept new responses. Existing results remain visible; this does not rename or merge historical people.
- `GET /admin/candidates` lists review records in pages of 100. `POST /admin/candidates` creates or reviews an existing `candidate_id` with `name`, `race`, applicable geography, `source_url`, `identity_verified: true`, optional `party`, `status` and `verified_aliases`. Use a secure Bearer header, never a URL key. An existing ID cannot be moved to another race or area.
- Server-enforced seat order, atomic duplicate slots, refresh recovery, MP/MCA constituency consistency. The UI offers optional support only when the server confirms all six responses. Dismissal persists for the browser tab's session.
- Full structural geography checks, duplicate JSON-key detection, all 337 geography API responses verified against the file (47 county lists + 290 ward lists). Counts: 47 / 290 / 1,450; Kericho inspected.
- Test-only Paystack support, integer minor-unit amounts, unique intents, provider-hosted checkout, HMAC-SHA512 signature checking, server-side transaction verification, exact amount/currency/reference/test-domain checking and idempotent settlement. Contributions never join or update votes/results/ads.
- Header-only admin authorization; CSRF; same-origin checks; request-size/type validation; shared database rate limits; secure HttpOnly SameSite cookies; security headers; safer ad redirects; PostgreSQL-compatible ads and boolean queries; pinned patched dependencies.
- Health separates database connectivity, geography integrity, registry coverage and payment configuration. `production_ready` intentionally remains false pending deployed verification and release review; it is not an automatic launch certification.
- SQLite-to-PostgreSQL importer defaults to a rollback rehearsal, requires empty target data tables and refuses to overwrite existing records. It preserves source IDs, aliases and responses without resolving identities.

## Tests and their limits

1. Python suite against SQLite and real local PostgreSQL: **39 passed; 1 SQLite-only skip of the PostgreSQL import test**, including the final health/admin additions.
2. Independent localhost HTTP journey: six seats, aliases, optional payment disabled safely, actual app-process restart, identical results/aliases and saved progress, duplicate rejection after restart.
3. Node execution of the shipped UI script with a DOM model: no support on first five steps; support after MCA; dismissed prompt stays hidden after refresh; explicit identity confirmation; multiple matches; unknown name rejected. This is **not a visual browser or mobile test**.
4. `pip-audit` on pinned runtime dependencies: no known vulnerabilities reported on 3 October 2026. This is not a guarantee against unknown vulnerabilities.
5. Paystack tests use controlled provider responses, including tampering, replay, wrong currency/domain and fake references. **No genuine Paystack end-to-end payment has passed. No live keys are accepted.**

CI repeats database tests, UI logic checks and the localhost HTTP/restart smoke test. Tests create synthetic data only in local/CI schemas, never in production political results.

## Deployment gate — preserve the current data first

Do **not** merge into `kenya-pulse-mvp`, change its environment and restart, or manually redeploy until the running data is secured. Any of those can replace ephemeral SQLite storage.

1. Open the existing [Render service](https://dashboard.render.com/web/srv-davqqdjtqb8s73df54ug). Check Environment for whether `DATABASE_URL` exists, without revealing its value. Open `/health` yourself and confirm its database field.
2. If it is PostgreSQL, verify the attached database is `kenya-pulse-db` through the dashboard and make a protected backup from an authorized internal connection. Record only table counts in release notes, not preferences or secrets.
3. If it is SQLite, obtain a **consistent backup of the running `/tmp/kenya-pulse.db` before any restart**. Use the current instance's shell if Render exposes it, with SQLite's backup API; do not rely on copying a file while writes are in flight. If the free service offers no current-instance shell, stop here and obtain Render support/access to preserve the running instance. A new deployment cannot recover an old ephemeral file. Do not upgrade/restart assuming it preserves that file.
4. In an authorized environment with the protected backup and the new source, set the target database URL securely. Rehearse `python kenya-pulse/migrate_sqlite.py --source /secure/backup.db`. The target must be empty. During a controlled pause in submissions, capture a final consistent backup, then repeat with `--apply`. Compare all returned counts and specific IDs/aliases privately. The importer does not delete or overwrite source/target data. Do not put backups in Git.
5. Only after the backup/cutover is safe: in **kenya-pulse-db → Info / Connections**, copy **Internal Database URL** directly into **kenya-pulse-live → Environment → DATABASE_URL**. Do not paste it into chat. Then deploy the reviewed commit.
6. Keep the existing `PULSE_SALT` stable during migration to preserve fingerprints. Configure `PULSE_ADMIN_KEY` and a separate long random `PULSE_SESSION_SECRET` in Render. If old admin keys were used in URLs, rotate them securely after the old URL authentication is removed. No values belong in Git or chat.
7. Verify `/health` shows PostgreSQL, schema/geography good, and realistic registry coverage. Review registry identities and aliases from real sources. Do not populate production with the tests' synthetic candidates.
8. Run the full deployed journey in an **isolated staging database**. Record legitimate production counts before/after a restart without adding fake political preferences to public results. Confirm aliases, IDs and results persist. Test the UI at mobile width in a real browser. Only after these checks is deployed test pass #2 satisfied.

## Paystack action

After safe deployment and PostgreSQL attachment:

- In Render secure environment variables, enter **PAYSTACK_SECRET_KEY** using the account's **test secret key**, and **PAYSTACK_MODE=test**.
- Set **PULSE_PUBLIC_URL=https://kenya-pulse-live.onrender.com** until the owned domain is verified.
- In Paystack's **test-mode** webhook settings use `https://kenya-pulse-live.onrender.com/api/support/webhook`.
- The server supplies callback `https://kenya-pulse-live.onrender.com/support/return`.
- Complete two separate real Paystack **test** checkouts in staging, checking signed webhooks and server verification. Verify repeated callbacks do not produce duplicate settlement and results remain identical.
- Account approval and supported payment channels/amounts remain external. The app accepts positive KES amounts with at most two decimal places; a provider may reject an amount/channel, which never blocks participation.
- Live payments are deliberately disabled in code, even if a live key is accidentally entered. A later reviewed activation requires both end-to-end tests and account approval.

Official contracts reviewed: [initialize/verify](https://paystack.com/docs/api/transaction/), [webhooks](https://paystack.com/docs/payments/webhooks/), [verification](https://paystack.com/docs/payments/verify-payments/).

## Domain and email actions

Ownership of `kenyapulse.org` and the email provider have not been verified. No purchase, DNS edit or mailbox creation has occurred.

Once you own the domain, open **Render service → Settings → Custom Domains**, add `kenyapulse.org`, then inspect Render's actual DNS instructions. Render documents automatic `www` to root redirection when adding a root domain.

For Namecheap, Render's official documentation currently specifies the following records; these are documentation-derived, **not retrieved from this service's Custom Domains panel**:

| Type | Host | Target |
| --- | --- | --- |
| A | `@` | `216.24.57.1` |
| CNAME | `www` | `kenya-pulse-live.onrender.com` |

Check against the values the dashboard displays before applying. Preserve unrelated mail records. Resolve conflicting web A/AAAA/redirect records for these hosts. Verify root HTTPS, `http` to `https`, `www` redirection, certificate and health after propagation. Render provides TLS; no extra hosting or separate SSL purchase is needed. Sources: [Render custom domains](https://render.com/docs/custom-domains), [Render Namecheap DNS](https://render.com/docs/configure-namecheap-dns).

Start with one mailbox and aliases if the provider supports them:

| Address | Role |
| --- | --- |
| hello@kenyapulse.org | Main mailbox/general enquiries |
| support@kenyapulse.org | Participant support |
| payments@kenyapulse.org | Contribution enquiries |
| security@kenyapulse.org | Security reports |
| privacy@kenyapulse.org | Privacy/deletion requests |
| advertise@kenyapulse.org | Commercial advertisers |

Use the chosen provider's exact MX, SPF and DKIM records. Publish only one SPF record per host; use the provider's actual DKIM selector. Establish a monitored DMARC reporting mailbox before configuring the policy; start with monitoring, verify aligned delivery, then consider enforcement. No provider-specific DNS values have been guessed. Mail aliases are not proof that a mailbox exists. Verify each alias receives mail and inspect authentication results on a delivered test message before replacing the existing contact address.

## Remaining security/operational limits

- Technical fingerprinting is not identity verification or one-person-one-response. Shared networks/devices can collide and a changed client can evade it. Methodology must retain this limitation.
- Existing inline JavaScript requires CSP `unsafe-inline`; the policy still restricts external origins, framing, base URLs and forms. A nonce/external-script refactor is a separate hardening step.
- The proxy fingerprint trusts the nearest forwarded address only on Render. Confirm actual proxy behavior during staging tests; compare existing fingerprint continuity before launch.
- No authenticated Render environment inspection, production backup, database attachment, custom-domain setup, email authentication or visual mobile verification was possible with the current access.
