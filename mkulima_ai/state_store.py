import os, json, sqlite3, hashlib, hmac
from urllib import request as urlrequest, parse as urlparse, error as urlerror

DB=os.getenv("DB_PATH","/tmp/mkulima.db")

def _secret():
    return os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

def _url():
    return (os.getenv("SUPABASE_URL") or "").rstrip("/")

def supabase_enabled():
    return bool(_url() and _secret())

def actor_ref(phone):
    salt=(os.getenv("MKULIMA_ACTOR_SALT")
          or os.getenv("MKULIMA_CHECKOUT_SECRET")
          or _secret())
    if not salt:
        return None
    return hmac.new(salt.encode(), str(phone).encode(), hashlib.sha256).hexdigest()

def init_sqlite():
    con=sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS conversations(phone TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT '{}')")
    con.execute("CREATE TABLE IF NOT EXISTS processed_messages(message_id TEXT PRIMARY KEY,created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    con.commit(); con.close()

def _api(method,path,body=None,prefer=None):
    key=_secret()
    if not (_url() and key):
        raise RuntimeError("supabase_not_configured")
    data=None if body is None else json.dumps(body).encode()
    headers={
        "apikey":key,
        "Authorization":"Bearer "+key,
        "Content-Type":"application/json",
        "Accept":"application/json",
        "User-Agent":"MkulimaAI/1.0 (+https://mkulima-ai-whatsapp.onrender.com)"
    }
    if prefer: headers["Prefer"]=prefer
    req=urlrequest.Request(_url()+"/rest/v1/"+path,data=data,headers=headers,method=method)
    try:
        with urlrequest.urlopen(req,timeout=8) as res:
            raw=res.read()
            return json.loads(raw.decode()) if raw else None
    except urlerror.HTTPError as exc:
        try:
            body=exc.read().decode("utf-8","replace")[:500]
        except Exception:
            body=""
        print("SUPABASE_REST_ERROR method=%s path=%s status=%s body=%s" % (method,path.split("?")[0],getattr(exc,"code","?"),body), flush=True)
        raise

def load_state(phone):
    ref=actor_ref(phone)
    if supabase_enabled() and ref:
        try:
            rows=_api("GET","mkulima_conversations?actor_ref=eq."+urlparse.quote(ref)+"&select=state&limit=1") or []
            if rows:
                return rows[0].get("state") or {}
        except Exception:
            pass
    init_sqlite()
    con=sqlite3.connect(DB)
    row=con.execute("SELECT state FROM conversations WHERE phone=?",(phone,)).fetchone()
    con.close()
    try: return json.loads(row[0]) if row else {}
    except (TypeError,ValueError,json.JSONDecodeError): return {}

def save_state(phone,state):
    ref=actor_ref(phone)
    if supabase_enabled() and ref:
        try:
            _api("POST","mkulima_conversations?on_conflict=actor_ref",
                 {"actor_ref":ref,"state":state},
                 "resolution=merge-duplicates,return=minimal")
            return "supabase"
        except Exception:
            pass
    init_sqlite()
    con=sqlite3.connect(DB)
    con.execute("INSERT INTO conversations(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state",
                (phone,json.dumps(state)))
    con.commit(); con.close()
    return "sqlite"

def claim_message(message_id):
    """Return False for an already-seen message. Supabase is authoritative when configured."""
    if supabase_enabled():
        try:
            # message_jobs requires actor_ref and lease fields, so dedupe stays local until
            # the full lease worker is wired. Avoid pretending partial durable dedupe exists.
            pass
        except Exception:
            pass
    init_sqlite()
    con=sqlite3.connect(DB)
    try:
        con.execute("INSERT INTO processed_messages(message_id) VALUES(?)",(message_id,))
        con.commit(); return True
    except sqlite3.IntegrityError:
        return False
    finally:
        con.close()


def record_interaction(message_id, phone, question, recommendation, state, answer_version="mkulima-v1"):
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref and message_id):
        return None
    payload={
        "message_id":message_id,
        "actor_ref":ref,
        "question":str(question or "")[:8000],
        "recommendation":str(recommendation or "")[:8000],
        "crop":state.get("crop"),
        "location_context":state.get("location"),
        "language":state.get("language") or "unknown",
        "problem":state.get("primary_intent") or state.get("problem"),
        "confidence":state.get("confidence"),
        "answer_version":answer_version,
    }
    try:
        rows=_api("POST","mkulima_interactions",payload,"return=representation") or []
        return rows[0].get("id") if rows else None
    except Exception:
        return None

def record_feedback(message_id, interaction_id, rating, outcome=None):
    rating=str(rating or "").strip().lower()
    if rating not in {"helpful","wrong","still_problem"}:
        return False
    if not (supabase_enabled() and interaction_id and message_id):
        return False
    payload={"message_id":message_id,"interaction_id":interaction_id,"rating":rating}
    if outcome:
        payload["outcome"]=str(outcome)[:8000]
    try:
        _api("POST","mkulima_feedback",payload,"return=minimal")
        return True
    except Exception:
        return False


def record_revenue_event(phone, event_type, amount_kes=None, source=None, metadata=None):
    """Record a real business event only when Supabase is securely configured."""
    allowed={"lead_generated","buyer_match","premium_started","payment_success","cooperative_account"}
    event_type=str(event_type or "").strip().lower()
    ref=actor_ref(phone)
    if event_type not in allowed or not (supabase_enabled() and ref):
        return False
    payload={"actor_ref":ref,"event_type":event_type}
    if amount_kes is not None:
        try:
            amount=float(amount_kes)
            if amount < 0: return False
            payload["amount_kes"]=amount
        except (TypeError,ValueError):
            return False
    if source: payload["source"]=str(source)[:100]
    if metadata and isinstance(metadata,dict): payload["metadata"]=metadata
    try:
        _api("POST","mkulima_revenue_events",payload,"return=minimal")
        return True
    except Exception:
        return False


def get_access(phone):
    """Return current paid/free access without exposing the raw phone."""
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref):
        return {"plan":"free","active":False,"free_used":0,"free_limit":5}
    month=__import__("datetime").datetime.utcnow().strftime("%Y-%m")
    try:
        rows=_api("GET","mkulima_access?actor_ref=eq."+urlparse.quote(ref)+"&select=plan,access_until,free_month,free_used&limit=1") or []
        if not rows:
            _api("POST","mkulima_access",{"actor_ref":ref,"plan":"free","free_month":month,"free_used":0},"return=minimal")
            return {"plan":"free","active":False,"free_used":0,"free_limit":5}
        row=rows[0]
        if row.get("free_month")!=month:
            _api("PATCH","mkulima_access?actor_ref=eq."+urlparse.quote(ref),{"free_month":month,"free_used":0,"updated_at":__import__("datetime").datetime.utcnow().isoformat()+"Z"},"return=minimal")
            row["free_month"]=month; row["free_used"]=0
        active=False
        until=row.get("access_until")
        if until:
            try:
                active=__import__("datetime").datetime.fromisoformat(until.replace("Z","+00:00")) > __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            except Exception:
                active=False
        return {"plan":row.get("plan") or "free","active":active,"access_until":until,"free_used":int(row.get("free_used") or 0),"free_limit":5}
    except Exception:
        return {"plan":"free","active":False,"free_used":0,"free_limit":5}

def consume_free_question(phone):
    """Atomically-ish increment free usage after a useful answer is sent."""
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref):
        return False
    access=get_access(phone)
    if access.get("active"):
        return True
    if access.get("free_used",0) >= access.get("free_limit",5):
        return False
    try:
        new_used=access.get("free_used",0)+1
        _api("PATCH","mkulima_access?actor_ref=eq."+urlparse.quote(ref),
             {"free_used":new_used,"updated_at":__import__("datetime").datetime.utcnow().isoformat()+"Z"},
             "return=minimal")
        return True
    except Exception:
        return False

def create_payment(phone, reference, plan, amount_kes, email):
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref):
        return False
    payload={"actor_ref":ref,"reference":reference,"plan":plan,"amount_kes":int(amount_kes),"email":email,"status":"initialized","provider":"paystack"}
    try:
        _api("POST","mkulima_payments",payload,"return=minimal")
        return True
    except Exception:
        return False

def get_payment(reference):
    if not (supabase_enabled() and reference):
        return None
    try:
        rows=_api("GET","mkulima_payments?reference=eq."+urlparse.quote(reference)+"&select=*&limit=1") or []
        return rows[0] if rows else None
    except Exception:
        return None

def mark_payment_paid(reference, provider_payload=None):
    p=get_payment(reference)
    if not p:
        return False
    plan=p.get("plan")
    seconds=86400 if plan=="day_pass" else 30*86400
    now=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    until=now+__import__("datetime").timedelta(seconds=seconds)
    try:
        _api("PATCH","mkulima_payments?reference=eq."+urlparse.quote(reference),
             {"status":"paid","paid_at":now.isoformat(),"provider_payload":provider_payload or {}},
             "return=minimal")
        _api("POST","mkulima_access?on_conflict=actor_ref",
             {"actor_ref":p["actor_ref"],"plan":plan,"access_until":until.isoformat(),"updated_at":now.isoformat()},
             "resolution=merge-duplicates,return=minimal")
        return True
    except Exception:
        return False


def create_payment_for_actor(actor, reference, plan, amount_kes, email):
    if not (supabase_enabled() and actor):
        return False
    payload={"actor_ref":actor,"reference":reference,"plan":plan,"amount_kes":int(amount_kes),"email":email,"status":"initialized","provider":"paystack"}
    try:
        _api("POST","mkulima_payments",payload,"return=minimal")
        return True
    except Exception:
        return False
