# Frontend Build Plan — Razorpay AI Finance Controller (Mark 1)

## Status: Backend done & tested. Frontend HTML not yet built.

## Architecture decision (already made, don't revisit)
- FastAPI backend (main.py) — BUILT AND TESTED. 4 real endpoint tests passed:
  /api/reconcile, /api/usage, /api/audit_csv, /api/upload (rejects bad CSV correctly).
- Single HTML file frontend (static/index.html) served by FastAPI, calling the
  above endpoints via fetch(). NOT React/TypeScript — plain HTML/CSS/JS, so it
  can actually be built and verified this session, not left half-tested.
- Rejected: full multi-page React/TypeScript platform with WebSockets, payouts,
  developer settings, role permissions (user requested it, was talked back to
  a scoped version — see conversation). Reasoning: doesn't match Track 4 brief
  scope, too large to verify working in remaining time.

## Structure: persistent sidebar + 3 tab sections (not separate page loads)

### Persistent left sidebar (visible on all 3 sections)
- Orders CSV + Settlements CSV upload (drag/drop or click)
- "Reset to sample data" button
- "Download audit trail (CSV)" button
- Live AI usage tracker: calls made, failures, fallback events, models used
  (reads from GET /api/usage)

### Section 1 — Dashboard
- 4 metric cards: Total Orders, Matched (+match rate %), Exceptions, Orphans
- Match-rate donut chart (matched vs exception vs orphan, by count)
- Exception-category bar chart (duplicate / missing / ambiguous / partial counts)
- Data source: GET /api/reconcile

### Section 2 — Reconciliation (the main work area)
- Sub-tabs: Matched Orders | Exceptions | Orphan Settlements
- Exceptions: expandable rows, confidence bars (signature element) on
  AI-analyzed rows, rule-based recommended_action shown, review checkbox
  (calls POST /api/review), filter (All/Pending/Reviewed)
- "Run AI on all N" button with rate-limit counter (mirrors Streamlit's
  3-manual-reruns cap) — calls POST /api/analyze_batch
- Data source: GET /api/reconcile (same call, different section renders it)

### Section 3 — Insights & Assistant
- Auto-generated "Story of this run" text summary (computed client-side from
  the same /api/reconcile summary data already fetched — no new AI call)
- Detailed exception-category breakdown chart (₹ value per category, not just count)
- "Flagged for attention" shortlist: top 3-5 highest-₹ unresolved exceptions
- Q&A chat interface — calls POST /api/ask

## Design tokens (already researched, confirmed via web search)
- Navy: #012652 (Razorpay's real primary)
- Blue: #0D94FB (Razorpay's real accent)
- Background: #F7F9FC (off-white, not stark white)
- Matched/good: #0F9D6C (emerald)
- Needs-review: #D97706 (amber)
- Text: #1A1F2E (near-black)
- Type: IBM Plex Sans (headings/UI), IBM Plex Mono (ALL numbers, IDs, amounts)
- Signature element: confidence bar (horizontal fill bar next to AI category badge)
- Charts: donut (match rate) + bar (category counts) + bar (category ₹ value)
- No warm-cream/terracotta (Claude's own look), no near-black+neon — those are
  the flagged generic-AI-design defaults per frontend-design skill.

## Build order — PROGRESS TRACKER
1. ✅ DONE, TESTED: static/index.html skeleton (sidebar + 3-tab shell)
2. ✅ DONE, TESTED: sidebar usage tracker wiring (GET /api/usage, real fetch)
3. ✅ DONE, TESTED: Dashboard (4 metric cards + donut chart + bar chart, real data verified consistent)
4. ✅ DONE, TESTED: Reconciliation section — matched/orphans tables, exception cards with
   confidence bars, review checkbox (round-trip tested: toggle on/off via /api/review,
   confirmed persists correctly), filter buttons, batch AI button (UI wired, NOT
   live-tested since it costs real Gemini quota -- needs testing on Mohit's machine)
5. ✅ DONE, TESTED: Insights section — story summary (computed client-side, no new
   AI call), category value bar chart (₹ not counts, verified distinct from
   Dashboard chart), top-5 flagged shortlist, Q&A chat wired to /api/ask
   (endpoint confirmed returns 200 cleanly even without live Gemini access)
6. ⬜ NOT STARTED: Full end-to-end test on Mohit's machine (file upload flow,
   live batch AI call, live Q&A with real answers, CSV download button click,
   VIEWING IN AN ACTUAL BROWSER for the first time -- structural tests passed
   but visual rendering has not been human-verified yet)
7. ⬜ NOT STARTED: Visual self-critique pass per frontend-design skill

## Verification note (important)
Everything built so far has been tested via FastAPI's TestClient: element IDs
confirmed present, API round-trips confirmed working (review toggle on/off,
reconcile data consistency, HTML structural balance). This catches structural
and logic bugs. It does NOT catch visual/CSS bugs (misalignment, overflow,
broken responsive behavior) -- those can only be caught by actually opening
this in a browser, which is what Step 6 is for.

## How to resume: run `uvicorn main:app --reload --port 8000` from
recon-agent-merged/, open http://127.0.0.1:8000 in browser. Steps 1-4 are
built and unit-tested via FastAPI TestClient (see test assertions in session
history) but NOT yet viewed in an actual browser -- that's part of step 6.
