"""Dead-letter store for jobs that exhausted their retries.

A Redis list of JSON blobs. Nothing is ever silently dropped: a job either
ends in the sent log or here, where it can be inspected and requeued with
`python manage.py dead_letter --requeue`.
"""
import json
import time

from . import keys
from .redis_client import get_redis


def push(message, error, attempts):
    entry = {
        "failed_at": time.time(),
        "attempts": attempts,
        "error": error,
        "message": message,
    }
    get_redis().lpush(keys.DEAD_LETTER, json.dumps(entry))


def entries():
    return [json.loads(raw) for raw in get_redis().lrange(keys.DEAD_LETTER, 0, -1)]


def pop():
    """Take the oldest entry, or None. RPOP because LPUSH makes it a FIFO."""
    raw = get_redis().rpop(keys.DEAD_LETTER)
    return json.loads(raw) if raw else None
