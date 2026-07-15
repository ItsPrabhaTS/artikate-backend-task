from django.db import models

from .context import require_current_tenant


class TenantManager(models.Manager):
    """Scopes every queryset to the tenant bound to the current context.

    get_queryset() is the one choke point that every manager entry path
    funnels through - .all(), .filter(), .get(), .count(), .values(),
    .exists(), reverse related managers - so filtering here means no call
    site can forget the tenant filter.

    When no tenant is bound this raises instead of silently returning an
    unfiltered (or empty) queryset. Failing loudly is the point: a missing
    tenant context is a programming error, and the alternative - returning
    all rows - is exactly the data leak this design exists to prevent.
    """

    def get_queryset(self):
        tenant = require_current_tenant()
        return super().get_queryset().filter(tenant=tenant)
