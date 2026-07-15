import redis
from django.conf import settings

_client = None


def get_redis():
    """Process-wide client; redis-py pools connections internally."""
    global _client
    if _client is None:
        _client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client
