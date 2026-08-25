# BeatHub

A music marketplace where producers, artists and DJs sell beats and tracks,
with Paystack checkout for Kenya M-PESA and cards, exclusive vs
non-exclusive licensing, automatic platform commission splitting, creator
stores and an admin dashboard.

Built with FastAPI + SQLAlchemy + Jinja2, PostgreSQL in production.

---

## 1. Local Setup

```bash
cd beathub
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Set `SECRET_KEY` and `DATABASE_URL` in `.env`.
For quick local development you can use:

```env
DATABASE_URL=sqlite:///./beathub.db
```

Start the application:

```bash
uvicorn main:app --reload
```

For PostgreSQL, run `alembic upgrade head` manually when desired; production
startup also runs the migrations automatically before accepting requests.

---

## 2. PostgreSQL Configuration

```env
DATABASE_URL=postgresql://username:password@host:5432/beathub
```

Production startup runs:

```bash
alembic upgrade head
```

The payment schema migrations are forward-only and reconcile the historical
`payment_transactions` columns, including `completed_at`, `callback_processed`
and VARCHAR-backed payment status values.

---

## 3. Paystack Configuration

BeatHub uses Paystack as the customer payment gateway. The checkout is
server-side verified before ownership is granted.

Paystack is configured with:

```env
PAYSTACK_SECRET_KEY=your-secret-key
PAYSTACK_PUBLIC_KEY=your-public-key
PAYSTACK_BASE_URL=https://api.paystack.co
BASE_URL=https://your-deployed-domain.com
```

Keep the secret key server-side only. Use Paystack test keys during testing;
switch to the appropriate live key only after the Paystack account and payment
channels are approved for live use.

The BeatHub payment flow is:

1. Buyer opens `/checkout/track/{slug}`.
2. BeatHub creates a pending order and initializes Paystack.
3. Paystack handles the available payment channel, including Kenya M-PESA or
   card where enabled on the Paystack account.
4. Paystack redirects the customer to BeatHub's `/paystack/callback`.
5. BeatHub independently verifies the reference against Paystack's API.
6. Paystack's signed `/paystack/webhook` is also verified independently.
7. Callback/webhook processing is idempotent and row-locked so duplicate
   delivery cannot grant ownership twice.
8. Only a verified successful payment is passed to `finalize_order()`.
9. The order becomes `COMPLETED`, a license is created, and creator earnings
   are recorded.

---

## 4. First Purchase Test

1. Deploy the app with Paystack test credentials.
2. Create a Producer / Artist / DJ account.
3. Upload a published non-exclusive track priced at **KSh 3.00 or more**.
4. Create a separate buyer account.
5. Open the track and choose **Buy Now**.
6. Enter a valid buyer email.
7. Complete the Paystack checkout using an enabled test payment channel.
8. Wait for the order-status page to show **COMPLETED**.
9. Confirm the purchased track is available from the buyer's account.
10. Confirm the producer dashboard shows the sale, commission and net earnings.

For an exclusive track, verify that the first successful purchase marks the
track sold and a second buyer cannot obtain the same exclusive ownership.

### Creating an admin user

There's no public admin signup. Promote an existing user through a one-off
script or DB console:

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
uvicorn main:app --host 0.0.0.0 --port $PORT
```

The application startup hook automatically runs `alembic upgrade head` for
PostgreSQL before the service accepts requests. This prevents the Paystack
checkout code from running against an old payment schema.

**Required environment variables:**

- `APP_ENV=production`
- `SECRET_KEY` — a long random string
- `DATABASE_URL` — your managed PostgreSQL connection string
- `BASE_URL` — your public app URL
- `PAYSTACK_SECRET_KEY`
- `PAYSTACK_PUBLIC_KEY` (if needed by future client-side Paystack features)
- `PAYSTACK_BASE_URL=https://api.paystack.co`
- `PLATFORM_COMMISSION_PERCENT`
- `DISCORD_INVITE_URL` (optional)
- `EMAIL_*` variables if password-reset emails should be automated

Do not commit live secrets to GitHub.

---

## 6. Media Storage

BeatHub supports S3-compatible storage through `app/services/storage.py`.
For production, use persistent/object storage rather than relying on an
ephemeral application filesystem.

Relevant settings include:

```env
MEDIA_STORAGE=r2
R2_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET_NAME=beathub
R2_PUBLIC_URL=...
R2_DOWNLOAD_URL_EXPIRES=900
```

---

## 7. What's Included

- Full authentication: register, login, logout, password reset
- Producer / artist / DJ dashboards and public creator stores
- Track and beat uploads with artwork and audio previews
- Exclusive and non-exclusive licensing
- Database-level exclusive ownership protection
- Paystack checkout for enabled Kenya M-PESA and card channels
- Server-side Paystack transaction verification
- Signed Paystack webhook verification
- Idempotent payment completion with database row locking
- Ownership granted only after verified payment
- Decimal-based commission and creator revenue splitting
- Creator sales, earnings and withdrawal workflow
- Admin dashboard and sales visibility
- S3-compatible media storage support
- Production PostgreSQL migration safeguards

---

## 8. Production Payment Readiness

Before accepting real customer payments, verify the Paystack account is
approved for live transactions and the required Kenya payment channels are
enabled, set the live Paystack secret key in the hosting environment, confirm
`BASE_URL` is the real HTTPS BeatHub URL, and complete one controlled live
purchase with a low-priced non-exclusive track.

Daraja/Safaricom direct checkout is intentionally not part of the current
customer payment flow. Paystack is the single BeatHub checkout gateway.
