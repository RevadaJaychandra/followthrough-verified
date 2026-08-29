"""Tests for deadline phrase resolution.

Plain asserts, no pytest dependency — run directly:
    python test_deadlines.py

Deadline parsing is the one place in this system where being wrong is worse
than being silent: a mis-parsed phrase produces a false "overdue" blocker and
prompts a human to escalate something that was never late. So the cases that
must return None are tested as carefully as the ones that must return a date.
"""
import datetime
import sys

from deadlines import days_overdue, parse_deadline

# A fixed Wednesday, so weekday arithmetic is deterministic.
WEDNESDAY = datetime.datetime(2026, 8, 26, 12, 0, tzinfo=datetime.timezone.utc)
D = datetime.date

failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: expected {expected}, got {actual}")


# --- weekday names resolve to the NEXT occurrence ---
check("Friday from Wednesday", parse_deadline("Friday", WEDNESDAY), D(2026, 8, 28))
check("by Friday", parse_deadline("by Friday", WEDNESDAY), D(2026, 8, 28))
check("Monday wraps to next week", parse_deadline("Monday", WEDNESDAY), D(2026, 8, 31))
# Said on a Wednesday, "Wednesday" means the coming one, not today.
check("Wednesday on a Wednesday", parse_deadline("Wednesday", WEDNESDAY), D(2026, 9, 2))

# --- relative phrases ---
check("today", parse_deadline("today", WEDNESDAY), D(2026, 8, 26))
check("tomorrow", parse_deadline("tomorrow", WEDNESDAY), D(2026, 8, 27))
check("end of day", parse_deadline("end of day", WEDNESDAY), D(2026, 8, 26))
check("in 3 days", parse_deadline("in 3 days", WEDNESDAY), D(2026, 8, 29))
check("within 2 weeks", parse_deadline("within 2 weeks", WEDNESDAY), D(2026, 9, 9))
check("end of the week", parse_deadline("end of the week", WEDNESDAY), D(2026, 8, 28))
check("iso date", parse_deadline("2026-09-15", WEDNESDAY), D(2026, 9, 15))

# --- must return None rather than guess ---
for phrase in ["unspecified", "", "TBD", "asap", "soon", "when the API is ready",
               "sometime", "n/a"]:
    check(f"no-guess {phrase!r}", parse_deadline(phrase, WEDNESDAY), None)
check("invalid iso date", parse_deadline("2026-02-31", WEDNESDAY), None)

# --- overdue arithmetic ---
# Friday 2026-08-28, evaluated the following Monday, is 3 days late.
MONDAY_AFTER = datetime.datetime(2026, 8, 31, 9, 0, tzinfo=datetime.timezone.utc)
check("3 days overdue", days_overdue("2026-08-28", MONDAY_AFTER), 3)
check("due today is not overdue", days_overdue("2026-08-31", MONDAY_AFTER), 0)
check("future is not overdue", days_overdue("2026-09-30", MONDAY_AFTER), 0)
check("unparseable is None", days_overdue("unspecified", MONDAY_AFTER), None)

if failures:
    print("FAILED:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)

print("deadline parsing OK")
