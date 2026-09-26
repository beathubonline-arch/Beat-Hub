import os,json,urllib.request
def send_whatsapp_text(to,body):
    token=os.getenv("WHATSAPP_TOKEN"); phone_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    if not token or not phone_id:
        app_mode={"mode":"dry-run","to":to,"body":body}
        print("WHATSAPP_DRY_RUN",json.dumps(app_mode,ensure_ascii=False),flush=True)
        return app_mode
    url=f"https://graph.facebook.com/v23.0/{phone_id}/messages"
    data=json.dumps({"messaging_product":"whatsapp","to":to,"type":"text","text":{"body":body}}).encode()
    req=urllib.request.Request(url,data=data,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as r:
        return json.loads(r.read())
