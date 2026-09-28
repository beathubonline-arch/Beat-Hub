import os,json,urllib.request,urllib.error

def send_whatsapp_text(to,body):
    token=os.getenv("WHATSAPP_TOKEN"); phone_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        app_mode={"mode":"dry-run","to":to,"body":body}
        print("WHATSAPP_DRY_RUN: delivery disabled",flush=True)
        return app_mode
    url=f"https://graph.facebook.com/v23.0/{phone_id}/messages"
    data=json.dumps({"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":body}}).encode()
    req=urllib.request.Request(url,data=data,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as r:
            result=json.loads(r.read())
            print("WHATSAPP_SEND_OK",json.dumps({"message_id":(result.get("messages") or [{}])[0].get("id")}),flush=True)
            return result
    except urllib.error.HTTPError as exc:
        raw=exc.read().decode("utf-8","replace")
        try:
            payload=json.loads(raw)
            err=payload.get("error",{})
            safe={"status":exc.code,"type":err.get("type"),"code":err.get("code"),"error_subcode":err.get("error_subcode"),"fbtrace_id":err.get("fbtrace_id")}
        except Exception:
            safe={"status":exc.code,"message":"Upstream response could not be parsed"}
        print("WHATSAPP_SEND_ERROR",json.dumps(safe,ensure_ascii=False),flush=True)
        raise
