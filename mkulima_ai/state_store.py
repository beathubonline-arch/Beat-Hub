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
        print("MKULIMA_PAYMENT_STORE_NOT_READY supabase_url=%s supabase_secret=%s actor=%s" % (
            bool(_url()), bool(_secret()), bool(actor)
        ), flush=True)
        return False
    payload={"actor_ref":actor,"reference":reference,"plan":plan,"amount_kes":int(amount_kes),"email":email,"status":"initialized","provider":"paystack"}
    try:
        _api("POST","mkulima_payments",payload,"return=minimal")
        return True
    except Exception as exc:
        print("MKULIMA_PAYMENT_STORE_ERROR type=%s message=%s" % (type(exc).__name__, str(exc)[:300]), flush=True)
        return False


# --- Mkulima Market: buyers, seller listings, and offers ---
def _token_hash(token):
    return hashlib.sha256(str(token).encode()).hexdigest()

def register_buyer(profile):
    """Create a buyer profile. Returns the one-time raw access token plus safe profile."""
    if not supabase_enabled():
        return None
    token=__import__("secrets").token_urlsafe(24)
    products=[str(x).strip().lower()[:80] for x in (profile.get("products") or []) if str(x).strip()]
    payload={
        "buyer_token_hash":_token_hash(token),
        "business_name":str(profile.get("business_name") or "").strip()[:160],
        "contact_name":str(profile.get("contact_name") or "").strip()[:160],
        "phone":str(profile.get("phone") or "").strip()[:60],
        "email":(str(profile.get("email") or "").strip()[:320] or None),
        "county":(str(profile.get("county") or "").strip()[:120] or None),
        "locality":(str(profile.get("locality") or "").strip()[:160] or None),
        "products":products,
        "min_quantity":profile.get("min_quantity"),
        "max_quantity":profile.get("max_quantity"),
        "preferred_unit":(str(profile.get("preferred_unit") or "").strip()[:40] or None),
        "pickup":bool(profile.get("pickup")),
        "payment_terms":(str(profile.get("payment_terms") or "").strip()[:300] or None),
    }
    if not (payload["business_name"] and payload["contact_name"] and payload["phone"] and products):
        return None
    try:
        rows=_api("POST","mkulima_buyers",payload,"return=representation") or []
        if not rows: return None
        row=rows[0]
        return {"token":token,"buyer":{"id":row.get("id"),"business_name":row.get("business_name"),"verified":row.get("verified"),"reputation_score":row.get("reputation_score")}}
    except Exception:
        return None

def get_buyer_by_token(token):
    if not (supabase_enabled() and token):
        return None
    try:
        rows=_api("GET","mkulima_buyers?buyer_token_hash=eq."+urlparse.quote(_token_hash(token))+"&active=eq.true&select=*&limit=1") or []
        return rows[0] if rows else None
    except Exception:
        return None

def list_marketplace_listings(product=None, location=None, limit=50):
    if not supabase_enabled():
        return []
    limit=max(1,min(int(limit or 50),100))
    path="mkulima_seller_listings?status=in.(open,matched)&expires_at=gt."+urlparse.quote(__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat())+"&select=id,product,quantity,unit,location_text,county,target_price,urgency,status,created_at&order=created_at.desc&limit="+str(limit)
    if product:
        path+="&product=eq."+urlparse.quote(str(product).strip().lower())
    try:
        rows=_api("GET",path) or []
        if location:
            needle=str(location).strip().lower()
            rows=[r for r in rows if needle in str(r.get("location_text") or "").lower() or needle in str(r.get("county") or "").lower()]
        return rows
    except Exception:
        return []

def create_seller_listing(phone, state):
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref):
        return None
    product=str(state.get("product") or state.get("crop") or "").strip().lower()
    quantity=state.get("quantity",state.get("bags"))
    unit=str(state.get("quantity_unit") or ("bags" if state.get("bags") is not None else "")).strip().lower()
    location=str(state.get("location") or "").strip()
    if not (product and quantity and unit and location):
        return None
    # Reuse a current open listing for the same actor/product instead of spamming duplicates.
    try:
        existing=_api("GET","mkulima_seller_listings?actor_ref=eq."+urlparse.quote(ref)+"&product=eq."+urlparse.quote(product)+"&status=in.(open,matched)&select=id&order=created_at.desc&limit=1") or []
        if existing:
            return existing[0].get("id")
    except Exception:
        pass
    urgency=state.get("sale_timing") or "this_week"
    if urgency not in {"today","few_days","this_week","best_price"}: urgency="this_week"
    payload={
        "actor_ref":ref,"product":product,"quantity":float(quantity),"unit":unit,
        "location_text":location,"county":state.get("county"),"target_price":state.get("target_price"),
        "urgency":urgency,"status":"open","source":"whatsapp"
    }
    try:
        rows=_api("POST","mkulima_seller_listings",payload,"return=representation") or []
        return rows[0].get("id") if rows else None
    except Exception:
        return None

def matching_buyers_for_listing(listing_id, limit=10):
    if not (supabase_enabled() and listing_id):
        return []
    try:
        listings=_api("GET","mkulima_seller_listings?id=eq."+urlparse.quote(str(listing_id))+"&select=product,quantity,unit,location_text,county&limit=1") or []
        if not listings: return []
        listing=listings[0]
        buyers=_api("GET","mkulima_buyers?active=eq.true&select=id,business_name,county,locality,products,min_quantity,max_quantity,preferred_unit,pickup,verified,reputation_score,deals_completed&limit=100") or []
        scored=[]
        for b in buyers:
            if listing.get("product") not in (b.get("products") or []):
                continue
            q=float(listing.get("quantity") or 0)
            if b.get("min_quantity") is not None and q<float(b["min_quantity"]): continue
            if b.get("max_quantity") is not None and q>float(b["max_quantity"]): continue
            score=0
            if b.get("verified"): score+=30
            score+=min(float(b.get("reputation_score") or 0),100)*0.25
            if b.get("pickup"): score+=10
            loc=(str(listing.get("location_text") or "")+" "+str(listing.get("county") or "")).lower()
            bloc=(str(b.get("locality") or "")+" "+str(b.get("county") or "")).lower()
            if bloc and any(part and part in loc for part in re.split(r"[, ]+",bloc)): score+=20
            if b.get("preferred_unit") and b.get("preferred_unit")==listing.get("unit"): score+=5
            scored.append((score,b))
        scored.sort(key=lambda x:x[0],reverse=True)
        return [{"match_score":round(s,1),**b} for s,b in scored[:max(1,min(int(limit or 10),20))]]
    except Exception:
        return []

def submit_buyer_offer(token, listing_id, price_per_unit, pickup=False, payment_terms=None, note=None):
    buyer=get_buyer_by_token(token)
    if not buyer:
        return {"ok":False,"error":"invalid_buyer"}
    try:
        listings=_api("GET","mkulima_seller_listings?id=eq."+urlparse.quote(str(listing_id))+"&status=in.(open,matched)&select=*&limit=1") or []
        if not listings: return {"ok":False,"error":"listing_not_available"}
        listing=listings[0]
        price=float(price_per_unit)
        if price<=0: return {"ok":False,"error":"invalid_price"}
        total=price*float(listing.get("quantity") or 0)
        payload={
            "listing_id":listing_id,"buyer_id":buyer["id"],"price_per_unit":price,
            "total_amount":total,"pickup":bool(pickup),
            "payment_terms":(str(payment_terms or "").strip()[:300] or None),
            "note":(str(note or "").strip()[:1000] or None),
            "status":"submitted"
        }
        path="mkulima_buyer_offers?on_conflict=listing_id,buyer_id"
        rows=_api("POST",path,payload,"resolution=merge-duplicates,return=representation") or []
        _api("PATCH","mkulima_seller_listings?id=eq."+urlparse.quote(str(listing_id)),{"status":"matched","updated_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},"return=minimal")
        return {"ok":True,"offer":rows[0] if rows else payload}
    except (TypeError,ValueError):
        return {"ok":False,"error":"invalid_price"}
    except Exception:
        return {"ok":False,"error":"offer_failed"}

def seller_offers(phone, limit=10):
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref):
        return []
    try:
        listings=_api("GET","mkulima_seller_listings?actor_ref=eq."+urlparse.quote(ref)+"&status=in.(open,matched)&select=id,product,quantity,unit,location_text&order=created_at.desc&limit=10") or []
        if not listings: return []
        by_id={str(x["id"]):x for x in listings}
        ids=",".join(by_id.keys())
        offers=_api("GET","mkulima_buyer_offers?listing_id=in.("+ids+")&status=in.(submitted,shortlisted)&select=id,listing_id,price_per_unit,total_amount,pickup,payment_terms,note,created_at,buyer_id&order=created_at.desc&limit="+str(max(1,min(int(limit or 10),25)))) or []
        out=[]
        for o in offers:
            b=_api("GET","mkulima_buyers?id=eq."+urlparse.quote(str(o.get("buyer_id")))+"&select=business_name,county,locality,verified,reputation_score&limit=1") or []
            buyer=b[0] if b else {}
            listing=by_id.get(str(o.get("listing_id")),{})
            out.append({**o,"listing":listing,"buyer":buyer,"offer_code":str(o.get("id") or "")[:8].upper()})
        return out
    except Exception:
        return []

def accept_seller_offer(phone, offer_code):
    ref=actor_ref(phone)
    if not (supabase_enabled() and ref and offer_code):
        return {"ok":False,"error":"invalid_request"}
    try:
        listings=_api("GET","mkulima_seller_listings?actor_ref=eq."+urlparse.quote(ref)+"&status=in.(open,matched)&select=id&limit=50") or []
        allowed={str(x["id"]) for x in listings}
        offers=_api("GET","mkulima_buyer_offers?status=in.(submitted,shortlisted)&select=*&limit=100") or []
        matches=[o for o in offers if str(o.get("listing_id")) in allowed and str(o.get("id") or "").upper().startswith(str(offer_code).strip().upper())]
        if len(matches)!=1:
            return {"ok":False,"error":"offer_not_found"}
        offer=matches[0]
        _api("PATCH","mkulima_buyer_offers?id=eq."+urlparse.quote(str(offer["id"])),{"status":"accepted","updated_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},"return=minimal")
        _api("PATCH","mkulima_seller_listings?id=eq."+urlparse.quote(str(offer["listing_id"])),{"status":"matched","updated_at":__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()},"return=minimal")
        buyers=_api("GET","mkulima_buyers?id=eq."+urlparse.quote(str(offer["buyer_id"]))+"&select=business_name,contact_name,phone,email,county,locality,verified,reputation_score,payment_terms&limit=1") or []
        return {"ok":True,"offer":offer,"buyer":buyers[0] if buyers else {}}
    except Exception:
        return {"ok":False,"error":"accept_failed"}
