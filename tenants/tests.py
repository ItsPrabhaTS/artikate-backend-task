from decimal import Decimal

import pytest

from .context import TenantContextError, get_current_tenant, tenant_context
from .models import Order, Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenants():
    acme = Tenant.objects.create(name="Acme Corp", slug="acme")
    globex = Tenant.objects.create(name="Globex", slug="globex")

    with tenant_context(acme):
        Order.objects.create(reference="ACME-1", amount=Decimal("10.00"))
        Order.objects.create(reference="ACME-2", amount=Decimal("20.00"))
    with tenant_context(globex):
        Order.objects.create(reference="GLOBEX-1", amount=Decimal("99.00"))

    return acme, globex


# ---------------------------------------------------------------------------
# (b) .objects.all() does not bypass scoping
# ---------------------------------------------------------------------------

def test_all_returns_only_current_tenant_rows(tenants):
    acme, globex = tenants
    with tenant_context(acme):
        refs = {o.reference for o in Order.objects.all()}
    assert refs == {"ACME-1", "ACME-2"}

    with tenant_context(globex):
        refs = {o.reference for o in Order.objects.all()}
    assert refs == {"GLOBEX-1"}


# ---------------------------------------------------------------------------
# (a) tenant A cannot reach tenant B's data through any ORM entry point
# ---------------------------------------------------------------------------

def test_get_by_pk_cannot_cross_tenants(tenants):
    acme, globex = tenants
    foreign_pk = Order.unscoped.get(reference="GLOBEX-1").pk

    with tenant_context(acme), pytest.raises(Order.DoesNotExist):
        Order.objects.get(pk=foreign_pk)


def test_filter_values_count_exists_are_all_scoped(tenants):
    acme, _ = tenants
    with tenant_context(acme):
        assert not Order.objects.filter(reference="GLOBEX-1").exists()
        assert Order.objects.count() == 2
        assert set(Order.objects.values_list("reference", flat=True)) == {"ACME-1", "ACME-2"}


def test_even_an_explicit_cross_tenant_filter_returns_nothing(tenants):
    """filter(tenant=other) composes with the manager filter into
    `tenant = A AND tenant = B` - contradictory, so nothing leaks."""
    acme, globex = tenants
    with tenant_context(acme):
        assert Order.objects.filter(tenant=globex).count() == 0


def test_reverse_related_manager_is_scoped_too(tenants):
    """tenant_b.order_set uses Order's default manager class, so even FK
    traversal from the other tenant's own row cannot leak its orders."""
    acme, globex = tenants
    with tenant_context(acme):
        assert globex.order_set.count() == 0


# ---------------------------------------------------------------------------
# No context at all: fail loudly, never return everything
# ---------------------------------------------------------------------------

def test_orm_access_without_tenant_context_raises(tenants):
    assert get_current_tenant() is None
    with pytest.raises(TenantContextError):
        Order.objects.all()


def test_unscoped_is_the_only_and_explicit_escape_hatch(tenants):
    assert Order.unscoped.count() == 3


# ---------------------------------------------------------------------------
# Writes are scoped as well
# ---------------------------------------------------------------------------

def test_save_fills_tenant_from_context(tenants):
    acme, _ = tenants
    with tenant_context(acme):
        order = Order.objects.create(reference="ACME-3", amount=Decimal("5.00"))
    assert order.tenant == acme


def test_save_refuses_a_cross_tenant_write(tenants):
    acme, globex = tenants
    with tenant_context(acme), pytest.raises(TenantContextError):
        Order(tenant=globex, reference="SMUGGLED", amount=Decimal("1.00")).save()


def test_save_without_context_or_explicit_tenant_raises(tenants):
    with pytest.raises(TenantContextError):
        Order(reference="ORPHAN", amount=Decimal("1.00")).save()


# ---------------------------------------------------------------------------
# Middleware: binding, isolation across requests, cleanup
# ---------------------------------------------------------------------------

def test_header_scopes_the_request(client, tenants):
    response = client.get("/api/tenants/orders/", headers={"X-Tenant": "acme"})
    assert response.status_code == 200
    refs = {o["reference"] for o in response.json()["orders"]}
    assert refs == {"ACME-1", "ACME-2"}


def test_two_requests_cannot_see_each_others_tenant(client, tenants):
    first = client.get("/api/tenants/orders/", headers={"X-Tenant": "acme"}).json()
    second = client.get("/api/tenants/orders/", headers={"X-Tenant": "globex"}).json()
    assert {o["reference"] for o in first["orders"]} == {"ACME-1", "ACME-2"}
    assert {o["reference"] for o in second["orders"]} == {"GLOBEX-1"}


def test_subdomain_resolves_the_tenant(client, tenants):
    response = client.get("/api/tenants/orders/", HTTP_HOST="globex.testserver")
    assert response.status_code == 200
    assert {o["reference"] for o in response.json()["orders"]} == {"GLOBEX-1"}


def test_unknown_tenant_is_rejected(client, tenants):
    response = client.get("/api/tenants/orders/", headers={"X-Tenant": "mallory"})
    assert response.status_code == 403


def test_request_without_tenant_fails_closed_not_open(client, tenants):
    """No header, no subdomain: the view still runs Order.objects.all(),
    and the correct outcome is 403 - not a 200 with every tenant's rows."""
    response = client.get("/api/tenants/orders/")
    assert response.status_code == 403


def test_middleware_resets_context_after_the_request(client, tenants):
    client.get("/api/tenants/orders/", headers={"X-Tenant": "acme"})
    # The test client runs the middleware stack in this thread; a leaked
    # binding would be visible (and dangerous) right here.
    assert get_current_tenant() is None
