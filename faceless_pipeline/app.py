from flask import Flask, jsonify, request
import os, time, uuid
app=Flask(__name__)
STATE={"service":"beathub-faceless-orchestrator","version":"7.0","approval_required":True,"instagram_connected":False}
@app.get("/health")
def health(): return jsonify({**STATE,"status":"ok"})
@app.get("/pipeline")
def pipeline(): return jsonify({"stages":["analytics","experiment","generation_queue","render","qc","packaging","creative","approval","schedule","publish","analytics"],"publication_gate":"human_approval"})
@app.post("/jobs")
def jobs():
    data=request.get_json(silent=True) or {}
    return jsonify({"id":uuid.uuid4().hex[:12],"status":"generation_queued","brief":data,"created_at":time.time()}),202
@app.post("/publish")
def publish():
    data=request.get_json(silent=True) or {}
    if data.get("approved") is not True:
        return jsonify({"status":"blocked","reason":"human_approval_required"}),403
    return jsonify({"status":"ready_for_metricool_schedule","note":"scheduling adapter requires approved production media URL"}),200
