"""Turning the deadline people actually say out loud into a real date.

Meeting transcripts do not contain ISO dates. They contain "by Friday",
"end of the week", "tomorrow", "next Tuesday". The extraction agent faithfully
records that phrase, which is the right thing to store — it is what was said —
but the monitoring agent cannot compare a string to `now`.

This module is the bridge. It resolves the phrases that actually show up in
standups, and returns None for anything it cannot resolve confidently. None is
a first-class answer here: guessing a deadline would produce false "overdue"
escalations, which is far worse than staying quiet.
"""
import datetime
import re

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

# Phrases that carry no resolvable date. Matching these explicitly (rather
# than letting them fall through) keeps the "unparseable" log quiet.
_NO_DEADLINE = {"unspecified", "none", "n/a", "tbd", "asap", "", "no deadline"}


def parse_deadline(phrase: str, now: datetime.datetime | None = None) -> datetime.date | None:
    """Resolve a spoken deadline phrase to a date, or None if not confident.

    Weekday names resolve to the *next* occurrence of that weekday, which is
    how people mean them in a standup: on Wednesday, "by Friday" means this
    Friday; on Friday, "by Friday" means the next one, not today.
    """
    if not phrase:
        return None

    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.date()
    text = phrase.strip().lower()

    if text in _NO_DEADLINE:
        return None

    if "today" in text or "end of day" in text or text == "eod":
        return today
    if "tomorrow" in text:
        return today + datetime.timedelta(days=1)

    # "in 3 days", "within 2 weeks"
    m = re.search(r"\b(?:in|within)\s+(\d+)\s+(day|week)s?\b", text)
    if m:
        n = int(m.group(1))
        return today + datetime.timedelta(days=n * (7 if m.group(2) == "week" else 1))

    if "end of the week" in text or "end of week" in text or text == "eow":
        # Friday of the current week.
        return today + datetime.timedelta(days=(4 - today.weekday()) % 7)

    if "next week" in text:
        return today + datetime.timedelta(days=(7 - today.weekday()) + 4)

    for name, index in _WEEKDAYS.items():
        if re.search(rf"\b{name}\b", text):
            ahead = (index - today.weekday()) % 7
            # "Friday" said on a Friday means the coming Friday, not today.
            return today + datetime.timedelta(days=ahead or 7)

    # An explicit ISO date, if the extraction model happened to emit one.
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    return None


def days_overdue(phrase: str, now: datetime.datetime | None = None) -> int | None:
    """How many days past its deadline a commitment is.

    Returns None when the deadline cannot be resolved, 0 when it is due today
    or still in the future, and a positive count once it has slipped.
    """
    deadline = parse_deadline(phrase, now)
    if deadline is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    delta = (now.date() - deadline).days
    return max(delta, 0)
