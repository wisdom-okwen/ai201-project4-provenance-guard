from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
import scoring
import signals
from config import (
    APPEAL_RATE_LIMITS,
    LOG_RATE_LIMITS,
    MIN_CONTENT_LENGTH,
    SUBMIT_RATE_LIMITS,
)

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

db.init_db()


@app.errorhandler(429)
def ratelimit_handler(_e):
    return jsonify({"error": "rate limit exceeded, please try again later"}), 429


@app.route("/submit", methods=["POST"])
@limiter.limit(SUBMIT_RATE_LIMITS)
def submit():
    data = request.get_json(silent=True) or {}
    content = data.get("content")
    creator_id = data.get("creator_id")

    if not content:
        return jsonify({"error": "content is required"}), 400
    if not creator_id:
        return jsonify({"error": "creator_id is required"}), 400
    if len(content) < MIN_CONTENT_LENGTH:
        return jsonify({
            "error": f"content must be at least {MIN_CONTENT_LENGTH} characters for reliable analysis"
        }), 400

    try:
        llm_score = signals.get_llm_score(content)
    except Exception:
        return jsonify({"error": "classification service unavailable, please try again"}), 503

    stylo_score = signals.get_stylometric_score(content)
    confidence = scoring.combine(llm_score, stylo_score)
    attribution, label = scoring.classify(confidence)

    content_id = db.insert_submission(
        creator_id, content, llm_score, stylo_score, confidence, attribution, label
    )
    db.insert_audit_log(
        content_id, creator_id, attribution, confidence, llm_score, stylo_score, status="classified"
    )

    return jsonify({
        "content_id": content_id,
        "creator_id": creator_id,
        "attribution": attribution,
        "confidence": round(confidence, 4),
        "llm_score": round(llm_score, 4),
        "stylometric_score": round(stylo_score, 4),
        "label": label,
        "status": "classified",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 201


@app.route("/log", methods=["GET"])
@limiter.limit(LOG_RATE_LIMITS)
def get_log():
    creator_id = request.args.get("creator_id")
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    offset = int(request.args.get("offset", 0))

    entries = db.get_audit_log(creator_id=creator_id, status=status, limit=limit, offset=offset)
    return jsonify({"count": len(entries), "entries": entries}), 200


@app.route("/appeal", methods=["POST"])
@limiter.limit(APPEAL_RATE_LIMITS)
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    reasoning = data.get("reasoning")

    if not content_id:
        return jsonify({"error": "content_id is required"}), 400
    if not reasoning:
        return jsonify({"error": "reasoning is required"}), 400

    submission = db.get_submission(content_id)
    if not submission:
        return jsonify({"error": "submission not found"}), 404
    if submission["status"] == "under_review":
        return jsonify({"error": "this submission already has a pending appeal"}), 409

    db.insert_appeal(content_id, reasoning)
    db.update_submission_status(content_id, "under_review")
    db.insert_audit_log(
        content_id,
        submission["creator_id"],
        submission["attribution"],
        submission["confidence"],
        submission["llm_score"],
        submission["stylo_score"],
        status="under_review",
        appeal_reasoning=reasoning,
    )

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Your appeal has been recorded and this submission is now under review.",
    }), 201


if __name__ == "__main__":
    app.run(debug=True, port=5000)
