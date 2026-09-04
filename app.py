"""AI Finance Controller — Flask Web Application.

Wraps the existing reconciliation engine as a web service with:
- User authentication (JWT)
- Razorpay Test Mode API integration
- Multi-bank CSV upload and parsing
- Full reconciliation pipeline
- AI Copilot for natural-language queries
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent))

load_dotenv()

from afc import pipeline
from afc.config import STATES, AUTO_THRESHOLD, REVIEW_THRESHOLD, HONESTY_CLAUSE
from afc.core.confidence import AUTO_RECONCILE
from afc.core.decompose import DECOMPOSITION_FAILED
from afc.core.faults import DEV_ONLY_PAIRS
from afc.generate import emit, plan
from afc.generate.generator import generate
from afc.ingest.loader import load
from afc.money import format_rupees
from afc.metrics import attribution, confusion
from afc.llm.narrate import NullNarrator

from server.auth import create_token, login_required
from server.models import (
    init_db, create_user, verify_user,
    save_razorpay_connection, get_razorpay_connection,
    save_bank_upload, get_bank_uploads, get_bank_upload_data,
    save_reconciliation_run, get_latest_reconciliation,
)
from server.razorpay_client import RazorpayClient, normalize_razorpay_data
from server.bank_parser import parse_bank_csv
from server.live_loader import build_normalized_batch
from server.copilot import ask_copilot

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "afc-dev-secret")
app.config["FLASK_SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "afc-dev-flask")
CORS(app)

# Initialize database on startup
init_db()

DEV_ONLY_CLASSES = {"+".join(sorted(p)) for p in DEV_ONLY_PAIRS}


# ================================================================ Static files
@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


# ================================================================ Auth routes
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required"}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400

    user = create_user(email, password)
    if not user:
        return jsonify({"error": "Email already registered"}), 409

    token = create_token(user["id"], user["email"], app.config["JWT_SECRET_KEY"])
    return jsonify({"token": token, "user": user}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    user = verify_user(email, password)
    if not user:
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_token(user["id"], user["email"], app.config["JWT_SECRET_KEY"])
    return jsonify({"token": token, "user": user})


# ================================================================ Razorpay routes
@app.route("/api/razorpay/connect", methods=["POST"])
@login_required
def connect_razorpay():
    data = request.get_json()
    key_id = data.get("key_id", "").strip()
    key_secret = data.get("key_secret", "").strip()

    if not key_id or not key_secret:
        return jsonify({"error": "API Key ID and Secret required"}), 400

    # Verify connection
    client = RazorpayClient(key_id, key_secret)
    success, error_msg = client.verify_connection()
    if not success:
        return jsonify({"error": error_msg}), 400

    save_razorpay_connection(request.user_id, key_id, key_secret)
    return jsonify({"message": "Razorpay connected successfully", "test_mode": True})


@app.route("/api/razorpay/status", methods=["GET"])
@login_required
def razorpay_status():
    conn = get_razorpay_connection(request.user_id)
    if not conn:
        return jsonify({"connected": False})
    return jsonify({
        "connected": True,
        "key_id": conn["key_id"][:12] + "...",
        "is_test_mode": bool(conn["is_test_mode"]),
        "connected_at": conn["connected_at"],
    })


@app.route("/api/razorpay/fetch", methods=["POST"])
@login_required
def fetch_razorpay_data():
    conn = get_razorpay_connection(request.user_id)
    if not conn:
        return jsonify({"error": "Razorpay not connected. Please connect first."}), 400

    client = RazorpayClient(conn["key_id"], conn["key_secret"])
    raw_data = client.fetch_all_data()
    normalized = normalize_razorpay_data(raw_data)

    return jsonify({
        "raw_counts": raw_data["counts"],
        "normalized": {
            "payments": len(normalized["payments"]),
            "settlements": len(normalized["settlements"]),
            "refunds": len(normalized["refunds"]),
        },
        "data": normalized,
    })


# ================================================================ Bank upload routes
@app.route("/api/bank/upload", methods=["POST"])
@login_required
def upload_bank_statement():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    bank_name = request.form.get("bank_name", "").strip() or None

    if not file.filename.endswith(".csv"):
        return jsonify({"error": "Only CSV files are accepted"}), 400

    csv_text = file.read().decode("utf-8", errors="replace")
    result = parse_bank_csv(csv_text, bank_hint=bank_name)

    upload_id = save_bank_upload(
        user_id=request.user_id,
        bank_name=result.bank_name,
        filename=file.filename,
        row_count=result.total_rows,
        razorpay_rows=result.razorpay_rows,
        other_gateway_rows=result.other_gateway_rows,
        csv_data=csv_text,
    )

    return jsonify({
        "upload_id": upload_id,
        "bank_name": result.bank_name,
        "total_rows": result.total_rows,
        "credit_rows": result.credit_rows,
        "razorpay_rows": result.razorpay_rows,
        "other_gateway_rows": result.other_gateway_rows,
        "unknown_rows": result.unknown_rows,
        "gateway_summary": result.gateway_summary,
    })


@app.route("/api/bank/uploads", methods=["GET"])
@login_required
def list_bank_uploads():
    uploads = get_bank_uploads(request.user_id)
    return jsonify({"uploads": uploads})


# ================================================================ Reconciliation routes
@app.route("/api/reconcile", methods=["POST"])
@login_required
def run_reconciliation():
    """Run reconciliation using either live Razorpay+bank data or synthetic data."""
    data = request.get_json() or {}
    mode = data.get("mode", "synthetic")  # "live" or "synthetic"

    started = time.time()

    if mode == "live":
        # Use live Razorpay data + uploaded bank statements
        conn = get_razorpay_connection(request.user_id)
        if not conn:
            return jsonify({"error": "Razorpay not connected"}), 400

        # Fetch Razorpay data
        client = RazorpayClient(conn["key_id"], conn["key_secret"])
        raw_data = client.fetch_all_data()
        razorpay_data = normalize_razorpay_data(raw_data)

        # Get bank statement rows
        uploads = get_bank_uploads(request.user_id)
        bank_rows = []
        for upload in uploads:
            csv_data = get_bank_upload_data(upload["id"], request.user_id)
            if csv_data:
                parsed = parse_bank_csv(csv_data, bank_hint=upload["bank_name"])
                # Only include Razorpay-related rows for reconciliation
                bank_rows.extend(parsed.rows)

        try:
            batch = build_normalized_batch(razorpay_data, bank_rows)
            result = pipeline.run(batch)
        except Exception as e:
            return jsonify({"error": f"Reconciliation failed: {str(e)}"}), 500

        # Build results dict
        results_dict = _build_results_dict(result, batch)
        metrics_dict = _build_live_metrics(result, batch, time.time() - started)

    else:
        # Use synthetic data (for demo/testing)
        seed = data.get("seed", 42)
        ds = generate(seed)

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            emit.emit(ds, out)
            batch = load(out)

        result = pipeline.run(batch)

        # Build results with ground truth
        results_dict = _build_results_dict(result, batch)
        metrics_dict = _build_synthetic_metrics(result, ds, batch, time.time() - started)

    # Save to database
    save_reconciliation_run(
        request.user_id,
        json.dumps(results_dict),
        json.dumps(metrics_dict),
    )

    return jsonify({
        "message": "Reconciliation completed",
        "mode": mode,
        "wall_clock_seconds": round(time.time() - started, 4),
    })


@app.route("/api/results", methods=["GET"])
@login_required
def get_results():
    run = get_latest_reconciliation(request.user_id)
    if not run:
        return jsonify({"error": "No reconciliation run found. Run reconciliation first."}), 404
    return jsonify(run)


@app.route("/api/results/exceptions", methods=["GET"])
@login_required
def get_exceptions():
    run = get_latest_reconciliation(request.user_id)
    if not run:
        return jsonify({"error": "No reconciliation run found"}), 404

    decisions = run["results"].get("decisions", [])
    exceptions = [
        d for d in decisions
        if d.get("state") in ("HUMAN_REVIEW", "UNRESOLVED", "AWAITING_BANK")
    ]

    return jsonify({
        "total": len(exceptions),
        "exceptions": sorted(exceptions, key=lambda x: x.get("state", "")),
    })


@app.route("/api/results/transaction/<unit_id>", methods=["GET"])
@login_required
def get_transaction_detail(unit_id):
    run = get_latest_reconciliation(request.user_id)
    if not run:
        return jsonify({"error": "No reconciliation run found"}), 404

    decisions = run["results"].get("decisions", [])
    decision = next((d for d in decisions if d.get("unit_id") == unit_id), None)
    if not decision:
        return jsonify({"error": f"Transaction {unit_id} not found"}), 404

    # Find decomposition for this transaction's settlement
    decompositions = run["results"].get("decompositions", [])
    decomposition = None
    for d in decompositions:
        if d.get("settlement_id") and unit_id.startswith("pay_"):
            # Match by settlement - would need settlement mapping
            decomposition = d
            break

    return jsonify({
        "decision": decision,
        "decomposition": decomposition,
    })


from server.copilot import ask_copilot
from server.recon_qa_agent import ReconciliationQAAgent


# ================================================================ Q&A Agent routes
@app.route("/api/agent/qa", methods=["POST"])
@login_required
def recon_qa_agent_ask():
    data = request.get_json() or {}
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "Question is required"}), 400

    run = get_latest_reconciliation(request.user_id)
    results = run["results"] if run else {}
    metrics = run["metrics"] if run else {}

    agent = ReconciliationQAAgent(results, metrics)
    answer = agent.answer(question)
    return jsonify({"answer": answer, "question": question, "agent": "ReconciliationQAAgent"})


@app.route("/api/copilot/ask", methods=["POST"])
@login_required
def copilot_ask():
    data = request.get_json() or {}
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "Question is required"}), 400

    run = get_latest_reconciliation(request.user_id)
    if not run:
        return jsonify({
            "answer": "No reconciliation data available. Please run reconciliation first, then ask me questions about the results."
        })

    # Route through dedicated ReconciliationQAAgent for strict domain-bounded answers
    agent = ReconciliationQAAgent(run["results"], run["metrics"])
    answer = agent.answer(question)
    return jsonify({"answer": answer, "question": question, "agent": "ReconciliationQAAgent"})


# ================================================================ Helper functions

def _build_results_dict(result, batch) -> dict:
    """Convert PipelineResult into a JSON-serializable dict."""
    decisions = []
    for d in result.decisions:
        decisions.append(d.as_dict())

    decompositions = []
    for d in result.decompositions:
        decompositions.append({
            "settlement_id": d.settlement_id,
            "expected_paise": d.expected_paise,
            "actual_paise": d.actual_paise,
            "gap_paise": d.gap_paise,
            "attributed": [
                {"cause": a.cause, "amount_paise": a.amount_paise, "proof": a.proof}
                for a in d.attributed
            ],
            "unexplained_paise": d.unexplained_paise,
            "failed": d.failed,
            "failure_reason": d.failure_reason,
        })

    # Build payment-settlement-bank mapping for flow visualization
    payment_flows = []
    settlement_map = {s.settlement_id: s for s in batch.settlements}
    payment_by_settlement = defaultdict(list)
    for p in batch.payments:
        if p.settlement_id:
            payment_by_settlement[p.settlement_id].append(p)

    for d in result.decisions:
        flow = {
            "unit_id": d.unit_id,
            "unit_kind": d.unit_kind,
            "state": d.state,
            "rule": d.rule,
        }

        # Find matching payment
        payment = next((p for p in batch.payments if p.payment_id == d.unit_id), None)
        if payment:
            flow["payment"] = {
                "payment_id": payment.payment_id,
                "order_id": payment.order_id,
                "gross_paise": payment.gross_paise,
                "captured_on": payment.captured_on.isoformat(),
                "settlement_id": payment.settlement_id,
            }

            # Find settlement
            if payment.settlement_id and payment.settlement_id in settlement_map:
                s = settlement_map[payment.settlement_id]
                flow["settlement"] = {
                    "settlement_id": s.settlement_id,
                    "settled_on": s.settled_on.isoformat(),
                    "utr": s.utr,
                    "due_on": s.due_on.isoformat(),
                }

            # Find ledger entry
            ledger_entry = next((l for l in batch.ledger if l.payment_id == d.unit_id), None)
            if ledger_entry:
                flow["ledger"] = {
                    "ledger_id": ledger_entry.ledger_id,
                    "amount_paise": ledger_entry.amount_paise,
                    "booked_on": ledger_entry.booked_on.isoformat(),
                }

        payment_flows.append(flow)

    # Build match report summary
    match_summary = result.match_report.outcome_counts()

    return {
        "decisions": decisions,
        "decompositions": decompositions,
        "payment_flows": payment_flows,
        "match_summary": match_summary,
        "integrity": result.integrity_report.summary(),
        "wall_clock_seconds": round(result.wall_clock_seconds, 4),
    }


def _build_live_metrics(result, batch, wall_clock: float) -> dict:
    """Build metrics for live data (no ground truth available)."""
    decisions = result.decisions
    routing = Counter(d.routing for d in decisions)
    failures = sum(1 for d in decisions if d.rule == DECOMPOSITION_FAILED)
    terminal = sum(1 for d in decisions if d.state in ("VERIFIED", "EXPLAINED"))
    total = len(decisions)

    state_counts = Counter(d.state for d in decisions)

    return {
        "metrics": {
            "split": "live",
            "seed": 0,
            "targets": "live_data",
            "population_total": total,
            "population_scored": total,
            "decomposition_failures": failures,
            "coverage_terminal_without_human": round(terminal / total, 6) if total else 0.0,
            "routing": dict(sorted(routing.items())),
            "thresholds": {"auto": AUTO_THRESHOLD, "review": REVIEW_THRESHOLD},
            "state_counts": dict(state_counts),
            "honesty_clause": "Live data reconciliation — no synthetic ground truth available.",
        },
        "run_meta": {
            "wall_clock_seconds": round(wall_clock, 4),
            "records_per_second": round(total / wall_clock, 1) if wall_clock else 0.0,
            "mode": "live",
        },
    }


def _build_synthetic_metrics(result, ds, batch, wall_clock: float) -> dict:
    """Build full metrics for synthetic data (with ground truth)."""
    truth = {t.unit_id: t for t in ds.truth}
    predicted = {d.unit_id: d for d in result.decisions}
    impact = {t.unit_id: t.rupee_impact_paise for t in ds.truth}

    scored, excluded = [], 0
    rupees = Counter()
    for unit_id, t in sorted(truth.items()):
        if t.fault_class in DEV_ONLY_CLASSES:
            excluded += 1
            continue
        d = predicted.get(unit_id)
        if d is None:
            continue
        scored.append((t.true_state, d.state, t.fault_class))
        if d.routing == AUTO_RECONCILE:
            bucket = "auto_correct" if d.state == t.true_state else "auto_wrong"
            rupees[bucket] += impact[unit_id]
        elif d.state in ("HUMAN_REVIEW", "AWAITING_BANK"):
            rupees["review"] += impact[unit_id]
        else:
            rupees["unresolved"] += impact[unit_id]

    matrix = confusion.build([(t, p) for t, p, _ in scored])
    fault_only = confusion.build([(t, p) for t, p, fc in scored if fc != "clean"])
    attribution_score = attribution.score(result.decompositions, ds.settlement_truth)
    routing = Counter(d.routing for d in result.decisions)
    failures = sum(1 for d in result.decisions if d.rule == DECOMPOSITION_FAILED)
    terminal = sum(1 for d in result.decisions if d.state in ("VERIFIED", "EXPLAINED"))

    state_counts = Counter(d.state for d in result.decisions)

    return {
        "metrics": {
            "split": "synthetic",
            "seed": ds.seed,
            "targets": "default",
            "denominator": "recon_unit",
            "population_total": len(ds.truth),
            "population_scored": matrix.scored,
            "units_excluded_same_axis_compounds": excluded,
            "decomposition_failures": failures,
            "confusion": matrix.as_dict(),
            "confusion_fault_carrying_only": fault_only.as_dict(),
            "attribution": attribution_score.as_dict(),
            "coverage_terminal_without_human": round(terminal / matrix.scored, 6) if matrix.scored else 0.0,
            "routing": dict(sorted(routing.items())),
            "thresholds": {"auto": AUTO_THRESHOLD, "review": REVIEW_THRESHOLD},
            "state_counts": dict(state_counts),
            "rupees": {
                "auto_reconciled_correct": rupees["auto_correct"],
                "auto_reconciled_wrong_cost_of_false_positives": rupees["auto_wrong"],
                "sent_to_human_review": rupees["review"],
                "unresolved": rupees["unresolved"],
            },
            "honesty_clause": HONESTY_CLAUSE,
        },
        "run_meta": {
            "wall_clock_seconds": round(wall_clock, 4),
            "records_per_second": round(len(ds.truth) / wall_clock, 1) if wall_clock else 0.0,
            "mode": "synthetic",
        },
    }


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 5000))
    print("\n  ╔═══════════════════════════════════════════════╗")
    print(f"   ║   AI Finance Controller — Web Dashboard       ║")
    print(f"   ║   http://localhost:{port:<27}                 ║")
    print("    ╚═══════════════════════════════════════════════╝\n")
    app.run(host="0.0.0.0", debug=True, port=port)
