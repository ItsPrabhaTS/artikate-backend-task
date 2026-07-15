"""Every Redis key the emailer touches, in one place."""

# Sliding-window rate limiter state (sorted set, trimmed by the Lua script).
RATE_LIMITER = "emailer:rate"

# Every provider call ever made (sorted set, member unique per attempt,
# score = ms timestamp). This is the audit trail the tests assert against.
ATTEMPT_LOG = "emailer:attempts"

# Successful sends (sorted set, member = message_id, score = ms timestamp).
SENT = "emailer:sent"

# Permanently failed jobs as JSON blobs (list).
DEAD_LETTER = "emailer:dead_letter"

# Per-message provider attempt counters used by FlakyEmailProvider (hash).
FLAKY_ATTEMPTS = "emailer:flaky_attempts"

ALL = [RATE_LIMITER, ATTEMPT_LOG, SENT, DEAD_LETTER, FLAKY_ATTEMPTS]
