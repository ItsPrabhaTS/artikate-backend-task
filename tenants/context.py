"""Tenant context shared by the middleware and the ORM manager.

Built on contextvars rather than threading.local so the same code is correct
under both sync (WSGI worker threads) and async (ASGI event loop) execution.
ANSWERS.md section 3 covers why thread-locals break in async views.
"""
from contextlib import contextmanager
from contextvars import ContextVar

_current_tenant = ContextVar("current_tenant", default=None)


class TenantContextError(Exception):
    """Raised when tenant-scoped data is touched with no tenant bound."""


def get_current_tenant():
    """The tenant bound to the current context, or None."""
    return _current_tenant.get()


def require_current_tenant():
    tenant = _current_tenant.get()
    if tenant is None:
        raise TenantContextError(
            "No tenant is bound to the current context. Either the request "
            "did not carry a tenant (missing X-Tenant header / subdomain), or "
            "this is a background task or shell session. Use "
            "`with tenant_context(tenant):` to bind one explicitly, or the "
            "`.unscoped` manager if you genuinely need cross-tenant access."
        )
    return tenant


def bind_tenant(tenant):
    """Bind a tenant and return the reset Token (middleware use)."""
    return _current_tenant.set(tenant)


def unbind_tenant(token):
    _current_tenant.reset(token)


@contextmanager
def tenant_context(tenant):
    """Explicit binding for shells, management commands and Celery tasks."""
    token = _current_tenant.set(tenant)
    try:
        yield
    finally:
        _current_tenant.reset(token)
