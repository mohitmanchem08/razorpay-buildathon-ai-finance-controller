# Next 10 Steps

1. ✅ DONE: Loading skeletons for Dashboard on first load (shimmer effect,
   dismissed on first successful render or error, verified structurally
   and against live backend)
2. ✅ DONE: Inline error banner (showError()) replaces all 3 alert() popups
   and the previously-silent AI batch auto-run failure case. Verified
   structurally and against live backend.
3. ✅ DONE: Search box on Matched Orders table (order ID/payment ID/customer)
4. ✅ DONE: Copy-to-clipboard buttons on order_id/payment_id in exception cards
5. ✅ DONE: "Last AI analysis" timestamp in sidebar, resets to "Pending for
   this dataset" on new upload/reset before re-analysis completes
6. ✅ DONE: Esc key closes expanded exception cards
7. ✅ DONE: Custom 404/validation/500 error handlers, verified against live
   TestClient (clean JSON instead of default FastAPI error format)
8. ✅ DONE: Full manual browser test pass by Mohit — confirmed clean
9. ✅ DONE: app.py + streamlit dependency removed. All references cleaned
   up in ai_client.py, main.py, README.md. Verified: 32 tests pass, backend
   works, no stale references remain.
10. Animation/motion pass as its own dedicated step -- transitions, hover
    states, loading spinners -- reviewed on its own, not bundled into
    functional work
11. Data persistence + run history (SQLite): STATE currently lives in memory
    only and is lost on server restart, with zero history of past runs.
    Two tiers to decide between when we get here: (a) just survive a
    restart (simple), or (b) full timestamped run history with a UI to
    browse back through past reconciliations (bigger -- schema design,
    migration logic, and a history browsing view, not just a persistence
    swap)

Resume: cd into recon-agent-merged, uvicorn main:app --reload --port 8000
