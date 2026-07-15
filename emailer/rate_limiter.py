"""Sliding-window rate limiter built directly on Redis primitives.

A sorted set holds one member per granted permit, scored with its timestamp
in milliseconds. Granting a permit is three steps:

  1. evict permits older than the window     ZREMRANGEBYSCORE
  2. count what survives                     ZCARD
  3. if under the limit, record this permit  ZADD (+ PEXPIRE housekeeping)

All three run inside a single Lua script. Redis executes a Lua script
atomically - the server is single-threaded and nothing interleaves with it -
which is what makes the check-then-add safe when many Celery workers race
for the same window. MULTI/EXEC could not express this: whether to ZADD
depends on ZCARD's answer *inside* the same atomic unit, and transactions
can't branch on intermediate results.

Why sliding window over the alternatives (full reasoning in DESIGN.md):
fixed window (INCR+EXPIRE) admits up to 2x the limit across a window
boundary, and a token bucket adds refill-state bookkeeping we don't need -
the provider's contract is literally "no more than N calls in any rolling
minute", which is exactly what this structure models.

On denial the script returns how long (ms) until the oldest permit falls out
of the window, so callers can reschedule for the moment a slot actually
frees instead of polling.
"""
import time

import redis as redis_lib

SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local audit  = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

-- Clamp `now` to the newest timestamp this key has seen. Callers read their
-- clock *before* entering the script, so two racing workers can arrive out
-- of order (and separate hosts drift). If a stale `now` ran the trim after a
-- fresher one, permits still inside the stale window would already be gone
-- and the count would come up short - admitting more than the limit. With
-- the clamp, time as this key sees it never goes backwards.
local hwm_key = key .. ':hwm'
local hwm = tonumber(redis.call('GET', hwm_key) or '0')
if now_ms < hwm then
    now_ms = hwm
else
    redis.call('SET', hwm_key, now_ms, 'PX', window * 2)
end

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window)

if redis.call('ZCARD', key) < limit then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window)
    if audit then
        -- untrimmed audit trail, written atomically with the grant so it
        -- records exactly what the limiter enforced
        redis.call('ZADD', audit, now_ms, member)
    end
    return -1
end

local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local retry_after = (tonumber(oldest[2]) + window) - now_ms
if retry_after < 1 then
    retry_after = 1
end
return retry_after
"""


class RateLimiterUnavailable(Exception):
    """Redis could not answer. Callers must fail CLOSED - no permit, no send.

    Blowing through the provider's limit risks hard rejections or account
    suspension; a delayed email is recoverable, an over-limit burst is not.
    """


class SlidingWindowRateLimiter:
    def __init__(self, client, key, limit, window_seconds, clock=time.time,
                 audit_key=None):
        self.key = key
        self.limit = limit
        self.window_ms = int(window_seconds * 1000)
        self.clock = clock
        self.audit_key = audit_key
        self._script = client.register_script(SLIDING_WINDOW_LUA)

    def try_acquire(self, member):
        """Attempt to take one permit.

        Returns (granted, retry_after_seconds). `member` must be unique per
        attempt (ZADD would silently re-score a duplicate member instead of
        adding a new one).
        """
        now_ms = int(self.clock() * 1000)
        keys = [self.key] + ([self.audit_key] if self.audit_key else [])
        try:
            result = self._script(
                keys=keys,
                args=[now_ms, self.window_ms, self.limit, member],
            )
        except redis_lib.RedisError as exc:
            raise RateLimiterUnavailable(str(exc)) from exc

        if result == -1:
            return True, 0.0
        return False, result / 1000.0
