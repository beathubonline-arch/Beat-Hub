import os,re,hashlib,hmac,requests
from flask import Flask,request,jsonify
from flask_cors import CORS
from psycopg_pool import ConnectionPool
app=Flask(__name__)
ORIGIN=os.getenv("STORE_ORIGIN","https://before-us-stories-store.onrender.com").rstrip("/")
CORS(app,resources={r"/api/*":{"origins":[ORIGIN]}},supports_credentials=False)
DATABASE_URL=os.getenv("DATABASE_URL","")
pool=ConnectionPool(DATABASE_URL,min_size=0,max_size=3,open=False) if DATABASE_URL else None
def db():
    if not pool: raise RuntimeError("Database not configured")
    if pool.closed:pool.open()
    return pool.connection()
def initialize():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS before_us_subscribers(
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            email text UNIQUE NOT NULL,
            source text NOT NULL DEFAULT 'website',
            consent_at timestamptz NOT NULL DEFAULT now(),
            unsubscribed_at timestamptz
        )""")
@app.get("/health")
def health():
    return jsonify(service="before-us-api",database_configured=bool(DATABASE_URL),payments_enabled=False)
@app.post("/api/subscribe")
def subscribe():
    if not DATABASE_URL:return jsonify(error="Subscriber signup is not yet available."),503
    data=request.get_json(silent=True) or {}
    email=str(data.get("email","")).strip().lower()
    if data.get("website"):return jsonify(ok=True)
    if not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,200}\.[A-Za-z]{2,30}",email) or len(email)>254:
        return jsonify(error="Please enter a valid email address."),400
    if data.get("consent") is not True:return jsonify(error="Please confirm your email subscription."),400
    try:
        initialize()
        with db() as conn:
            conn.execute("""INSERT INTO before_us_subscribers(email,source) VALUES (%s,'storefront')
            ON CONFLICT(email) DO UPDATE SET unsubscribed_at=NULL,consent_at=now()""",(email,))
        return jsonify(ok=True,message="Subscribed. You can unsubscribe at any time.")
    except Exception:
        app.logger.exception("subscribe failure")
        return jsonify(error="Signup is temporarily unavailable."),503
@app.post("/api/checkout")
def checkout():
    return jsonify(error="Checkout is not available until products, fulfillment, and payment verification are ready."),503
@app.post("/api/paystack/webhook")
def webhook():
    return jsonify(error="Payment integration not yet configured"),503
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT","10000")))
