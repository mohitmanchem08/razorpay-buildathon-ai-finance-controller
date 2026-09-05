"""
test_matcher.py
Automated tests for matcher.py's core reconciliation logic.

These tests use small, hand-built CSVs (not the full synthetic dataset) so
each test is fast, isolated, and its expected outcome is verifiable by eye.
Run with: pytest test_matcher.py -v
"""

import csv
import os
import tempfile
import pytest
from matcher import reconcile, summarize, recommended_action, validate_orders, validate_settlements


def _write_csv(path, fieldnames, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_exact_match(temp_dir):
    """An order with an identical settlement (same amount, same date) should match exactly."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "1000", "date": "2026-08-01", "utr_ref": "O1"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert len(results) == 1
    assert results[0].status == "matched"
    assert "Exact match" in results[0].reason
    assert len(orphans) == 0


def test_fee_deduction_within_tolerance_matches(temp_dir):
    """A settlement 2% short of the order amount should match as a gateway fee, not an exception."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "980", "date": "2026-08-01", "utr_ref": "O1"}])  # 2% fee

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "matched"
    assert "fee" in results[0].reason.lower()


def test_large_mismatch_is_exception(temp_dir):
    """A settlement far outside the fee/partial tolerance should be an exception, not a false match."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "890", "date": "2026-08-01", "utr_ref": "O1"}])  # 11% short: too big for fee, too small for partial

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "exception"
    assert "outside expected tolerance" in results[0].reason


def test_missing_settlement_flagged_correctly(temp_dir):
    """An order with no matching settlement at all should be a clear, specific exception."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    # Settlements file has data, just none referencing O1 -- this is the
    # realistic case (an empty settlements file is a separate, already-
    # covered validation case, see test_validate_settlements below).
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P99", "amount": "999", "date": "2026-08-01", "utr_ref": "O_UNRELATED"}])

    results, orphans = reconcile(orders_path, settlements_path)
    o1_result = [r for r in results if r.order_id == "O1"][0]
    assert o1_result.status == "exception"
    assert "No settlement found at all" in o1_result.reason
    assert recommended_action(o1_result.reason) == "CHECK_PAYMENT_STATUS"


def test_duplicate_settlement_detected(temp_dir):
    """Two settlements referencing the same order should be flagged as a duplicate, not silently picked."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "1000", "date": "2026-08-01", "utr_ref": "O1"},
                {"payment_id": "P2", "amount": "1000", "date": "2026-08-01", "utr_ref": "O1"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "exception"
    assert "duplicate entry" in results[0].reason
    assert recommended_action(results[0].reason) == "REVIEW_FOR_DEDUP"


def test_orphan_settlement_detected(temp_dir):
    """A settlement referencing an order_id that doesn't exist should be caught as an orphan."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "1000", "date": "2026-08-01", "utr_ref": "O1"},
                {"payment_id": "P2", "amount": "500", "date": "2026-08-01", "utr_ref": "O999"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert len(orphans) == 1
    assert orphans[0]["payment_id"] == "P2"
    assert orphans[0]["utr_ref"] == "O999"


def test_partial_settlement_distinguished_from_fee(temp_dir):
    """A settlement 50% short should be classified as 'partial', not confused with a fee deduction."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "500", "date": "2026-08-01", "utr_ref": "O1"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "exception"
    assert "partial payment" in results[0].reason
    assert recommended_action(results[0].reason) == "VERIFY_PARTIAL_SETTLEMENT"


def test_date_drift_within_tolerance_matches(temp_dir):
    """A settlement 2 days late (within the 3-day tolerance) should still match, with a note."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "1000", "date": "2026-08-03", "utr_ref": "O1"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "matched"
    assert "day(s) after" in results[0].reason


def test_summarize_totals_are_accurate(temp_dir):
    """summarize()'s precomputed totals must exactly match a manual sum -- this is the core
    safeguard against AI math-hallucination, so it must itself be correct."""
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"], [
        {"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"},
        {"order_id": "O2", "amount": "500", "date": "2026-08-01", "customer_ref": "c2"},
    ])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"], [
        {"payment_id": "P1", "amount": "1000", "date": "2026-08-01", "utr_ref": "O1"},
        # O2 has no settlement -> exception
    ])

    results, orphans = reconcile(orders_path, settlements_path)
    summary = summarize(results, orphans)
    assert summary["matched_value_total"] == 1000.0
    assert summary["exception_value_total"] == 500.0
    assert summary["match_rate_pct"] == 50.0


def test_validate_orders_rejects_missing_column():
    """Uploading a CSV missing a required column should raise a clear, specific error."""
    with pytest.raises(ValueError, match="missing required column"):
        validate_orders([{"order_id": "O1", "amount": "1000", "date": "2026-08-01"}])  # missing customer_ref


def test_validate_orders_rejects_bad_amount():
    """A non-numeric amount should raise a clear, row-specific error, not a cryptic crash."""
    with pytest.raises(ValueError, match="not a valid amount"):
        validate_orders([{"order_id": "O1", "amount": "not_a_number",
                          "date": "2026-08-01", "customer_ref": "c1"}])


def test_validate_orders_rejects_bad_date():
    """A malformed date should raise a clear, row-specific error, not a cryptic crash."""
    with pytest.raises(ValueError, match="not a valid date"):
        validate_orders([{"order_id": "O1", "amount": "1000",
                          "date": "not-a-date", "customer_ref": "c1"}])


def test_empty_orders_file_rejected():
    """An empty orders file should raise a clear error rather than silently producing zero results."""
    with pytest.raises(ValueError, match="empty"):
        validate_orders([])


def test_empty_settlements_file_rejected():
    """An empty settlements file should raise a clear error, same as an empty orders file."""
    with pytest.raises(ValueError, match="empty"):
        validate_settlements([])


def test_full_synthetic_dataset_produces_expected_match_rate():
    """
    Regression guard: the real data/orders.csv + data/settlements.csv should
    always produce the same match rate. If this test fails after an edit,
    the matching logic changed behavior on real data -- investigate before
    assuming it's fine.

    Orphan count is 4. An earlier version of fuzzy matching (string
    similarity alone, no amount/date gate) incorrectly flagged P572
    (references 'O909', ₹600) as a plausible fuzzy match for O109 (₹1500)
    purely because 'O909' resembled 'O109' as a string -- a 60% amount
    deviation that should have disqualified it. After adding
    is_amount_date_compatible() as a required gate, P572 correctly reverts
    to a plain orphan, since a reference-string coincidence with no
    supporting amount evidence isn't meaningful signal on its own.
    """
    results, orphans = reconcile()  # uses default data/ paths
    summary = summarize(results, orphans)
    assert summary["total_orders"] == 70
    assert summary["matched_count"] == 50
    assert summary["exception_count"] == 20
    assert summary["orphan_count"] == 4
    assert summary["match_rate_pct"] == 71.4


def test_oversized_file_rejected(temp_dir):
    """A CSV exceeding MAX_ROWS should be rejected with a clear error, not silently processed
    (which could exhaust AI quota or freeze the app on an unexpectedly huge upload)."""
    from matcher import MAX_ROWS
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")

    # Build a file with MAX_ROWS + 1 rows -- one over the limit
    big_orders = [
        {"order_id": f"O{i}", "amount": "100", "date": "2026-08-01", "customer_ref": "c1"}
        for i in range(MAX_ROWS + 1)
    ]
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"], big_orders)
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"], [])

    with pytest.raises(ValueError, match="row limit"):
        reconcile(orders_path, settlements_path)


def test_formula_injection_neutralized():
    """
    A value starting with =, +, -, @, tab, or carriage return could be
    interpreted as an executable formula if the exported audit CSV is later
    opened in Excel/Sheets -- a real, documented attack class (CSV/formula
    injection). neutralize_formula_injection() must prefix these with a
    single quote so spreadsheet apps treat them as plain text.
    """
    from matcher import neutralize_formula_injection
    assert neutralize_formula_injection("=cmd|'/c calc'!A1").startswith("'=")
    assert neutralize_formula_injection("+1+1").startswith("'+")
    assert neutralize_formula_injection("@SUM(A1:A10)").startswith("'@")
    # Normal values must pass through completely unchanged
    assert neutralize_formula_injection("cust_42") == "cust_42"
    assert neutralize_formula_injection("O101") == "O101"


def test_recommended_action_escalates_on_low_ai_confidence():
    """
    When ai_escalated=True is passed (set when the AI's own confidence was
    below the threshold), recommended_action must override the normal
    rule-based action with ESCALATE_TO_SENIOR_ANALYST, regardless of what
    the reason text says. This is the one place an AI signal is allowed to
    influence the recommended action -- and it must only make the outcome
    MORE conservative, never less.
    """
    normal_reason = "Amount/date mismatch outside expected tolerance (order X vs settled Y)."
    # Without escalation, normal rule-based action applies
    assert recommended_action(normal_reason, ai_escalated=False) == "FLAG_FOR_FINANCE_REVIEW"
    # With escalation, it overrides regardless of the underlying reason
    assert recommended_action(normal_reason, ai_escalated=True) == "ESCALATE_TO_SENIOR_ANALYST"
    # Even a duplicate-entry reason gets overridden if AI escalation says so
    assert recommended_action("duplicate entry found", ai_escalated=True) == "ESCALATE_TO_SENIOR_ANALYST"
    # Default parameter value (no escalation info) preserves old behavior
    assert recommended_action(normal_reason) == "FLAG_FOR_FINANCE_REVIEW"


def test_normalize_reference_strips_common_affixes():
    """Common prefixes, leading zeros, and whitespace should all normalize toward the same form."""
    from matcher import normalize_reference
    assert normalize_reference("ORD_O101") == "O101"
    assert normalize_reference("TXN-O101") == "O101"
    assert normalize_reference(" O101 ") == "O101"
    assert normalize_reference("o101") == "O101"  # case-insensitive


def test_fuzzy_match_catches_single_char_typo():
    """A genuine single-character typo, with a clearly distinguishable set of
    candidates and compatible amounts, should be caught -- verified
    empirically (see FUZZY_SIMILARITY_THRESHOLD comment in matcher.py)."""
    from matcher import find_fuzzy_match
    from datetime import date
    d = date(2026, 8, 1)
    candidates = [
        {"utr_ref": "O102", "amount": "1000", "date": "2026-08-01"},  # typo match, compatible amount
        {"utr_ref": "O999", "amount": "1000", "date": "2026-08-01"},  # unrelated reference
        {"utr_ref": "O555", "amount": "1000", "date": "2026-08-01"},  # unrelated reference
    ]
    result = find_fuzzy_match("O101", 1000, d, candidates)
    assert result is not None
    assert result[0]["utr_ref"] == "O102"


def test_fuzzy_match_refuses_to_guess_on_tie():
    """If two candidates are EQUALLY similar, fuzzy matching must return None
    rather than arbitrarily picking one -- an ambiguous typo-correction is
    worse than no correction, since it risks misattributing a real payment."""
    from matcher import find_fuzzy_match
    from datetime import date
    d = date(2026, 8, 1)
    # O102 and O103 are both exactly 1 char off from O101 -> guaranteed tie,
    # both with compatible amounts so the tie is reached, not filtered out first
    candidates = [
        {"utr_ref": "O102", "amount": "1000", "date": "2026-08-01"},
        {"utr_ref": "O103", "amount": "1000", "date": "2026-08-01"},
    ]
    result = find_fuzzy_match("O101", 1000, d, candidates)
    assert result is None


def test_fuzzy_match_rejects_unrelated_references():
    """References that are genuinely different (not a plausible typo) must not match."""
    from matcher import find_fuzzy_match
    from datetime import date
    d = date(2026, 8, 1)
    candidates = [
        {"utr_ref": "O999", "amount": "1000", "date": "2026-08-01"},
        {"utr_ref": "O888", "amount": "1000", "date": "2026-08-01"},
    ]
    result = find_fuzzy_match("O101", 1000, d, candidates)
    assert result is None


def test_fuzzy_match_never_returns_matched_status(temp_dir):
    """
    End-to-end check: even when fuzzy matching succeeds, the resulting
    MatchResult must have status='exception', never 'matched'. Fuzzy matches
    require human confirmation and must never be silently treated as
    equivalent to an exact match.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O101", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    # Settlement references "O102" (typo of O101) instead of an exact match
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P999", "amount": "1000", "date": "2026-08-01", "utr_ref": "O102"},
                {"payment_id": "P998", "amount": "500", "date": "2026-08-01", "utr_ref": "O777"}])

    results, orphans = reconcile(orders_path, settlements_path)
    o101_result = [r for r in results if r.order_id == "O101"][0]
    assert o101_result.status == "exception"
    assert "possibly a typo" in o101_result.reason
    assert "NOT auto-matched" in o101_result.reason


def test_is_amount_date_compatible_gate():
    """Direct unit tests for the compatibility gate used before accepting any fuzzy match."""
    from matcher import is_amount_date_compatible
    from datetime import date
    d1 = date(2026, 8, 1)
    d2 = date(2026, 8, 3)
    d_far = date(2026, 9, 1)

    assert is_amount_date_compatible(1000, d1, 1000, d1) is True   # identical
    assert is_amount_date_compatible(1000, d1, 950, d2) is True    # 5% off, 2 days -- plausible
    assert is_amount_date_compatible(1500, d1, 600, d1) is False   # 60% off -- the real bug case
    assert is_amount_date_compatible(1000, d1, 1000, d_far) is False  # date too far apart


def test_fuzzy_match_rejects_incompatible_amount_despite_similar_reference(temp_dir):
    """
    Real regression test for the bug found during review: a settlement whose
    reference coincidentally resembles an order's ID, but whose amount is
    wildly different, must NOT be presented as a fuzzy match. String
    similarity alone was previously sufficient to trigger a fuzzy match --
    this is no longer the case.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O109", "amount": "1500", "date": "2026-08-01", "customer_ref": "c1"}])
    # "O909" is a plausible string-typo of "O109", but the amount is wildly different
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P572", "amount": "600", "date": "2026-08-01", "utr_ref": "O909"}])

    results, orphans = reconcile(orders_path, settlements_path)
    o109_result = [r for r in results if r.order_id == "O109"][0]
    # Must remain a plain "no settlement" exception, NOT a fuzzy match
    assert "No settlement found at all" in o109_result.reason
    assert "possibly a typo" not in o109_result.reason
    # The settlement should show up as a genuine orphan instead
    assert any(o["payment_id"] == "P572" for o in orphans)


def test_batch_settlement_verified_end_to_end(temp_dir):
    """
    Real end-to-end test through reconcile(): 3 orders sharing a batch_id,
    one settlement whose amount = sum minus a valid fee, should be detected
    as a verified batch match -- flagged for confirmation, not auto-matched.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref", "batch_id"], [
        {"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1", "batch_id": "B1"},
        {"order_id": "O2", "amount": "500", "date": "2026-08-01", "customer_ref": "c2", "batch_id": "B1"},
        {"order_id": "O3", "amount": "300", "date": "2026-08-01", "customer_ref": "c3", "batch_id": "B1"},
    ])
    # sum = 1800, settled 1764 = ~2% fee, within normal tolerance
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref", "batch_id"], [
        {"payment_id": "P1", "amount": "1764", "date": "2026-08-01", "utr_ref": "BATCH", "batch_id": "B1"},
    ])

    results, orphans = reconcile(orders_path, settlements_path)
    batch_results = [r for r in results if r.order_id in ("O1", "O2", "O3")]
    assert len(batch_results) == 3
    for r in batch_results:
        assert r.status == "exception"  # batch matches always need human confirmation
        assert "batch" in r.reason.lower()
        assert "NOT auto-confirmed" in r.reason
        assert r.payment_id == "P1"


def test_batch_settlement_with_bad_math_left_unresolved(temp_dir):
    """
    If a batch_id group's arithmetic does NOT verify within fee tolerance,
    the orders must remain plain unresolved exceptions -- never incorrectly
    grouped just because they share a batch_id.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref", "batch_id"], [
        {"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1", "batch_id": "B1"},
        {"order_id": "O2", "amount": "500", "date": "2026-08-01", "customer_ref": "c2", "batch_id": "B1"},
    ])
    # sum = 1500, but settled only 900 -- way outside any fee tolerance, should NOT verify
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref", "batch_id"], [
        {"payment_id": "P1", "amount": "900", "date": "2026-08-01", "utr_ref": "BATCH", "batch_id": "B1"},
    ])

    results, orphans = reconcile(orders_path, settlements_path)
    o1 = [r for r in results if r.order_id == "O1"][0]
    o2 = [r for r in results if r.order_id == "O2"][0]
    # Should remain plain "no settlement found" exceptions, NOT batch-matched
    assert "No settlement found at all" in o1.reason
    assert "No settlement found at all" in o2.reason


def test_batch_settlement_opt_in_no_effect_without_batch_id_column(temp_dir):
    """
    Critical safety test: data with NO batch_id column at all must produce
    IDENTICAL results to before this feature existed. This is what makes
    the feature safely opt-in rather than a silent behavior change for
    every existing dataset, including this project's own sample data.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    # No batch_id column anywhere, and settlement doesn't reference O1 at all
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P99", "amount": "999", "date": "2026-08-01", "utr_ref": "O_UNRELATED"}])

    results, orphans = reconcile(orders_path, settlements_path)
    assert results[0].status == "exception"
    assert "No settlement found at all" in results[0].reason
    assert "batch" not in results[0].reason.lower()


def test_every_result_has_a_real_match_type(temp_dir):
    """
    Every MatchResult produced by reconcile() must have a real match_type,
    never the "UNSET" default -- if a new matching branch is ever added
    without setting match_type, this test should catch it.
    """
    results, orphans = reconcile()  # full real dataset
    unset = [r for r in results if r.match_type == "UNSET"]
    assert unset == [], f"{len(unset)} result(s) have no match_type set: {[r.order_id for r in unset]}"


def test_summarize_categories_reconcile_against_exception_count():
    """
    The category counts in summarize() (duplicate/missing/ambiguous/partial/
    fuzzy/batch) must always sum to exactly exception_count -- if they don't,
    some exception type isn't being counted anywhere, which was a real gap
    found in review (fuzzy and batch categories were originally missing
    entirely from the breakdown).
    """
    results, orphans = reconcile()
    summary = summarize(results, orphans)
    assert summary["categories_reconcile"] is True
    category_sum = (summary["duplicate_count"] + summary["missing_count"] +
                    summary["ambiguous_count"] + summary["partial_count"] +
                    summary["fuzzy_reference_count"] + summary["batch_settlement_count"])
    assert category_sum == summary["exception_count"]


def test_recommended_action_accepts_structured_match_type():
    """recommended_action() should work correctly when passed a structured
    match_type code directly, not just a legacy reason string."""
    from matcher import (recommended_action, MATCH_TYPE_DUPLICATE, MATCH_TYPE_MISSING,
                          MATCH_TYPE_FUZZY_REFERENCE, MATCH_TYPE_BATCH_SETTLEMENT)
    assert recommended_action(MATCH_TYPE_DUPLICATE) == "REVIEW_FOR_DEDUP"
    assert recommended_action(MATCH_TYPE_MISSING) == "CHECK_PAYMENT_STATUS"
    assert recommended_action(MATCH_TYPE_FUZZY_REFERENCE) == "CONFIRM_FUZZY_REFERENCE_MATCH"
    assert recommended_action(MATCH_TYPE_BATCH_SETTLEMENT) == "CONFIRM_BATCH_SETTLEMENT_GROUPING"
    # AI escalation still overrides regardless of which form is passed
    assert recommended_action(MATCH_TYPE_DUPLICATE, ai_escalated=True) == "ESCALATE_TO_SENIOR_ANALYST"


def test_configurable_fee_tolerance_actually_changes_behavior(temp_dir):
    """
    reconcile()'s fee_min_pct/fee_max_pct overrides must genuinely affect
    matching outcomes, not just be accepted and ignored -- verified with a
    case that only matches under a wider tolerance than the module default.
    """
    orders_path = os.path.join(temp_dir, "orders.csv")
    settlements_path = os.path.join(temp_dir, "settlements.csv")
    # 10% "fee" -- outside the default 1.5%-3.6% range, so should be an
    # unresolved exception by default, but should MATCH under a wider policy
    _write_csv(orders_path, ["order_id", "amount", "date", "customer_ref"],
               [{"order_id": "O1", "amount": "1000", "date": "2026-08-01", "customer_ref": "c1"}])
    _write_csv(settlements_path, ["payment_id", "amount", "date", "utr_ref"],
               [{"payment_id": "P1", "amount": "900", "date": "2026-08-01", "utr_ref": "O1"}])

    # Default tolerance: should NOT match (10% is outside 1.5%-3.6%)
    default_results, _ = reconcile(orders_path, settlements_path)
    assert default_results[0].status == "exception"

    # Wider tolerance explicitly covering 10%: SHOULD match
    wide_results, _ = reconcile(orders_path, settlements_path, fee_min_pct=0.0, fee_max_pct=0.15)
    assert wide_results[0].status == "matched"


def test_evaluate_accuracy_reports_zero_false_auto_matches():
    """
    The independently hand-labeled evaluation harness (evaluate_accuracy.py)
    should report zero false auto-matches on its labeled set -- run here as
    a regression guard so this doesn't silently break if matching logic
    changes. This is a DIFFERENT check than the matcher's own unit tests:
    it exercises the full reconcile() pipeline against ground truth decided
    independently of the matcher's own match_type field, avoiding the
    circularity risk of testing the matcher against its own labels.
    """
    from evaluate_accuracy import evaluate
    result = evaluate()
    assert result["false_positives"] == 0, (
        "A false auto-match occurred against hand-labeled ground truth -- "
        "this is the most serious possible regression in a finance tool "
        "and must be investigated before proceeding."
    )
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
