from rest_framework import serializers

from .models import Order, OrderItem


class OrderItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderItem
        fields = ["product_name", "unit_price", "quantity"]


class BrokenOrderSummarySerializer(serializers.ModelSerializer):
    """The serializer as it looked after the bad deployment.

    Three separate per-order queries hide in here:
      - `customer_name` walks the FK -> one SELECT per order
      - the nested `items` field evaluates order.items.all() -> one SELECT per order
      - `get_total` builds a *new* queryset via order.items.all() (related managers
        return a fresh queryset each call, so the previous fetch is not reused)
        -> one more SELECT per order

    For a customer with N orders the endpoint runs 1 + 3N queries.
    """

    customer_name = serializers.CharField(source="customer.name", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    total = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ["id", "customer_name", "status", "created_at", "items", "total"]

    def get_total(self, order):
        return sum(item.unit_price * item.quantity for item in order.items.all())


class OrderSummarySerializer(serializers.ModelSerializer):
    """Same payload, but every field is satisfied from the view's queryset:

      - `customer_name` comes from the select_related JOIN
      - `items` reads the prefetch_related cache
      - `total` is a database aggregate exposed as an annotation
    """

    customer_name = serializers.CharField(source="customer.name", read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Order
        fields = ["id", "customer_name", "status", "created_at", "items", "total"]
