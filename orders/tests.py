from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .models import Customer, Order, OrderItem

pytestmark = pytest.mark.django_db

N_ORDERS = 40
ITEMS_PER_ORDER = 3


@pytest.fixture
def customer():
    customer = Customer.objects.create(name="Ana", email="ana@example.com")
    orders = Order.objects.bulk_create(
        Order(customer=customer, status=Order.Status.PAID) for _ in range(N_ORDERS)
    )
    OrderItem.objects.bulk_create(
        OrderItem(
            order=order,
            product_name=f"product-{i}",
            unit_price=Decimal("9.99"),
            quantity=2,
        )
        for order in orders
        for i in range(ITEMS_PER_ORDER)
    )
    return customer


def test_broken_endpoint_query_count_scales_with_orders(client, customer):
    """The regression: 1 base query plus 3 per order (customer FK walk,
    nested items, and get_total's second items fetch)."""
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(f"/api/orders/summary-broken/?customer={customer.pk}")

    assert response.status_code == 200
    assert len(ctx.captured_queries) >= 1 + 3 * N_ORDERS


def test_fixed_endpoint_query_count_is_constant(client, customer, django_assert_num_queries):
    """select_related + prefetch_related + annotate: exactly two queries no
    matter how many orders the customer has."""
    with django_assert_num_queries(2):
        response = client.get(f"/api/orders/summary/?customer={customer.pk}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == N_ORDERS
    expected_total = Decimal("9.99") * 2 * ITEMS_PER_ORDER
    assert Decimal(payload["orders"][0]["total"]) == expected_total


def test_fix_does_not_change_the_response_payload(client, customer):
    broken = client.get(f"/api/orders/summary-broken/?customer={customer.pk}").json()
    fixed = client.get(f"/api/orders/summary/?customer={customer.pk}").json()

    assert fixed["count"] == broken["count"]
    for before, after in zip(broken["orders"], fixed["orders"]):
        assert after["id"] == before["id"]
        assert after["customer_name"] == before["customer_name"]
        assert after["status"] == before["status"]
        assert after["created_at"] == before["created_at"]
        assert after["items"] == before["items"]
        # broken serialises the Python sum, fixed a DB aggregate - compare
        # numerically rather than by string representation
        assert Decimal(str(after["total"])) == Decimal(str(before["total"]))


def test_customer_param_is_required(client):
    assert client.get("/api/orders/summary/").status_code == 400
    assert client.get("/api/orders/summary-broken/").status_code == 400
