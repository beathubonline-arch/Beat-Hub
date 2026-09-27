from flask import Flask, jsonify, request
import os, time, uuid

app = Flask(__name__)

STATE = {
    "service": "beathub-faceless-orchestrator",
    "version": "10.0",
    "approval_required": True,
    "instagram_connected": False,
    "engine_mode": "multi_channel",
    "upgrade_policy": "candidate_then_regression_x2_then_promote",
}

CHANNELS = {
    "beathub": {
        "niche": "music creators and beat marketplace",
        "formats": ["short", "long_form"],
        "approval_required": True,
    }
}

PIPELINE = [
    "research", "analytics", "experiment", "script_or_prompt", "generation_queue",
    "render", "technical_qc", "audio", "packaging", "creative_qc",
    "approval", "schedule", "publish", "analytics_ingest", "learning"
]

def channel_config(channel_id):
    return CHANNELS.get(channel_id)

@app.get("/health")
def health():
    return jsonify({**STATE, "status": "ok", "channels": list(CHANNELS)})

@app.get("/pipeline")
def pipeline():
    return jsonify({
        "stages": PIPELINE,
        "publication_gate": "human_approval",
        "formats": ["short", "long_form"],
        "upgrade_policy": STATE["upgrade_policy"],
    })

@app.get("/channels")
def channels():
    return jsonify(CHANNELS)

@app.post("/jobs")
def jobs():
    data = request.get_json(silent=True) or {}
    channel_id = data.get("channel_id", "beathub")
    fmt = data.get("format", "short")
    if not channel_config(channel_id):
        return jsonify({"status":"blocked","reason":"unknown_channel"}), 400
    if fmt not in ("short", "long_form"):
        return jsonify({"status":"blocked","reason":"unsupported_format"}), 400
    return jsonify({
        "id": uuid.uuid4().hex[:12],
        "channel_id": channel_id,
        "format": fmt,
        "status": "generation_queued",
        "brief": data.get("brief", {}),
        "created_at": time.time(),
    }), 202

@app.post("/publish")
def publish():
    data = request.get_json(silent=True) or {}
    if data.get("approved") is not True:
        return jsonify({"status":"blocked","reason":"human_approval_required"}), 403
    if not data.get("media_url"):
        return jsonify({"status":"blocked","reason":"production_media_url_required"}), 400
    if data.get("audio_qc_passed") is not True:
        return jsonify({"status":"blocked","reason":"audio_qc_required"}), 400
    return jsonify({
        "status":"ready_for_metricool_schedule",
        "channel_id": data.get("channel_id","beathub"),
        "idempotency_key": data.get("idempotency_key") or uuid.uuid4().hex,
    }), 200

@app.post("/upgrade/candidate")
def upgrade_candidate():
    data = request.get_json(silent=True) or {}
    return jsonify({
        "candidate_id": uuid.uuid4().hex[:12],
        "component": data.get("component"),
        "candidate": data.get("candidate"),
        "status": "awaiting_regression",
        "required_passes": 2,
        "auto_promote": False,
        "rule": "never replace production on discovery alone",
    }), 202
