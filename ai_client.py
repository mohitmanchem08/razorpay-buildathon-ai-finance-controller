"""
ai_client.py
Single point of contact with the AI provider. Every other file calls
ask_ai(prompt) and doesn't care whether it's Gemini, Claude, or anything
else underneath.

temperature=0.1 is set deliberately low: we observed the same prompt on the
same ambiguous data returning noticeably different answers across separate
runs (one cautious "I'm not sure", one confident guess) during testing. A
low temperature reduces that run-to-run inconsistency, which matters more
in a finance tool than in a creative-writing context.
"""

import os
import re
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

PROVIDER = "gemini"  # change to "claude" later if you switch

# Ordered by preference: try the best/newest model first, fall back to
# older/lighter models with likely higher free-tier daily limits if exhausted.
#
# Known volatility warning: Google renames, deprecates, and replaces free-tier
# models frequently (we've now hit this twice in one project: 2.5-flash and
# 3.6-flash both eventually returned errors telling us to switch models).
# Because of this, treat this list as a best-effort snapshot, not a permanent
# fact -- if every model below starts failing, check the live model list in
# your own Google AI Studio account rather than assuming this list is current.
GEMINI_MODEL_FALLBACK_CHAIN = [
    "gemini-3.6-flash",       # primary target, but tight 20 req/day free cap
    "gemini-3.5-flash",       # current-gen alternative, likely higher quota
    "gemini-3.5-flash-lite",  # lightest current-gen model, highest free quota
]

_client = None
_current_key_index = 0

# In-memory log of every AI call made this session: which model answered,
# whether it took a fallback (model or key) to succeed, and whether it
# ultimately failed entirely. Read by main.py to show a live quota/usage
# indicator in the sidebar -- built from real call outcomes, not a static
# guess at remaining quota (which Google doesn't expose via the API anyway).
call_log = []


def get_usage_summary():
    """
    Returns a simple summary of this session's AI call activity, for
    display in the app's sidebar. Built from real observed outcomes, not
    a prediction -- we deliberately don't claim to know your exact remaining
    quota, since Google doesn't expose that number via the API.
    """
    total = len(call_log)
    successes = sum(1 for c in call_log if c["success"])
    failures = total - successes
    fallbacks = sum(1 for c in call_log if c.get("fallback_occurred"))
    models_used = sorted(set(c["model_used"] for c in call_log if c.get("model_used")))
    return {
        "total_calls": total,
        "successes": successes,
        "failures": failures,
        "fallback_events": fallbacks,
        "models_used": models_used,
    }


def _get_api_keys():
    """
    Reads GEMINI_API_KEYS (comma-separated) if set, falls back to the
    single GEMINI_API_KEY for backward compatibility. Having multiple keys
    (e.g. from different Google accounts) means when one account's daily
    quota is exhausted across every model in the fallback chain, we can
    move to the next account's key instead of failing entirely.
    """
    multi = os.environ.get("GEMINI_API_KEYS")
    if multi:
        keys = [k.strip() for k in multi.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get("GEMINI_API_KEY")
    if single:
        return [single]
    raise RuntimeError(
        "No Gemini API key found. Set GEMINI_API_KEY=your-key, or "
        "GEMINI_API_KEYS=key1,key2 for multiple accounts (see README)."
    )


def _get_gemini_client(key_index=0):
    from google import genai
    keys = _get_api_keys()
    key_index = min(key_index, len(keys) - 1)
    return genai.Client(api_key=keys[key_index])


def _is_recoverable_error(exception):
    """
    Detect errors worth trying the next model (or next key) in the fallback
    chain for: quota exhaustion (429), model-not-found (404, e.g. a
    deprecated model name), and server overload (503, "high demand" -- a
    real, observed Gemini failure mode, not hypothetical). All three mean
    "this specific model/key combination is currently unusable", which is
    exactly when falling through to the next one makes sense. Other errors
    (malformed prompt, auth failure) should surface immediately instead of
    being masked by silently trying every model in the chain.
    """
    msg = str(exception)
    return (
        "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower()
        or "404" in msg or "NOT_FOUND" in msg
        or "503" in msg or "UNAVAILABLE" in msg or "high demand" in msg.lower()
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=15),
    reraise=True,
)
def _call_gemini_model(model_name, prompt, max_tokens, response_schema, key_index=0):
    client = _get_gemini_client(key_index)
    from google.genai import types

    config = types.GenerateContentConfig(
        max_output_tokens=max_tokens,
        temperature=0.1,
    )
    if response_schema:
        config.response_mime_type = "application/json"
        config.response_schema = response_schema

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=config,
    )
    raw_text = response.text.strip()

    if response_schema:
        raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip())

    return raw_text


def ask_ai(prompt, max_tokens=200, response_schema=None, return_model_used=False):
    """
    Send a prompt to the configured AI provider, return plain text.

    On Gemini, automatically falls back through GEMINI_MODEL_FALLBACK_CHAIN
    if a model's daily quota is exhausted (HTTP 429 / RESOURCE_EXHAUSTED) --
    other errors (bad prompt, network issue) still raise normally rather
    than silently trying every model.

    If return_model_used=True, returns (text, model_name) instead of just
    text, so callers can be transparent about which model actually answered.
    """
    if PROVIDER == "gemini":
        num_keys = len(_get_api_keys())
        last_error = None
        attempts = 0
        for key_index in range(num_keys):
            for model_name in GEMINI_MODEL_FALLBACK_CHAIN:
                attempts += 1
                try:
                    text = _call_gemini_model(
                        model_name, prompt, max_tokens, response_schema, key_index=key_index
                    )
                    call_log.append({
                        "success": True,
                        "model_used": model_name,
                        "key_index": key_index,
                        "attempts_needed": attempts,
                        "fallback_occurred": attempts > 1,
                    })
                    return (text, model_name) if return_model_used else text
                except Exception as e:
                    last_error = e
                    if _is_recoverable_error(e):
                        continue  # try the next model, or next key if models exhausted
                    call_log.append({
                        "success": False, "model_used": None, "key_index": key_index,
                        "attempts_needed": attempts, "fallback_occurred": attempts > 1,
                    })
                    raise  # a non-recoverable error should surface immediately
        # every model, across every available key, was unavailable
        call_log.append({
            "success": False, "model_used": None, "key_index": None,
            "attempts_needed": attempts, "fallback_occurred": True,
        })
        raise RuntimeError(
            f"All Gemini models across all {num_keys} configured key(s) were "
            f"unavailable (quota exhausted or deprecated). "
            f"Last error: {last_error}"
        )

    elif PROVIDER == "claude":
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        return (text, "claude-sonnet-4-6") if return_model_used else text

    else:
        raise ValueError(f"Unknown provider: {PROVIDER}")
