"""
evaluate_accuracy.py
Measures precision, recall, and false-auto-match rate against an
INDEPENDENTLY hand-labeled ground truth set -- not by re-deriving "truth"
from the matcher's own match_type field, which would be circular (it would
mathematically guarantee 0% false-auto-match, since every "matched" status
in matcher.py already only ever comes from the four match types being
labeled as "true matches").

Instead, this script builds a small labeled dataset where each row's correct
outcome is decided BEFORE the matcher ever sees it, then checks the matcher's
actual behavior against that predetermined answer. This is what makes the
result meaningful evidence rather than a tautology.
"""

import csv
import os
import tempfile
from matcher import reconcile


def build_labeled_dataset(orders_path, settlements_path):
    """
    Each row is labeled with its correct outcome BEFORE the matcher runs.
    'should_match' = True means a competent human analyst would consider
    this a legitimate automatic match; False means it should be surfaced
    as an exception requiring review.
    """
    orders = []
    settlements = []
    ground_truth = {}  # order_id -> True (should auto-match) / False (should be exception)

    def add_case(order_id, order_amount, order_date, settlement_amount=None,
                 settlement_date=None, utr_ref=None, should_match=None, payment_id=None):
        orders.append({"order_id": order_id, "amount": order_amount, "date": order_date,
                        "customer_ref": f"cust_{order_id}"})
        if settlement_amount is not None:
            settlements.append({
                "payment_id": payment_id or f"P_{order_id}",
                "amount": settlement_amount,
                "date": settlement_date or order_date,
                "utr_ref": utr_ref or order_id,
            })
        ground_truth[order_id] = should_match

    # Clear correct auto-matches
    add_case("L001", 1000, "2026-08-01", 1000, "2026-08-01", should_match=True)
    add_case("L002", 500, "2026-08-01", 490, "2026-08-01", should_match=True)   # 2% fee, in-tolerance
    add_case("L003", 2000, "2026-08-01", 2000, "2026-08-03", should_match=True)  # date drift, in-tolerance
    add_case("L004", 750, "2026-08-01", 738, "2026-08-02", should_match=True)   # fee + drift, in-tolerance

    # Clear correct exceptions
    add_case("L005", 1000, "2026-08-01", None, should_match=False)              # genuinely missing
    add_case("L006", 500, "2026-08-01", 200, "2026-08-01", should_match=False)  # way outside tolerance
    add_case("L007", 1500, "2026-08-01", 750, "2026-08-01", should_match=False)  # partial payment
    add_case("L008", 1000, "2026-08-01", 850, "2026-08-01", should_match=False)  # 15% off, ambiguous

    # Duplicate settlement -- correct outcome is exception (needs dedup), not auto-match
    orders.append({"order_id": "L009", "amount": 1000, "date": "2026-08-01", "customer_ref": "cust_L009"})
    settlements.append({"payment_id": "P_L009a", "amount": 1000, "date": "2026-08-01", "utr_ref": "L009"})
    settlements.append({"payment_id": "P_L009b", "amount": 1000, "date": "2026-08-01", "utr_ref": "L009"})
    ground_truth["L009"] = False

    with open(orders_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["order_id", "amount", "date", "customer_ref"])
        w.writeheader()
        w.writerows(orders)
    with open(settlements_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["payment_id", "amount", "date", "utr_ref"])
        w.writeheader()
        w.writerows(settlements)

    return ground_truth


def evaluate():
    with tempfile.TemporaryDirectory() as d:
        orders_path = os.path.join(d, "orders.csv")
        settlements_path = os.path.join(d, "settlements.csv")
        ground_truth = build_labeled_dataset(orders_path, settlements_path)

        results, orphans = reconcile(orders_path, settlements_path)

        true_positives = 0   # correctly auto-matched
        false_positives = 0  # auto-matched but should have been an exception (the dangerous case)
        true_negatives = 0   # correctly flagged as exception
        false_negatives = 0  # flagged as exception but should have auto-matched

        for r in results:
            predicted_match = (r.status == "matched")
            actual_should_match = ground_truth[r.order_id]

            if predicted_match and actual_should_match:
                true_positives += 1
            elif predicted_match and not actual_should_match:
                false_positives += 1
            elif not predicted_match and not actual_should_match:
                true_negatives += 1
            elif not predicted_match and actual_should_match:
                false_negatives += 1

        total = len(ground_truth)
        precision = true_positives / (true_positives + false_positives) if (true_positives + false_positives) else 1.0
        recall = true_positives / (true_positives + false_negatives) if (true_positives + false_negatives) else 1.0
        false_auto_match_rate = false_positives / total

        print("=" * 60)
        print("ACCURACY EVALUATION (independently hand-labeled ground truth)")
        print("=" * 60)
        print(f"Labeled test cases       : {total}")
        print(f"True positives (correct auto-match)      : {true_positives}")
        print(f"False positives (WRONGLY auto-matched)   : {false_positives}")
        print(f"True negatives (correctly flagged)       : {true_negatives}")
        print(f"False negatives (should've auto-matched) : {false_negatives}")
        print("-" * 60)
        print(f"Precision                : {precision * 100:.1f}%")
        print(f"Recall                   : {recall * 100:.1f}%")
        print(f"False-auto-match rate    : {false_auto_match_rate * 100:.1f}%")
        print("=" * 60)
        print()
        print("Note: this is a small (9-case) hand-labeled set covering the")
        print("main decision boundaries (exact, fee, drift, missing, partial,")
        print("ambiguous, duplicate) -- meant as a real, non-circular sanity")
        print("check on the matcher's core logic, not a comprehensive")
        print("statistical accuracy claim. A larger independently-labeled set")
        print("would be needed for a rigorous precision/recall figure.")

        return {
            "precision": precision, "recall": recall,
            "false_auto_match_rate": false_auto_match_rate,
            "true_positives": true_positives, "false_positives": false_positives,
            "true_negatives": true_negatives, "false_negatives": false_negatives,
        }


if __name__ == "__main__":
    evaluate()
