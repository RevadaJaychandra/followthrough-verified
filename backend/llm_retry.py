"""Retrying Gemini's capacity errors.

Gemini answers 503 UNAVAILABLE ("this model is currently experiencing high
demand") and 429 RESOURCE_EXHAUSTED under load, independently of whether the
request is correct. Both happened repeatedly while bringing this project up
live, and each time the symptom was the same: an agent silently produced
nothing and a commitment stalled with no explanation.

These are worth retrying. A 400 or a 404 is not — that is a bug or a bad model
id, and retrying just delays the real error.
"""
import asyncio
import time

# Substrings that mean "busy, try again", not "you asked wrongly".
_TRANSIENT_MARKERS = ("503", "unavailable", "429", "resource_exhausted",
                      "500", "internal", "deadline", "timeout",
                      "high demand", "overloaded")


def is_transient(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def describe(exc: Exception) -> str:
    """A short, human-readable reason suitable for showing in the UI."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return ("Gemini rate limit reached for this model. On the AI Studio free "
                "tier that is 20 requests per day per model — either wait, point "
                "GEMINI_MODEL_* at a different model, or enable billing.")
    if "503" in text or "high demand" in text.lower():
        return "Gemini is temporarily overloaded for this model. Try again in a moment."
    return text[:300]


async def with_retry_async(fn, *, label: str, attempts: int = 3, base_delay: float = 2.0):
    """Await fn(), retrying transient model errors with exponential backoff.

    Re-raises the last exception if every attempt fails, so the caller decides
    how to degrade.
    """
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except Exception as e:
            if not is_transient(e) or attempt == attempts:
                raise
            print(f"[llm] {label}: transient error (attempt {attempt}/{attempts}), "
                  f"retrying in {delay:.0f}s — {str(e)[:120]}")
            await asyncio.sleep(delay)
            delay *= 2


def with_retry(fn, *, label: str, attempts: int = 3, base_delay: float = 2.0):
    """Synchronous counterpart, for the plain generate_content call path."""
    delay = base_delay
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            if not is_transient(e) or attempt == attempts:
                raise
            print(f"[llm] {label}: transient error (attempt {attempt}/{attempts}), "
                  f"retrying in {delay:.0f}s — {str(e)[:120]}")
            time.sleep(delay)
            delay *= 2
