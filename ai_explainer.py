"""
ai_explainer.py
Uses the configured AI provider (see ai_client.py) to add explanations on
top of exceptions that matcher.py's rule-based logic couldn't confidently
classify on its own.

Design note: we deliberately do NOT ask the AI to re-decide matched vs
exception -- that decision stays deterministic and auditable in matcher.py.
The AI's job here is narrower and safer: given the numbers, suggest the most
plausible reason, the way a human analyst glancing at the row would.

Structured output uses a Pydantic schema with Gemini's native JSON mode,
which is more reliable than instructing the model to "output only JSON" in
plain text -- but we still keep a fallback path in case parsing fails for
any reason, so a malformed response never crashes the app or shows garbage.
"""

import json
from pydantic import BaseModel, Field
from ai_client import ask_ai

CATEGORY_OPTIONS = [
    "GATEWAY_FEE_MISMATCH",
    "PARTIAL_PAYMENT_OR_REFUND",
    "DATE_TIMING_ISSUE",
    "UNKNOWN_DISCREPANCY",
]


class ExplanationSchema(BaseModel):
    category: str = Field(description=f"Must be one of {CATEGORY_OPTIONS}")
    confidence: float = Field(description="Float between 0.0 and 1.0")
    rationale: str = Field(description="One sentence plain-English rationale")


def explain_exception(order_id, order_amount, order_date, settled_amount=None,
                       settled_date=None, rule_based_reason=""):
    """
    Plain-text explanation of one exception. Falls back gracefully on
    API failure so a demo never crashes on a network hiccup.
    """
    prompt = f"""You are a finance-ops assistant helping a merchant reconcile payments.
Given this unresolved transaction, suggest the single most likely reason it
didn't match cleanly, in one plain-English sentence. Be specific and grounded
in the numbers given. If genuinely unsure, say so honestly instead of
guessing confidently.

Order ID: {order_id}
Order amount: Rs.{order_amount}
Order date: {order_date}
Settled amount: {f'Rs.{settled_amount}' if settled_amount is not None else 'No settlement found'}
Settled date: {settled_date if settled_date else 'N/A'}
Rule-based system's initial note: {rule_based_reason}

Respond with ONLY the one-sentence explanation, nothing else."""

    try:
        result = ask_ai(prompt, max_tokens=400)
        if not result or not result.strip():
            return (
                f"[AI returned an empty response with no error] "
                f"Falling back to rule-based note: {rule_based_reason}"
            )
        # A response under 15 chars with no sentence-ending punctuation is
        # almost certainly cut off mid-thought (token budget exhausted
        # before the model finished), not a genuinely short real answer --
        # this happened once with max_tokens=150 (fixed above to 400), kept
        # here as a safety net rather than trusting the budget alone.
        if len(result.strip()) < 15 and not result.strip().endswith((".", "!", "?")):
            return (
                f"[AI response appears truncated: '{result.strip()}'] "
                f"Falling back to rule-based note: {rule_based_reason}"
            )
        return result
    except Exception as e:
        return f"[AI explanation unavailable: {e}] Falling back to rule-based note: {rule_based_reason}"


CONFIDENCE_ESCALATION_THRESHOLD = 0.70


def explain_exception_structured(order_id, order_amount, order_date, settled_amount=None,
                                  settled_date=None, rule_based_reason=""):
    """
    Structured version: returns {category, confidence, rationale} using
    native Gemini schema enforcement, so the audit trail can carry a usable
    ai_category and confidence_score, not just free text.

    Reliability note: even with native schema enforcement, we don't assume
    success blindly -- if parsing fails or the category isn't one of our
    known options, we fall back to the plain-text explainer rather than
    crashing or showing broken data in the audit trail.

    Confidence gating: if the AI's own confidence is below
    CONFIDENCE_ESCALATION_THRESHOLD, we don't just display the low number --
    we set escalated=True and recommended_action=ESCALATE_TO_SENIOR_ANALYST,
    so a genuinely uncertain AI guess is routed to a human rather than
    presented next to more confident categorizations with equal visual
    weight. The original category/rationale are still shown, just paired
    with an explicit "don't trust this alone" signal.
    """
    prompt = f"""You are a finance-ops assistant. Analyze this unresolved transaction
and determine the most likely category, your confidence, and a one-sentence rationale.
If you are genuinely unsure of the cause, use category "UNKNOWN_DISCREPANCY" and a
lower confidence value rather than guessing confidently.

Order ID: {order_id}
Order amount: Rs.{order_amount}
Order date: {order_date}
Settled amount: {f'Rs.{settled_amount}' if settled_amount is not None else 'No settlement found'}
Settled date: {settled_date if settled_date else 'N/A'}
Rule-based system's initial note: {rule_based_reason}"""

    try:
        raw, model_used = ask_ai(
            prompt, max_tokens=500, response_schema=ExplanationSchema, return_model_used=True
        )
        if not raw or not raw.strip():
            raise ValueError("Model returned an empty response (no error, but no content either).")

        parsed = json.loads(raw)

        category = parsed.get("category", "UNCATEGORIZED")
        if category not in CATEGORY_OPTIONS:
            category = "UNCATEGORIZED"
        confidence = parsed.get("confidence")
        try:
            confidence = round(float(confidence), 2) if confidence is not None else None
        except (ValueError, TypeError):
            confidence = None
        rationale = parsed.get("rationale", raw)

        escalated = confidence is not None and confidence < CONFIDENCE_ESCALATION_THRESHOLD

        return {
            "category": category,
            "confidence": confidence,
            "rationale": rationale,
            "model_used": model_used,
            "escalated": escalated,
        }

    except Exception:
        rationale = explain_exception(
            order_id, order_amount, order_date, settled_amount, settled_date, rule_based_reason
        )
        # A parse/API failure means we have no reliable confidence signal at
        # all -- treat as escalated by default, same as a low-confidence result.
        return {
            "category": "UNCATEGORIZED", "confidence": None, "rationale": rationale,
            "model_used": None, "escalated": True,
        }


if __name__ == "__main__":
    result = explain_exception_structured(
        order_id="O158",
        order_amount=2500,
        order_date="2026-08-01",
        settled_amount=1637,
        settled_date="2026-08-01",
        rule_based_reason="Amount mismatch outside expected fee/partial tolerance.",
    )
    print(result)
