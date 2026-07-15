from django.db import models

from .context import TenantContextError, get_current_tenant, require_current_tenant
from .managers import TenantManager


class Tenant(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.slug


class TenantScopedModel(models.Model):
    """Base class for anything that must never cross tenant boundaries.

    Two managers, with very different jobs:

    * ``objects`` (default) - TenantManager, always filtered, raises when no
      tenant is bound. This is what application code uses.
    * ``unscoped`` - a plain Manager and the *base* manager
      (Meta.base_manager_name). Django's internals - cascade deletion,
      related-object descriptors - need an unfiltered manager to function
      correctly; making that explicit also gives ops/admin code a deliberate,
      greppable escape hatch instead of a workaround.
    """

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)

    objects = TenantManager()
    unscoped = models.Manager()

    class Meta:
        abstract = True
        default_manager_name = "objects"
        base_manager_name = "unscoped"

    def save(self, *args, **kwargs):
        # Writes are scoped too: fill the tenant from context when missing,
        # and refuse a write whose explicit tenant contradicts the context.
        if self.tenant_id is None:
            self.tenant = require_current_tenant()
        else:
            current = get_current_tenant()
            if current is not None and self.tenant_id != current.pk:
                raise TenantContextError(
                    f"Attempted to write a row for tenant id={self.tenant_id} "
                    f"while tenant '{current.slug}' is bound to the context."
                )
        super().save(*args, **kwargs)


class Order(TenantScopedModel):
    """Deliberately shares its name with orders.Order (section 1): the two
    sections are independent apps, and this one exists to demonstrate
    scoping, so the scaffold's naming is kept as-is."""

    reference = models.CharField(max_length=40)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(TenantScopedModel.Meta):
        abstract = False

    def __str__(self):
        return self.reference
