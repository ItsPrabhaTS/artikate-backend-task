import random
from decimal import Decimal

from django.core.management.base import BaseCommand

from orders.models import Customer, Order, OrderItem

DEMO_EMAIL = "dashboard.demo@example.com"

PRODUCTS = [
    ("USB-C cable", "9.99"),
    ("Mechanical keyboard", "89.00"),
    ("27\" monitor", "229.50"),
    ("Laptop stand", "34.25"),
    ("Webcam", "59.90"),
    ("Noise-cancelling headphones", "199.00"),
    ("Desk mat", "19.75"),
    ("HDMI adapter", "14.40"),
]


class Command(BaseCommand):
    help = (
        "Seed a demo customer with enough orders (>200) to reproduce the "
        "slow-dashboard incident from section 1."
    )

    def add_arguments(self, parser):
        parser.add_argument("--orders", type=int, default=250)
        parser.add_argument("--fresh", action="store_true",
                            help="Delete previously seeded demo data first.")

    def handle(self, *args, **options):
        # Deterministic data so repeated profiling runs compare like-for-like.
        random.seed(42)

        if options["fresh"]:
            Customer.objects.filter(email=DEMO_EMAIL).delete()

        customer, created = Customer.objects.get_or_create(
            email=DEMO_EMAIL, defaults={"name": "Dashboard Demo"}
        )
        if not created and customer.orders.exists():
            self.stdout.write(
                f"Demo customer already seeded (id={customer.pk}, "
                f"{customer.orders.count()} orders). Use --fresh to re-seed."
            )
            return

        n = options["orders"]
        orders = Order.objects.bulk_create(
            Order(customer=customer, status=random.choice(Order.Status.values))
            for _ in range(n)
        )

        items = []
        for order in orders:
            for _ in range(random.randint(1, 4)):
                name, price = random.choice(PRODUCTS)
                items.append(OrderItem(
                    order=order,
                    product_name=name,
                    unit_price=Decimal(price),
                    quantity=random.randint(1, 3),
                ))
        OrderItem.objects.bulk_create(items)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded customer id={customer.pk} with {n} orders / {len(items)} items.\n"
            f"Broken endpoint: /api/orders/summary-broken/?customer={customer.pk}\n"
            f"Fixed endpoint:  /api/orders/summary/?customer={customer.pk}"
        ))
