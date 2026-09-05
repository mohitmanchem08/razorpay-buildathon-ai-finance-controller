"""
main.py
FastAPI backend for the Razorpay AI Finance Controller.

Design note: this file is a thin HTTP wrapper around matcher.py, ai_explainer.py,
and qa_agent.py -- none of the core reconciliation, AI, or safety logic lives
here. Those modules are already tested (see test_matcher.py). This is now the
single frontend for the project (a Streamlit UI, app.py, existed earlier and
was removed once this frontend was live-tested and confirmed working end to
end -- see README's "What broke" section).

Run with: uvicorn main:app --reload --port 8000
"""

import os
import shutil
import tempfile
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from matcher import reconcile, summarize, recommended_action, neutralize_formula_injection, MAX_ROWS
from ai_explainer import explain_exception_structured
from qa_agent import build_context, ask_question
import ai_client

app = FastAPI(title="Razorpay AI Finance Controller API")

# CORS is restricted to localhost origins only. This API and its frontend
# are both meant to run together locally for a demo/judge review, never
# deployed as a public multi-tenant service (see README) -- so there's no
# legitimate reason for any other origin to call these endpoints. A wildcard
# ("*") would have been harmless in practice for a local-only tool, but
# restricting it anyway is correct API hygiene and costs nothing.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

os.makedirs("static", exist_ok=True)

# Custom exception handlers: return clean, consistent JSON for both API
# routes and any unmatched path, instead of FastAPI's default error format.
# For a browser hitting a bad URL directly, 404s especially benefit from a
# response that doesn't look like a raw stack trace.
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request, exc):
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={
                "error": "not_found",
                "message": f"No route matches '{request.url.path}'. "
                           f"Available API routes are under /api/*, and the dashboard is served at /.",
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "message": str(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "error": "validation_error",
            "message": "The request was malformed or missing required fields.",
            "details": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def custom_unhandled_exception_handler(request, exc):
    # Catches anything not already handled by a specific HTTPException raise
    # elsewhere in this file -- ensures the client always gets clean JSON,
    # never a raw Python traceback, even for a genuinely unexpected bug.
    return JSONResponse(
        status_code=500,
        content={"error": "internal_error", "message": "An unexpected server error occurred."},
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

MAX_UPLOAD_SIZE_MB = 5

# Tracks the currently active dataset paths and AI results in-memory, for
# this single-user local demo. Not designed for concurrent multi-user use --
# consistent with the project's stated local-only scope (see README).
STATE = {
    "orders_path": "data/orders.csv",
    "settlements_path": "data/settlements.csv",
    "ai_results": {},   # order_id -> {category, confidence, rationale, model_used}
    "reviewed": set(),
}


class QuestionRequest(BaseModel):
    question: str


class ReviewRequest(BaseModel):
    order_id: str
    reviewed: bool


@app.get("/")
def serve_frontend():
    return FileResponse(os.path.join("static", "index.html"))


def _cleanup_temp_upload_files():
    """
    Deletes the currently-tracked temp upload files, if any exist. Called
    before replacing them (new upload) or reverting to sample data (reset),
    so old uploaded CSVs don't silently accumulate on disk across a session
    -- a real, confirmed leak in the original version, which created a new
    timestamped temp file on every upload and never removed the previous one.
    """
    for key in ("orders_path", "settlements_path"):
        path = STATE.get(key)
        if path and path not in ("data/orders.csv", "data/settlements.csv"):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass  # already gone -- fine


@app.post("/api/upload")
def upload_files(orders_csv: UploadFile = File(...), settlements_csv: UploadFile = File(...)):
    """Accepts uploaded CSVs, enforces the same size limit as the Streamlit app."""
    for f, label in [(orders_csv, "orders_csv"), (settlements_csv, "settlements_csv")]:
        f.file.seek(0, os.SEEK_END)
        size_mb = f.file.tell() / (1024 * 1024)
        f.file.seek(0)
        if size_mb > MAX_UPLOAD_SIZE_MB:
            raise HTTPException(
                status_code=413,
                detail=f"{label} is {size_mb:.1f}MB, exceeding the {MAX_UPLOAD_SIZE_MB}MB limit.",
            )

    _cleanup_temp_upload_files()  # remove any previous upload's temp files first

    orders_path = os.path.join(tempfile.gettempdir(), f"orders_{datetime.now().timestamp()}.csv")
    settlements_path = os.path.join(tempfile.gettempdir(), f"settlements_{datetime.now().timestamp()}.csv")

    with open(orders_path, "wb") as out:
        shutil.copyfileobj(orders_csv.file, out)
    with open(settlements_path, "wb") as out:
        shutil.copyfileobj(settlements_csv.file, out)

    # Validate immediately so a bad upload fails fast with a clear error,
    # same as the Streamlit app's behavior, rather than failing later.
    try:
        reconcile(orders_path=orders_path, settlements_path=settlements_path)
    except ValueError as e:
        os.unlink(orders_path)       # clean up the just-written, invalid files too
        os.unlink(settlements_path)
        raise HTTPException(status_code=400, detail=str(e))

    STATE["orders_path"] = orders_path
    STATE["settlements_path"] = settlements_path
    STATE["ai_results"] = {}
    STATE["reviewed"] = set()
    return {"status": "ok", "message": "Files uploaded and validated successfully."}


@app.post("/api/reset")
def reset_to_sample_data():
    """Reverts to the built-in sample dataset, cleaning up any uploaded temp files."""
    _cleanup_temp_upload_files()
    STATE["orders_path"] = "data/orders.csv"
    STATE["settlements_path"] = "data/settlements.csv"
    STATE["ai_results"] = {}
    STATE["reviewed"] = set()
    return {"status": "ok"}


@app.get("/api/reconcile")
def get_reconciliation():
    """Returns the full reconciliation result set, serialized for the frontend."""
    try:
        results, orphans = reconcile(
            orders_path=STATE["orders_path"], settlements_path=STATE["settlements_path"]
        )
        summary = summarize(results, orphans)

        results_data = []
        for r in results:
            ai = STATE["ai_results"].get(r.order_id, {})
            results_data.append({
                "order_id": r.order_id,
                "order_amount": r.order_amount,
                "order_date": str(r.order_date),
                "customer_ref": r.customer_ref,
                "status": r.status,
                "reason": r.reason,
                "match_type": r.match_type,
                "recommended_action": recommended_action(r.match_type, ai.get("escalated", False)) if r.status == "exception" else "",
                "payment_id": r.payment_id or "",
                "settled_amount": r.settled_amount if r.settled_amount is not None else None,
                "settled_date": str(r.settled_date) if r.settled_date else "",
                "ai_category": ai.get("category", ""),
                "confidence": ai.get("confidence"),
                "ai_escalated": ai.get("escalated", False),
                "ai_rationale": ai.get("rationale", ""),
                "model_used": ai.get("model_used", ""),
                "reviewed": r.order_id in STATE["reviewed"],
            })

        return {"summary": summary, "results": results_data, "orphans": orphans}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/analyze_batch")
def analyze_batch():
    """
    Runs AI structured analysis on all ambiguous exceptions in one call,
    mirroring the Streamlit app's batch button behavior exactly.
    """
    AI_MARKER = "outside expected tolerance"
    try:
        results, orphans = reconcile(
            orders_path=STATE["orders_path"], settlements_path=STATE["settlements_path"]
        )
        eligible = [r for r in results if r.status == "exception" and AI_MARKER in r.reason]

        for r in eligible:
            STATE["ai_results"][r.order_id] = explain_exception_structured(
                order_id=r.order_id, order_amount=r.order_amount, order_date=r.order_date,
                settled_amount=r.settled_amount, settled_date=r.settled_date,
                rule_based_reason=r.reason,
            )

        return {"status": "ok", "analyzed_count": len(eligible)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/review")
def set_review_status(req: ReviewRequest):
    if req.reviewed:
        STATE["reviewed"].add(req.order_id)
    else:
        STATE["reviewed"].discard(req.order_id)
    return {"status": "ok"}


@app.post("/api/ask")
def ask_agent(req: QuestionRequest):
    try:
        ctx = build_context(orders_path=STATE["orders_path"], settlements_path=STATE["settlements_path"])
        answer = ask_question(req.question, context=ctx)
        return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/usage")
def get_ai_usage():
    """Live AI call tracking, same source as the Streamlit sidebar indicator."""
    return ai_client.get_usage_summary()


@app.get("/api/audit_csv")
def get_audit_csv():
    """Returns the full audit trail as CSV, with the same formula-injection
    protection applied as the Streamlit export."""
    import io
    import pandas as pd
    from fastapi.responses import StreamingResponse

    results, orphans = reconcile(
        orders_path=STATE["orders_path"], settlements_path=STATE["settlements_path"]
    )
    run_timestamp = datetime.now().isoformat(timespec="seconds")
    rows = []
    for r in results:
        ai = STATE["ai_results"].get(r.order_id, {})
        rows.append({
            "timestamp": run_timestamp, "type": r.status, "order_id": r.order_id,
            "claimed_reference": "", "order_amount": r.order_amount, "order_date": r.order_date,
            "payment_id": r.payment_id or "",
            "settled_amount": r.settled_amount if r.settled_amount is not None else "",
            "settled_date": r.settled_date if r.settled_date else "",
            "customer_ref": neutralize_formula_injection(r.customer_ref),
            "reason": r.reason,
            "match_type": r.match_type,
            "recommended_action": recommended_action(r.match_type, ai.get("escalated", False)) if r.status == "exception" else "",
            "ai_category": ai.get("category", ""), "confidence_score": ai.get("confidence", ""),
            "ai_rationale": ai.get("rationale", ""), "reviewed": r.order_id in STATE["reviewed"],
        })
    for o in orphans:
        rows.append({
            "timestamp": run_timestamp, "type": "orphan_settlement", "order_id": "",
            "claimed_reference": neutralize_formula_injection(o["utr_ref"]),
            "order_amount": "", "order_date": "", "payment_id": o["payment_id"],
            "settled_amount": o["amount"], "settled_date": o["date"], "customer_ref": "",
            "reason": o["reason"], "recommended_action": "MANUAL_REVIEW_REQUIRED",
            "ai_category": "", "confidence_score": "", "ai_rationale": "",
            "reviewed": o["payment_id"] in STATE["reviewed"],
        })

    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=reconciliation_audit_trail.csv"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
