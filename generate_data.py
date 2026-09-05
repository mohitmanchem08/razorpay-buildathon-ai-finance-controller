"""
generate_data.py
Generates two realistic, intentionally messy CSVs for the reconciliation demo:
- data/orders.csv       : the merchant's internal order records
- data/settlements.csv  : Razorpay's settlement (payout) records

The mess is deliberate and documented, so the reconciliation engine has
real edge cases to solve instead of a trivial 1:1 match.

Edge cases baked in:
1. Exact matches (majority of rows)
2. Fee-deducted matches      -> settlement amount is slightly less than order amount
3. Date-drift matches        -> settlement happens 1-3 days after the order
4. Duplicate settlement rows -> same payment recorded twice (data entry error)
5. Missing settlements       -> order exists, no settlement at all (money owed)
6. Orphan settlements        -> settlement exists with no matching order (shouldn't happen, but does)
7. Partial settlements       -> only part of the order amount was settled (partial refund/split payment)
"""

import csv
import random
from datetime import date, timedelta

random.seed(42)  # reproducible dataset

NUM_ORDERS = 70
START_DATE = date(2026, 8, 1)

orders = []
settlements = []

order_id_counter = 101
payment_id_counter = 501

def next_order_id():
    global order_id_counter
    oid = f"O{order_id_counter}"
    order_id_counter += 1
    return oid

def next_payment_id():
    global payment_id_counter
    pid = f"P{payment_id_counter}"
    payment_id_counter += 1
    return pid

def random_date(base, max_offset=25):
    return base + timedelta(days=random.randint(0, max_offset))

customers = [f"cust_{i:02d}" for i in range(1, 41)]

# Track how many of each case we generate, for an honest README later
case_counts = {
    "exact_match": 0,
    "fee_deducted": 0,
    "date_drift": 0,
    "duplicate_settlement": 0,
    "missing_settlement": 0,
    "orphan_settlement": 0,
    "partial_settlement": 0,
}

for _ in range(NUM_ORDERS):
    oid = next_order_id()
    amount = random.choice([250, 300, 500, 750, 1000, 1200, 1500, 2000, 2500])
    odate = random_date(START_DATE)
    cust = random.choice(customers)

    orders.append({
        "order_id": oid,
        "amount": amount,
        "date": odate.isoformat(),
        "customer_ref": cust,
    })

    case = random.choices(
        ["exact", "fee", "drift", "duplicate", "missing", "partial"],
        weights=[35, 20, 15, 8, 12, 10],
        k=1,
    )[0]

    if case == "exact":
        settlements.append({
            "payment_id": next_payment_id(),
            "amount": amount,
            "date": odate.isoformat(),
            "utr_ref": oid,
        })
        case_counts["exact_match"] += 1

    elif case == "fee":
        fee = round(amount * random.uniform(0.02, 0.036))
        settlements.append({
            "payment_id": next_payment_id(),
            "amount": amount - fee,
            "date": odate.isoformat(),
            "utr_ref": oid,
        })
        case_counts["fee_deducted"] += 1

    elif case == "drift":
        settle_date = odate + timedelta(days=random.randint(1, 3))
        settlements.append({
            "payment_id": next_payment_id(),
            "amount": amount,
            "date": settle_date.isoformat(),
            "utr_ref": oid,
        })
        case_counts["date_drift"] += 1

    elif case == "duplicate":
        pid = next_payment_id()
        settlements.append({
            "payment_id": pid,
            "amount": amount,
            "date": odate.isoformat(),
            "utr_ref": oid,
        })
        # accidental duplicate row, different payment_id, same everything else
        settlements.append({
            "payment_id": next_payment_id(),
            "amount": amount,
            "date": odate.isoformat(),
            "utr_ref": oid,
        })
        case_counts["duplicate_settlement"] += 1

    elif case == "missing":
        # no settlement created at all
        case_counts["missing_settlement"] += 1

    elif case == "partial":
        partial_amount = round(amount * random.uniform(0.4, 0.7))
        settlements.append({
            "payment_id": next_payment_id(),
            "amount": partial_amount,
            "date": odate.isoformat(),
            "utr_ref": oid,
        })
        case_counts["partial_settlement"] += 1

# A few orphan settlements: payments with no matching order (e.g. test transactions,
# or an order record that got lost on the merchant's side)
for _ in range(4):
    settlements.append({
        "payment_id": next_payment_id(),
        "amount": random.choice([400, 600, 900]),
        "date": random_date(START_DATE).isoformat(),
        "utr_ref": f"O{random.randint(900, 999)}",  # references an order that doesn't exist
    })
    case_counts["orphan_settlement"] += 1

random.shuffle(orders)
random.shuffle(settlements)

with open("data/orders.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["order_id", "amount", "date", "customer_ref"])
    writer.writeheader()
    writer.writerows(orders)

with open("data/settlements.csv", "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["payment_id", "amount", "date", "utr_ref"])
    writer.writeheader()
    writer.writerows(settlements)

print(f"Generated {len(orders)} orders and {len(settlements)} settlement rows.\n")
print("Case breakdown (ground truth, for your own reference / README):")
for k, v in case_counts.items():
    print(f"  {k:22s}: {v}")
