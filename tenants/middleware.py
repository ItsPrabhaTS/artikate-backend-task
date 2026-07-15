from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import JsonResponse

from .context import TenantContextError, bind_tenant, unbind_tenant
from .models import Tenant


class TenantMiddleware:
    """Binds the request's tenant to the context for the request lifetime.

    Resolution order:
      1. ``X-Tenant`` header carrying the tenant slug (in production this
         would be a verified JWT claim; the lookup line is the only thing
         that would change)
      2. subdomain - ``acme.example.com`` resolves the tenant ``acme``

    Requests without a resolvable tenant proceed *unbound*: public endpoints
    (admin, the section 1 API, silk) don't need one, and if an unbound request
    touches tenant-scoped data the manager raises TenantContextError, which is
    translated to a 403 below - fail closed, never leak.

    The bind is reset in a ``finally`` using the contextvar Token, so a tenant
    can never bleed into the next request handled by the same worker thread.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self.async_mode:
            return self.__acall__(request)

        tenant, error = self._resolve_tenant(request)
        if error is not None:
            return error
        token = bind_tenant(tenant) if tenant else None
        try:
            return self.get_response(request)
        finally:
            if token is not None:
                unbind_tenant(token)

    async def __acall__(self, request):
        tenant, error = self._resolve_tenant(request)
        if error is not None:
            return error
        token = bind_tenant(tenant) if tenant else None
        try:
            return await self.get_response(request)
        finally:
            if token is not None:
                unbind_tenant(token)

    def process_exception(self, request, exception):
        if isinstance(exception, TenantContextError):
            return JsonResponse({"detail": str(exception)}, status=403)
        return None

    def _resolve_tenant(self, request):
        """Returns (tenant, error_response); at most one is non-None."""
        slug = request.headers.get("X-Tenant") or self._subdomain(request)
        if not slug:
            return None, None
        try:
            return Tenant.objects.get(slug=slug), None
        except Tenant.DoesNotExist:
            # An explicitly named but unknown tenant is a hard error, not an
            # anonymous request.
            return None, JsonResponse({"detail": "Unknown tenant."}, status=403)

    @staticmethod
    def _subdomain(request):
        host = request.get_host().split(":")[0]
        parts = host.split(".")
        return parts[0] if len(parts) >= 2 else None
