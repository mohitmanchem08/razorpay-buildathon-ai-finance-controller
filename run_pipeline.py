"""
run_pipeline.py
End-to-end: reconcile orders vs settlements, then hand only the genuinely
unclear exceptions (the ones matcher.py couldn't confidently explain with
rules alone) to the AI layer for a plain-English guess.

Design choice: we do NOT call the AI for every exception. Duplicates,
missing settlements, and orphan settlements already have clear, deterministic
reasons -- calling an LLM on those would be pure cost with no benefit, and
would risk it inventing a less-accurate explanation than the rule already has.
The AI is reserved for the "amount/date mismatch outside expected tolerance"
bucket, where a human analyst's judgment genuinely adds value over a fixed rule.
"""

from matcher import reconcile, summarize
from ai_explainer import explain_exception

AI_ELIGIBLE_MARKER = "outside expected tolerance"


def run():
    results, orphans = reconcile()
    summary = summarize(results, orphans)

    print(f"{'='*60}")
    print(f"RECONCILIATION SUMMARY")
    print(f"{'='*60}")
    print(f"Match rate: {summary['matched_count']}/{summary['total_orders']} "
          f"({summary['match_rate_pct']}%)")
    print(f"Exceptions: {summary['exception_count']}")
    print(f"Orphan settlements: {summary['orphan_count']}")
    print()

    exceptions = [r for r in results if r.status == "exception"]
    ai_calls_made = 0

    print(f"{'='*60}")
    print(f"EXCEPTIONS (rule-based reason, AI added where genuinely unclear)")
    print(f"{'='*60}")
    for r in exceptions:
        reason = r.reason
        if AI_ELIGIBLE_MARKER in reason:
            ai_reason = explain_exception(
                order_id=r.order_id,
                order_amount=r.order_amount,
                order_date=r.order_date,
                settled_amount=r.settled_amount,
                settled_date=r.settled_date,
                rule_based_reason=reason,
            )
            ai_calls_made += 1
            print(f"✘ {r.order_id} (₹{r.order_amount:.0f})")
            print(f"    Rule-based: {reason}")
            print(f"    AI analysis: {ai_reason}")
        else:
            print(f"✘ {r.order_id} (₹{r.order_amount:.0f}) — {reason}")
        print()

    print(f"{'='*60}")
    print(f"AI calls made this run: {ai_calls_made} "
          f"(kept minimal by design — see docstring)")
    print(f"{'='*60}")


if __name__ == "__main__":
    run()
