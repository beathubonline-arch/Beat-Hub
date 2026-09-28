import os, json, base64, urllib.request, urllib.error

def _http_json(url, data=None, headers=None, method=None, timeout=45):
    req=urllib.request.Request(url,data=data,headers=headers or {},method=method)
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def download_whatsapp_media(media_id, max_bytes=8*1024*1024):
    """Fetch WhatsApp media into memory. Never writes farmer photos to disk."""
    token=os.getenv("WHATSAPP_TOKEN")
    if not token or not media_id:
        return {"ok":False,"status":"unavailable","reason":"missing_whatsapp_media_access"}
    try:
        meta=_http_json(
            f"https://graph.facebook.com/v23.0/{media_id}",
            headers={"Authorization":f"Bearer {token}"}
        )
        url=meta.get("url")
        if not url: return {"ok":False,"status":"error","reason":"media_url_missing"}
        req=urllib.request.Request(url,headers={"Authorization":f"Bearer {token}"})
        with urllib.request.urlopen(req,timeout=45) as r:
            raw=r.read(max_bytes+1)
            mime=r.headers.get_content_type()
        if len(raw)>max_bytes:
            return {"ok":False,"status":"rejected","reason":"image_too_large"}
        if not (mime or "").startswith("image/"):
            return {"ok":False,"status":"rejected","reason":"not_an_image"}
        return {"ok":True,"status":"downloaded","bytes":raw,"mime_type":mime,"size":len(raw)}
    except Exception as exc:
        return {"ok":False,"status":"error","reason":type(exc).__name__}

def analyze_farm_image(image_bytes, mime_type, context=None):
    """Provider-neutral farm vision adapter.

    Gemini is optional: no key means no inference, never a fabricated observation.
    Output is observations/hypotheses only; caller must combine with farmer context.
    """
    key=os.getenv("GEMINI_API_KEY")
    if not key:
        return {"ok":False,"status":"provider_unavailable","provider":None,
                "reason":"vision_provider_not_configured","observations":[]}
    prompt=(
      "You are the visual observation component of Mkulima AI for Kenyan farmers. "
      "Describe only agricultural details visibly supported by the image. "
      "Do not claim a definitive disease, pest, nutrient deficiency, pesticide need, "
      "animal diagnosis, or food safety conclusion from appearance alone. "
      "Return strict JSON with keys: image_type, visible_observations (array), "
      "possible_explanations (array), confidence (low|medium|high), "
      "questions_needed (array), urgent_visual_flags (array). "
      "If text on a product label is unclear, say so rather than inventing it. "
      "Farmer context: "+json.dumps(context or {},ensure_ascii=False)[:2500]
    )
    payload={
      "contents":[{"parts":[
        {"text":prompt},
        {"inline_data":{"mime_type":mime_type,"data":base64.b64encode(image_bytes).decode("ascii")}}
      ]}],
      "generationConfig":{"temperature":0.1,"responseMimeType":"application/json"}
    }
    model=os.getenv("MKULIMA_VISION_MODEL","gemini-2.5-flash")
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    try:
        out=_http_json(url,json.dumps(payload).encode("utf-8"),{"Content-Type":"application/json"},"POST",60)
        text=out["candidates"][0]["content"]["parts"][0]["text"]
        parsed=json.loads(text)
        return {"ok":True,"status":"observed","provider":"google_gemini","model":model,
                "observations":parsed}
    except Exception as exc:
        return {"ok":False,"status":"provider_error","provider":"google_gemini",
                "reason":type(exc).__name__,"observations":[]}

def safe_vision_reply(result, lang="sw"):
    if not result.get("ok"):
        return None
    x=result.get("observations") or {}
    obs=(x.get("visible_observations") or [])[:4]
    poss=(x.get("possible_explanations") or [])[:3]
    qs=(x.get("questions_needed") or [])[:3]
    flags=(x.get("urgent_visual_flags") or [])[:2]
    if lang=="en":
        parts=["📷 From the photo I can observe: "+("; ".join(obs) if obs else "no reliable specific symptom.")]
        if poss: parts.append("Possible explanations to check — not confirmed diagnoses: "+"; ".join(poss)+".")
        if flags: parts.append("⚠️ Visual flags needing prompt attention: "+"; ".join(flags)+".")
        if qs: parts.append("To narrow it safely: "+"; ".join(qs))
        parts.append("Confidence from the image alone: "+str(x.get("confidence","low"))+".")
    else:
        parts=["📷 Kwa picha naona: "+("; ".join(obs) if obs else "hakuna symptom specific ninayoweza kuthibitisha vizuri.")]
        if poss: parts.append("Possible causes za kuchunguza—si diagnosis confirmed: "+"; ".join(poss)+".")
        if flags: parts.append("⚠️ Kuna visual flags za kuangaliwa haraka: "+"; ".join(flags)+".")
        if qs: parts.append("Ili nichambue vizuri zaidi: "+"; ".join(qs))
        parts.append("Confidence ya picha pekee: "+str(x.get("confidence","low"))+".")
    return "\n\n".join(parts)
