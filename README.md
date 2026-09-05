# Razorpay AI Finance Controller

**Track 4 — AI Finance Controller** · Razorpay AI Buildathon 2026

An automated reconciliation agent that matches merchant order records against payment settlement records, classifies every discrepancy with a clear reason, and uses AI selectively — only on genuinely ambiguous cases — to add a human-analyst-style explanation.

## What this does

Every finance team has to answer one boring, high-stakes question every day: **did every order actually get paid, and by how much?** This tool automates that reconciliation loop:

1. Loads two datasets — orders and settlements
2. Matches them using deterministic rules (exact match, fee tolerance, date drift)
3. Classifies anything that doesn't match cleanly into a specific, honest exception category
4. Calls an AI model **only** on the genuinely ambiguous cases, to suggest a plain-English explanation
5. Exports a full audit trail — every row, matched or not, with a reason and a recommended action

## Current results (on the included 70-order synthetic dataset)

| Metric | Value |
|---|---|
| Match rate | **71.4%** (50 / 70 orders) |
| Exceptions | 20 |
| Orphan settlements | 3 |
| Value matched | ₹50,700 |
| Value stuck in exceptions | ₹24,550 |

This number is reported honestly, not optimized to look perfect. A 100% match rate on real-world-style messy data would be a red flag, not an achievement — see [Design Philosophy](#design-philosophy) below.

## Why AI is used selectively, not everywhere

Of the 20 exceptions, only **4** are ambiguous enough to warrant an AI call. The other 16 (duplicates, missing settlements, clear partial payments) already have a confident, deterministic explanation from rule-based logic — calling an LLM on those would add cost and hallucination risk for zero benefit. This is a deliberate design choice, not a limitation: **the AI is reserved for cases where a rule genuinely can't decide, the way a human analyst's judgment is reserved for cases a checklist can't resolve.**

## Architecture

```
generate_data.py   → produces realistic, intentionally messy synthetic data
matcher.py         → deterministic matching engine (exact, fee-tolerant,
                      date-drift, fuzzy-reference, and opt-in batch-settlement
                      matching, plus rule-based recommended actions)
ai_client.py        → AI provider abstraction (Gemini as the primary, tested
                      implementation; a Claude code path exists but is
                      untested in this project) with automatic model/key fallback
ai_explainer.py     → AI-generated structured explanations, with confidence-
                      gated escalation to a human reviewer below 70% confidence
qa_agent.py         → grounded Q&A over the reconciliation results
main.py             → FastAPI backend
static/index.html   → Razorpay-themed HTML/JS dashboard, the single frontend
test_matcher.py     → automated test suite (33 tests) for the core matching logic
evaluate_accuracy.py → precision/recall check against independently hand-labeled
                      ground truth (not derived from the matcher's own labels)
```

### One tested frontend, backed by tested core logic

`static/index.html` (served via `main.py`'s FastAPI backend) calls the exact
same `matcher.py`, `ai_explainer.py`, and `qa_agent.py` used throughout this
project's test suite — no separate, duplicated frontend logic. A Streamlit
UI (`app.py`) existed earlier in development as a faster way to iterate on
backend logic before the HTML frontend was built; it was removed once the
HTML frontend was fully live-tested end to end (upload, AI batch, Q&A, CSV
export, sorting) and confirmed working, since maintaining two frontends
going forward would only add drift risk with no real benefit.

### Matching modes, in the order they're tried

1. **Exact reference match** — same order_id/utr_ref, amount, and date
2. **Fee-tolerant match** — amount differs within the documented 1.5%–3.6% range
3. **Date-drift match** — settled up to 3 days after the order, same amount
4. **Fuzzy reference match** *(new)* — only tried when NO exact reference match
   exists; catches typos/formatting differences (e.g. "ORD_O101" vs "O101")
   using Levenshtein similarity. **Never auto-matched** — always flagged as an
   exception requiring human confirmation, since silently guessing on a
   string-similarity basis is a real risk in a finance tool.
5. **Batch settlement match** *(new, opt-in)* — only activates if the input
   CSV has an optional `batch_id` column; groups orders sharing a batch and
   verifies their summed amount against a single settlement within normal
   fee tolerance. Also always flagged for human confirmation, never auto-matched.
   Files without a `batch_id` column are completely unaffected — this was
   verified with a dedicated test.

### Key design decisions

- **Money-adjacent decisions stay in code, not the AI.** Matching logic, fee tolerances, and recommended actions are all deterministic and auditable. The AI's role is narrower: explaining genuinely ambiguous cases in plain English, never deciding matched vs. unmatched.
- **No LLM arithmetic.** All totals and counts (match rate, value stuck in exceptions, category breakdowns) are computed in Python and handed to the AI as stated facts. LLMs can silently get arithmetic wrong on longer lists — this removes that risk entirely rather than hoping the model gets it right.
- **Confidence scores reflect real uncertainty, but are AI self-assessed, not independently calibrated.** These are the model's own stated confidence, not validated against a labeled ground-truth set — worth being explicit about, since "confidence: 0.85" from an LLM is a useful signal, not a guaranteed accuracy rate. They still visibly vary in a meaningful way: a clear gateway-fee case scores ~0.95, while a genuinely unclear amount mismatch scores ~0.4 with an honest "cannot be definitively determined" rationale, instead of a fake, uniformly high confidence.
- **Automatic model/key fallback, made visible.** Gemini's free tier has real, tight, and frequently-changing limits. This tool automatically falls back across multiple models and API keys if one is quota-exhausted or temporarily overloaded — but the UI always shows *which* model actually answered, rather than silently swapping models without saying so.

## Design Philosophy

> "One cherry-picked match proves nothing." — Track 4 brief

This tool is built around that sentence. It doesn't chase a perfect-looking match rate. It reports the real number, breaks down *why* each exception failed to match, and is honest when even the AI layer is uncertain about a cause. The synthetic dataset deliberately includes 7 realistic edge cases (exact matches, fee deductions, date drift, duplicate settlements, missing settlements, orphan settlements, and partial payments) so the matching logic has real problems to solve, not a trivial 1:1 dataset.

## Known limitations (stated honestly, not hidden)

- **Fee tolerance range (1.5%–3.6%)** is an industry-typical approximation, not sourced from Razorpay's exact published rates (which aren't publicly available). Documented here rather than presented as authoritative.
- **AI batch analysis runs on load**, which uses real API quota — Gemini's free tier is tight (as low as 20 requests/day on the newest model), which is why multi-model and multi-key fallback exists.
- **No production infrastructure** (Docker, async processing, a real database) by deliberate choice — this is a demo-scale prototype on a ~70-row dataset, and adding infrastructure for a scale problem that doesn't exist here would be over-engineering, not rigor.

## Running it locally

```bash
pip install -r requirements.txt
cp .env.example .env   # then add your Gemini API key(s)
python3 -m pytest test_matcher.py -v   # run the test suite (no API key needed)
python3 -m uvicorn main:app --reload --port 8000   # then open http://127.0.0.1:8000
```

`GEMINI_API_KEYS` in `.env` accepts a comma-separated list of keys for automatic fallback across multiple accounts if you have them; a single `GEMINI_API_KEY` also works.

## Evaluation Results

**Demo dataset (included, `data/orders.csv` + `data/settlements.csv`, seed=42):**

| Metric | Value |
|---|---|
| Total orders | 70 |
| Matched automatically | 50 (71.4%) |
| Exceptions (all categorized, verified to sum correctly — `categories_reconcile: True`) | 20 |
| — Missing settlement | 8 |
| — Duplicate settlement | 7 |
| — Unresolved amount/date mismatch | 4 |
| — Partial payment | 1 |
| Orphan settlements | 4 |
| Value matched | ₹50,700 |
| Value in exceptions | ₹24,550 |

**Held-out stress dataset (different random seed=123, generated fresh, rules NOT tuned against it — a genuine out-of-sample check, not the same data re-measured):**

| Scale | Time | Throughput | Match rate |
|---|---|---|---|
| 1,000 records | 56ms | ~18,000 records/sec | 84.1% |
| 5,000 records | 767ms | ~6,500 records/sec | 84.3% |
| 10,000 records | 3.5s | ~2,890 records/sec | 84.6% |

**Honest limitation, measured not hidden:** throughput degrades as record count grows. A real performance bug was found and partially fixed during development: the fuzzy-matching pass was rebuilding its candidate list from scratch on every unresolved order instead of once, causing unnecessary repeated work at scale. Fixing that improved 10k-record throughput by ~16% (2,483 → 2,890 records/sec), but profiling after the fix shows the remaining bottleneck is still inside `reconcile()`'s own per-order filtering, not in the Levenshtein comparison itself — a proper fix would need a different data structure (removing claimed settlements from a mutable working list, rather than filtering a copy fresh each time), which wasn't attempted this close to deadline. At this project's stated demo scope (dozens to low thousands of records), current performance is adequate; it is not claimed to scale further without further work.

**Accuracy evaluation against independently hand-labeled ground truth:** `evaluate_accuracy.py` checks the matcher against 9 test cases where the correct outcome (should auto-match or should be an exception) was decided *before* running the matcher, covering the main decision boundaries: exact match, in-tolerance fee, in-tolerance date drift, missing settlement, way-outside-tolerance amount, partial payment, ambiguous mismatch, and duplicate settlement. Result: **0 false auto-matches, 100% precision, 100% recall** on this set. This is deliberately described as a small, real sanity check on core decision boundaries, not a statistically comprehensive accuracy claim — a larger independently-labeled set would be needed for that. Run it yourself: `python3 evaluate_accuracy.py`.

**What has NOT been measured, stated plainly:** false-exception rate on a large, independently-labeled dataset (the 9-case set above is real but small). The rule-based matching logic is deterministic and its behavior is fully covered by the 33-test suite (`test_matcher.py`) plus the accuracy harness, which is the actual evidence of correctness presented here.

## Architecture

```
CSV input (orders + settlements)
        │
        ▼
Deterministic matching engine (matcher.py)
  exact → fee-tolerant → date-drift → fuzzy-reference → batch-settlement
        │                                    │
   matched                              exception
        │                                    │
        │                          structured match_type +
        │                          rule-based recommended_action
        │                                    │
        │                          ┌─────────┴──────────┐
        │                    genuinely ambiguous    already explained
        │                    (4 of 20 cases)        by rules alone
        │                          │                     │
        │                    AI explainer            (no AI call)
        │                    (ai_explainer.py)
        │                          │
        │                  confidence < 70%?
        │                          │
        │                    ┌─────┴─────┐
        │                   yes          no
        │                    │            │
        │            ESCALATE_TO_    AI category +
        │            SENIOR_ANALYST  rationale shown
        │                    │            │
        └────────────────────┴────────────┘
                             │
                       Human review
                    (dashboard checkboxes)
                             │
                             ▼
                   Audit CSV export
        (match type, action, AI category/confidence, reviewed)
```

## Track 4 Compliance Summary

- **One closed finance-ops loop:** multi-source payment reconciliation (orders vs. settlements), end to end — match → categorize → recommend action → (selectively) AI-explain → audit export.
- **Dataset size:** 70 records in the included demo; stress-tested up to 10,000 records on a held-out, differently-seeded dataset (see Evaluation Results above).
- **Measured accuracy:** 71.4% automated match rate on the demo dataset, reported honestly rather than optimized to look higher, plus 100% precision/recall on an independently hand-labeled 9-case ground-truth set (see Evaluation Results). Every unmatched order/settlement is categorized with a specific, verifiable reason, and `categories_reconcile: True` confirms every exception is accounted for.
- **Exceptions:** every exception has a structured `match_type` code, a rule-based `recommended_action`, and — for genuinely ambiguous cases only — an AI-generated category, confidence score, and rationale.
- **Audit trail:** full CSV export (timestamped, includes match type, recommended action, AI category/confidence, and review status).
- **Selective AI use:** AI is called only for the subset of exceptions the deterministic rules can't confidently resolve (4 of 20 on the demo dataset) — visibly reported in the UI, confirmed by design in `ai_explainer.py`'s docstrings and `run_pipeline.py`'s explicit `AI_ELIGIBLE_MARKER` filter.

## What broke during development (and how it was fixed)

Documented honestly rather than glossed over — a real part of the engineering process:

1. **Deprecated SDK and model name.** Started on `google-generativeai` with `gemini-2.5-flash`; both were deprecated mid-build. Fixed by migrating to the current `google-genai` SDK and updating the model name based on the actual error message returned.
2. **Silent empty AI responses.** An edge case where a schema-constrained call succeeded with no exception raised, but returned empty text — originally showed up as a blank result with no explanation. Fixed by explicitly checking for empty responses and surfacing a clear error instead of silence.
3. **Streamlit execution-order bug.** The CSV export button was defined earlier in the script than the AI batch-processing button, so on the same click that ran AI analysis, the exported CSV reflected the *previous* state, missing the fresh results. Fixed by moving the download button to render last, after all tab content.
4. **Real Gemini failure modes hit during testing, not simulated:** daily quota exhaustion (429), a fully deprecated model (404), and server overload (503) — all encountered live while building. Led directly to the multi-model, multi-key automatic fallback system, with transparent reporting of which model ultimately answered.
5. **An arbitrary similarity threshold that would never fire.** When adding fuzzy reference matching, an initial 0.90 similarity threshold was found — through direct testing, not assumption — to be unreachable for any genuine single-character typo on this dataset's short order IDs (a real typo scored only ~0.75). Fixed by empirically measuring real typo vs. unrelated-ID scores and setting the threshold in the actual gap between them (0.70), with the reasoning documented in code rather than left as a magic number.
6. **A truncation bug that silently broke a downstream feature.** The Q&A layer's prompt-injection guard truncated all exception reason text to 60 characters — fine for short fields, but it was silently cutting off the fuzzy/batch match explanations before the AI ever saw words like "typo" or "batch," making the Q&A agent unable to discuss those exception types at all. Caught by directly checking the actual context string rather than assuming the feature worked. Fixed by giving system-generated explanation text its own, much longer length limit, separate from short user-supplied fields.
7. **The same class of truncation bug found again, live, in a different function.** The plain-text AI explainer fallback had `max_tokens=150`, too tight for current-generation reasoning models that spend tokens on internal "thinking" before writing the visible answer — the UI showed a genuinely broken fragment ("The transaction") instead of a full sentence. Found by inspecting a live screenshot of the running app, not assumed. Fixed by raising the limit and adding a safety check that catches any future truncation (a short response with no ending punctuation) and routes it through the honest fallback path instead of displaying a broken fragment.
8. **A real O(n²)-shaped performance bug, found by profiling, not guessing.** Initial fuzzy-matching code rebuilt its full candidate list by scanning every settlement on every single unresolved order, even though nothing about the settlement pool changes between most iterations. `cProfile` showed ~90% of total `reconcile()` time at 10,000 records was inside this rebuild, not in the Levenshtein string comparison it seemed reasonable to blame first. Fixing it (precompute once, filter cheaply per lookup) improved 10k-record throughput by ~16% — a real, modest, honestly-reported gain, not a claimed fix for the full bottleneck (profiling after the fix shows more remains — see Evaluation Results).

## What I'd build next with more time

- Real retrieval instead of full-context-in-prompt for the Q&A layer, if scaling beyond a few hundred records
- SQLite persistence for review state and AI results, so they survive a server restart (currently in-memory only — a real limitation for anything beyond a single local demo session, though acceptable for this project's stated scope)
- A larger, independently-labeled ground-truth set (hundreds of cases, not 9) for a statistically rigorous precision/recall figure — the current `evaluate_accuracy.py` result (100% on 9 cases) is real evidence on the core decision boundaries, not a comprehensive claim
- Fix the remaining performance bottleneck properly: replace the per-order candidate-list filtering in fuzzy matching with a mutable working set that settlements are removed from as they're claimed, rather than filtering a fresh copy on every lookup
- Integration testing against Razorpay's actual settlement report format, if given API/sandbox access
