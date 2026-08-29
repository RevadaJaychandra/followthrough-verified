"""Thin wrapper around Pub/Sub for the event-driven decoupling between agents.

Usage:
  publish(config.TOPIC_MEETING_PROCESSED, {"meeting_id": "..."})

For the hackathon demo, subscribers are implemented as simple pull-based
listeners started as background threads in main.py (see run_subscriber).
A push subscription -> Cloud Run endpoint is the "production" version;
noted in the architecture diagram as the deploy target, pull is used
locally/in-demo to avoid needing a public HTTPS endpoint during dev.

When config.OFFLINE_MODE is true, real Pub/Sub is never touched — an
in-memory queue + handler dispatch simulates the same publish/subscribe
behavior (including the async decoupling) so the full pipeline can be
tested with zero GCP credentials. Swap OFFLINE_MODE off and the exact
same publish()/run_subscriber() calls hit real Pub/Sub, no other code
changes needed.
"""
import json
import queue
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError

import config

_publisher = None
_subscriber = None

# ---------- offline in-memory backend ----------
_mem_queues: dict[str, "queue.Queue"] = {}


def _mem_queue(topic_name: str) -> "queue.Queue":
    if topic_name not in _mem_queues:
        _mem_queues[topic_name] = queue.Queue()
    return _mem_queues[topic_name]


def publisher():
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def subscriber():
    global _subscriber
    if _subscriber is None:
        from google.cloud import pubsub_v1
        _subscriber = pubsub_v1.SubscriberClient()
    return _subscriber


def topic_path(topic_name: str) -> str:
    return publisher().topic_path(config.GCP_PROJECT_ID, topic_name)


def subscription_path(topic_name: str) -> str:
    sub_name = f"{topic_name}-sub"
    return subscriber().subscription_path(config.GCP_PROJECT_ID, sub_name)


def ensure_topics_and_subscriptions():
    """Idempotent setup — create topics + pull subscriptions if missing."""
    if config.OFFLINE_MODE:
        for topic_name in config.ALL_TOPICS:
            _mem_queue(topic_name)  # just ensures it exists
        print("[pubsub][offline] in-memory queues ready")
        return

    for topic_name in config.ALL_TOPICS:
        tp = topic_path(topic_name)
        try:
            publisher().create_topic(name=tp)
            print(f"[pubsub] created topic {topic_name}")
        except Exception:
            pass  # already exists

        sp = subscription_path(topic_name)
        try:
            subscriber().create_subscription(name=sp, topic=tp)
            print(f"[pubsub] created subscription {topic_name}-sub")
        except Exception:
            pass  # already exists


def publish(topic_name: str, payload: dict):
    if config.OFFLINE_MODE:
        _mem_queue(topic_name).put(payload)
        print(f"[pubsub][offline] published to {topic_name}: {payload}")
        return

    data = json.dumps(payload).encode("utf-8")
    future = publisher().publish(topic_path(topic_name), data)
    future.result(timeout=10)
    print(f"[pubsub] published to {topic_name}: {payload}")


def run_subscriber(topic_name: str, handler):
    """Run a blocking pull subscriber in a background thread.

    handler: callable(payload: dict) -> None
    """
    if config.OFFLINE_MODE:
        def _mem_loop():
            q = _mem_queue(topic_name)
            print(f"[pubsub][offline] listening on {topic_name}")
            while True:
                payload = q.get()  # blocks until publish() puts something
                try:
                    handler(payload)
                except Exception as e:
                    print(f"[pubsub][offline] handler error on {topic_name}: {e}")

        t = threading.Thread(target=_mem_loop, daemon=True)
        t.start()
        return t

    def _callback(message):
        try:
            payload = json.loads(message.data.decode("utf-8"))
            handler(payload)
            message.ack()
        except Exception as e:
            print(f"[pubsub] handler error on {topic_name}: {e}")
            message.nack()

    def _run():
        sp = subscription_path(topic_name)
        future = subscriber().subscribe(sp, callback=_callback)
        print(f"[pubsub] listening on {topic_name}")
        try:
            future.result()
        except FuturesTimeoutError:
            future.cancel()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
