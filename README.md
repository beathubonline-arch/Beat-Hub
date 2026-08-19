# BeatHub

A music marketplace where producers/artists/DJs sell beats and tracks, with
real M-Pesa Daraja payments, exclusive vs non-exclusive licensing, automatic
platform commission splitting, and a working admin dashboard.

Built with FastAPI + SQLAlchemy + Jinja2, PostgreSQL in production.

---

## 1. Local Setup

```bash
# 1. Clone / download the project, then enter it
cd beathub

# 2. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your .env file
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY and DATABASE_URL.
# For quick local testing you can use SQLite:
#   DATABASE_URL=sqlite:///./beathub.db
# For anything resembling production, use PostgreSQL (see below).

# 5. Run database migrations
alembic upgrade head

# 6. Start the app
uvicorn main:app --reload
```

Visit `http://localhost:8000`.

The app also auto-creates tables on startup as a convenience for fresh
SQLite databases — but `alembic upgrade head` is the correct, safe way to
manage schema in PostgreSQL / production.

---

## 2. PostgreSQL Configuration

Create a database and point `DATABASE_URL` at it:

```env
DATABASE_URL=postgresql://username:password@host:5432/beathub
```

Then run:

```bash
alembic upgrade head
```

---

## 3. M-Pesa (Daraja) Configuration

1. Create an app at [developer.safaricom.co.ke](https://developer.safaricom.co.ke)
   to get your Consumer Key / Consumer Secret.
2. Fill in `.env`:

```env
MPESA_ENVIRONMENT=sandbox        # switch to "production" when ready
MPESA_CONSUMER_KEY=your-key
MPESA_CONSUMER_SECRET=your-secret
MPESA_SHORTCODE=your-shortcode          # sandbox default is 174379
MPESA_PASSKEY=your-passkey
MPESA_CALLBACK_URL=https://your-deployed-domain.com/mpesa/callback
```

**`MPESA_CALLBACK_URL` must be a public HTTPS URL** — Safaricom cannot reach
`localhost`. For local testing, use a tunnel (e.g. `ngrok http 8000`) and set
the callback URL to the tunnel's HTTPS address plus `/mpesa/callback`.

3. Set `PLATFORM_COMMISSION_PERCENT` (defaults to `10`).

Nothing about M-Pesa is hard-coded — every value comes from `.env`. Swapping
sandbox credentials for production credentials and flipping
`MPESA_ENVIRONMENT=production` is the only change needed to go live.

---

## 4. First Purchase Test (Safe Test Flow)

1. Deploy (or tunnel) the app so `MPESA_CALLBACK_URL` is reachable.
2. Sign up as a **Producer / Artist / DJ** account.
3. Go to **Dashboard → Upload Track(s)**, upload a track, set a low test
   price (e.g. KSh 1), and choose **Non-Exclusive** for your first test (so
   you can repeat the test without needing a second track).
4. Log out, sign up as a **Buyer** with a different email.
5. Find the track (via search or `/beats`) and click **Buy Now**.
6. Enter a real M-Pesa-registered phone number (sandbox numbers if using
   sandbox) and submit.
7. Approve the STK push prompt on the phone.
8. The order-status page polls automatically and will flip to
   **COMPLETED** once Safaricom's callback hits `/mpesa/callback`.
9. Log back in as the producer — **Dashboard** will show the real sale,
   commission, and net earnings.
10. Log in as **admin** (see below) to see the transaction under
    **Admin → Sales**.

To test the **exclusive** flow: upload a second track as **Exclusive**, buy
it once successfully, then confirm the track page now shows **SOLD** and the
Buy button is disabled — the backend also independently rejects any attempt
to purchase it again, even via a direct request.

### Creating an admin user

There's no public admin signup (by design). Promote an existing user via a
one-off script or DB console:

```bash
python3 -c "
from app.database import SessionLocal
from app.models.user import User, UserRole
db = SessionLocal()
u = db.query(User).filter(User.email == 'you@example.com').first()
u.role = UserRole.ADMIN
db.commit()
print('Promoted', u.email, 'to admin')
"
```

---

## 5. Production Deployment (e.g. Render)

**Build command:**
```bash
pip install -r requirements.txt
```

**Start command:**
```bash
alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT
```

**Required environment variables** (set these in your host's dashboard, not
in source control):

- `APP_ENV=production`
- `SECRET_KEY` — a long random string
- `DATABASE_URL` — your managed PostgreSQL connection string
- `BASE_URL` — your public app URL
- `MPESA_ENVIRONMENT=production`
- `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`,
  `MPESA_PASSKEY`, `MPESA_CALLBACK_URL`
- `PLATFORM_COMMISSION_PERCENT`
- `DISCORD_INVITE_URL` (optional)
- `EMAIL_*` variables if you want password-reset emails sent (otherwise the
  reset link is only logged server-side — functional but not automated)

**Static & media files:** by default, uploaded audio/artwork is stored on
local disk under `media/`. On most PaaS platforms (including Render's free
tier) local disk is **ephemeral** — files are lost on redeploy. For a real
production launch, either use Render's persistent disks or point
`app/services/storage.py` at S3-compatible storage (the module is a single
clean abstraction — swap `save_upload()`'s implementation without touching
any router code).

**M-Pesa callback URL:** must match your deployed domain exactly, e.g.
`https://beathub.onrender.com/mpesa/callback`, and must be set **before**
running any real purchase test.

---

## 6. What's Included

- Full auth: register, login, logout (fixed — returns a real redirect, never
  a 404), forgot/reset password
- Real DB-backed search across producers, artists, DJs, tracks, and albums
- Single and multi-track upload, album creation with track attachment
- **Exclusive licensing is enforced at the database level** via a unique
  constraint (`exclusive_ownership_locks.track_id`), not just hidden UI —
  verified under simulated race conditions (see `app/services/orders.py`)
- Real M-Pesa Daraja STK Push integration with idempotent callback handling
  — ownership is only ever granted after a confirmed payment callback
- Server-side, Decimal-based commission/revenue split — never trusts
  client-submitted prices
- Creator dashboard with real sales/commission/balance figures and a
  withdrawal request flow
- Admin dashboard: users, content, sales, platform revenue, withdrawal
  approval workflow
- Clean error handling — no raw tracebacks are ever shown to users

## 7. Known Follow-Ups

- File storage should move to S3/cloud storage before a real production
  launch (see note above)
- Producer payouts (M-Pesa B2C) are tracked through to a `PAID` status by an
  admin, but the actual automated B2C payout API call is not wired up —
  requires separate B2C credentials from Safaricom, which is deliberately
  scoped out until you have those.
- Email sending for password resets requires configuring `EMAIL_*` — until
  then, reset links are printed to the server log (safe, functional, just
  not automated).
