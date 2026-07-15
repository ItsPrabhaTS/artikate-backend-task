# Artikate Studio — Backend Assessment

Django + Celery + Redis implementation of the four assessment sections.

| Section | Where | Written answers |
|---|---|---|
| 1 — Diagnose a broken system | `orders/` app | `ANSWERS.md` §1 + `evidence/profiling.md` |
| 2 — Rate-limited async job queue | `emailer/` app | `DESIGN.md` + `ANSWERS.md` §2 |
| 3 — Multi-tenant data isolation | `tenants/` app | `ANSWERS.md` §3 |
| 4 — Written architecture review | — | `ANSWERS.md` §4 (questions A and B) |

## Setup (~3 minutes)

Prerequisites: Python 3.11+, Docker (for Redis only).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d          # starts Redis on localhost:6379
python manage.py migrate
```

## Run the tests

```bash
pytest
```

28 tests. The two `emailer` end-to-end tests need the compose Redis and take
~30s (they push 500 real jobs through a real in-process Celery worker); if
Redis is not reachable they skip with an explanatory message rather than fail.

> Note: the queue tests clear the `emailer:*` and `celery` keys in the Redis
> database they point at. Use the bundled compose Redis, or point `REDIS_URL`
> at a throwaway DB.

## Section 1 — see the incident and the fix

```bash
python manage.py seed_orders       # demo customer with 250 orders
python manage.py profile_summary   # before/after query counts -> evidence/profiling.md
python manage.py runserver
```

With the server running:

* broken endpoint: `http://localhost:8000/api/orders/summary-broken/?customer=1`
* fixed endpoint: `http://localhost:8000/api/orders/summary/?customer=1`
* profiler UI: `http://localhost:8000/silk/` (both requests are recorded there)

## Section 2 — watch the queue live

Terminal 1 (worker — `--pool=threads` also works on Windows):

```bash
celery -A config worker -l info --pool=threads --concurrency=8
```

Terminal 2:

```bash
python manage.py queue_demo --count 120   # burst of jobs incl. deliberate failures
python manage.py queue_stats --watch      # queued / sent / window usage / dead letters
python manage.py dead_letter              # inspect permanently failed jobs
python manage.py dead_letter --requeue    # send them again
```

The demo enqueues a couple of `fail-twice-*` messages (watch them retry with
backoff in the worker log) and one `fail-always-*` message (lands in the
dead-letter queue after 5 attempts). Rate-limit knobs live in
`config/settings.py` (`EMAIL_RATE_LIMIT`, default 200/min) and can be set via
environment variables — e.g. `EMAIL_RATE_LIMIT=20 EMAIL_RATE_WINDOW_SECONDS=10`
makes the throttling obvious within seconds.

## Section 3 — tenant isolation from the shell

```bash
python manage.py shell -c "
from tenants.models import Tenant
Tenant.objects.get_or_create(slug='acme', defaults={'name': 'Acme'})
Tenant.objects.get_or_create(slug='globex', defaults={'name': 'Globex'})"
python manage.py runserver
```

```bash
curl -H "X-Tenant: acme"   http://localhost:8000/api/tenants/orders/   # acme's rows
curl -H "X-Tenant: globex" http://localhost:8000/api/tenants/orders/   # globex's rows
curl                       http://localhost:8000/api/tenants/orders/   # 403 - fails closed
```

The proof of isolation is in `tenants/tests.py`, which tests the negatives:
cross-tenant `.get(pk=...)`, `.objects.all()` without context, cross-tenant
writes, and reverse-FK traversal all fail.

## Layout

```
config/    settings, celery app, root urls
orders/    section 1: models, broken+fixed views, seed/profiling commands, tests
emailer/   section 2: task, Lua rate limiter, providers, dead letter, commands, tests
tenants/   section 3: contextvar tenant context, manager, middleware, tests
evidence/  section 1 profiler output
```
