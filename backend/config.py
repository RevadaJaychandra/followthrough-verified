"""Central config for the FollowThrough backend.

All secrets are loaded from environment variables (via .env locally, or
Secret Manager env injection on Cloud Run — see deploy.sh). Nothing here
is a real credential; every secret-like value below is either empty by
default or a placeholder that fails validate() with a clear message.
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCP_REGION = os.getenv("GCP_REGION", "us-central1")

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

TOPIC_MEETING_PROCESSED = os.getenv("PUBSUB_TOPIC_MEETING_PROCESSED", "meeting-processed")
TOPIC_TASK_CREATED = os.getenv("PUBSUB_TOPIC_TASK_CREATED", "task-created")
TOPIC_TASK_BLOCKED = os.getenv("PUBSUB_TOPIC_TASK_BLOCKED", "task-blocked")
TOPIC_TASK_VERIFIED = os.getenv("PUBSUB_TOPIC_TASK_VERIFIED", "task-verified")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")


def _parse_user_map(raw: str) -> dict[str, str]:
    """Parse "Kartikeya=0kartik,Rahul=rahul-dev" into a lookup keyed by
    lowercased speaker name. Malformed entries are skipped rather than
    raising, so one typo in .env cannot stop the server from booting."""
    mapping = {}
    for pair in raw.split(","):
        if "=" not in pair:
            continue
        name, username = pair.split("=", 1)
        name, username = name.strip().lower(), username.strip()
        if name and username:
            mapping[name] = username
    return mapping


# Maps transcript speaker names to GitHub usernames so created issues get
# assigned to a real person. Unmapped names produce an unassigned issue —
# GitHub rejects create_issue outright if an assignee does not exist.
GITHUB_USER_MAP = _parse_user_map(os.getenv("GITHUB_USER_MAP", ""))

GMAIL_SENDER = os.getenv("GMAIL_SENDER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
ESCALATION_RECIPIENT = os.getenv("ESCALATION_RECIPIENT", "")

# Set to "true" to run without any live GCP/GitHub/Gmail calls — extraction
# uses a canned response, GitHub/email calls are no-ops that log instead of
# calling out. Lets you test the full request/response wiring and the
# frontend before any real credentials exist.
OFFLINE_MODE = os.getenv("OFFLINE_MODE", "false").lower() == "true"

# Browser origins allowed to call this API. Defaults to the local Vite dev
# server. Set CORS_ALLOWED_ORIGINS to a comma-separated list (or to "*") once
# the dashboard is deployed somewhere. The previous hardcoded "*" meant any
# website a judge happened to have open could drive this API, including the
# escalation-approval endpoints.
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ALLOWED_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",") if o.strip()
]

ALL_TOPICS = [
    TOPIC_MEETING_PROCESSED,
    TOPIC_TASK_CREATED,
    TOPIC_TASK_BLOCKED,
    TOPIC_TASK_VERIFIED,
]

# Values that mean "this is still the placeholder from .env.example" —
# used by validate() to fail with a specific, actionable message instead
# of a confusing downstream 401/403 from Google/GitHub/Gmail.
_PLACEHOLDER_MARKERS = ("xxxx", "your-", "ghp_xxxx", "you@example")


def _looks_like_placeholder(value: str) -> bool:
    v = value.lower()
    return any(marker in v for marker in _PLACEHOLDER_MARKERS)


def validate(require_github=True, require_gmail=True, require_gcp=True) -> list[str]:
    """Returns a list of human-readable problems with current config.
    Empty list means config looks usable. Does not make any network calls."""
    problems = []

    if require_gcp:
        if not GCP_PROJECT_ID:
            problems.append("GCP_PROJECT_ID is not set — copy .env.example to .env and fill it in.")

    if require_github:
        if not GITHUB_TOKEN or _looks_like_placeholder(GITHUB_TOKEN):
            problems.append("GITHUB_TOKEN is missing or still the placeholder value — get one at https://github.com/settings/tokens (repo scope).")
        if not GITHUB_REPO or _looks_like_placeholder(GITHUB_REPO):
            problems.append("GITHUB_REPO is missing or still the placeholder value — set it to 'yourusername/your-demo-repo'.")

    if require_gmail:
        if not GMAIL_SENDER or _looks_like_placeholder(GMAIL_SENDER):
            problems.append("GMAIL_SENDER is missing or still the placeholder value.")
        if not GMAIL_APP_PASSWORD or _looks_like_placeholder(GMAIL_APP_PASSWORD):
            problems.append("GMAIL_APP_PASSWORD is missing or still the placeholder value — generate one at https://myaccount.google.com/apppasswords.")
        if not ESCALATION_RECIPIENT or _looks_like_placeholder(ESCALATION_RECIPIENT):
            problems.append("ESCALATION_RECIPIENT is missing or still the placeholder value — set it to an inbox you can check during the demo.")

    return problems


if __name__ == "__main__":
    # Quick CLI check: `python config.py` prints exactly what's missing.
    problems = validate()
    if not problems:
        print("Config looks complete (values present, not placeholders).")
        print(f"  GCP_PROJECT_ID       = {GCP_PROJECT_ID}")
        print(f"  GCP_REGION           = {GCP_REGION}")
        print(f"  GEMINI_MODEL         = {GEMINI_MODEL}")
        print(f"  GITHUB_REPO          = {GITHUB_REPO}")
        print(f"  ESCALATION_RECIPIENT = {ESCALATION_RECIPIENT}")
        print(f"  GITHUB_USER_MAP      = {GITHUB_USER_MAP or '(none — issues will be unassigned)'}")
        print(f"  CORS_ALLOWED_ORIGINS = {', '.join(CORS_ALLOWED_ORIGINS)}")
        print(f"  OFFLINE_MODE         = {OFFLINE_MODE}")
        print()
        print("Secrets are present but not printed. Nothing here made a network call —")
        print("start the server to see the Gemini model preflight result.")
    else:
        print("Config problems found:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
