import os, json, sqlite3, hashlib, hmac
from urllib import request as urlrequest, parse as urlparse

DB=os.getenv("DB_PATH","/tmp/mkulima.db")

def _secret():
    return os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY")

def _url():
    return (os.getenv("SUPABASE_URL") or "").rstrip("/")

def supabase_enabled():
    return bool(_url() and _secret())

def actor_ref(phone):
    salt=os.getenv("MKULIMA_ACTOR_SALT") or _secret()
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
    headers={"apikey":key,"Authorization":"Bearer "+key,"Content-Type":"application/json"}
    if prefer: headers["Prefer"]=prefer
    req=urlrequest.Request(_url()+"/rest/v1/"+path,data=data,headers=headers,method=method)
    with urlrequest.urlopen(req,timeout=8) as res:
        raw=res.read()
        return json.loads(raw.decode()) if raw else None

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
