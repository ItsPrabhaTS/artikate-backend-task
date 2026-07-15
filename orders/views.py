from decimal import Decimal

from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Order
from .serializers import BrokenOrderSummarySerializer, OrderSummarySerializer


class BrokenOrderSummaryView(APIView):
    """/api/orders/summary-broken/?customer=<id>

    Reproduction of the incident: the view body is identical to the fixed
    version below — the regression lives entirely in the serializer it uses.
    Kept in the codebase so the before/after profiler comparison stays
    reproducible (see ANSWERS.md, section 1).
    """

    def get(self, request):
        customer_id = request.query_params.get("customer")
        if not customer_id:
            return Response({"detail": "customer query param is required"},
                            status=status.HTTP_400_BAD_REQUEST)

        orders = Order.objects.filter(customer_id=customer_id)
        data = BrokenOrderSummarySerializer(orders, many=True).data
        return Response({"count": len(data), "orders": data})


class OrderSummaryView(APIView):
    """/api/orders/summary/?customer=<id>

    Two queries regardless of order count:
      1. orders JOIN customer (select_related) with the total computed as a
         GROUP BY aggregate (annotate + Sum)
      2. one items query for the whole page (prefetch_related)
    """

    def get(self, request):
        customer_id = request.query_params.get("customer")
        if not customer_id:
            return Response({"detail": "customer query param is required"},
                            status=status.HTTP_400_BAD_REQUEST)

        orders = (
            Order.objects.filter(customer_id=customer_id)
            .select_related("customer")
            .prefetch_related("items")
            .annotate(
                # Coalesce so an order with no items reads 0.00, not null.
                total=Coalesce(
                    Sum(F("items__unit_price") * F("items__quantity")),
                    Value(Decimal("0")),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
            # Django drops Meta.ordering on aggregation (GROUP BY) queries,
            # so restate it or the response order silently changes.
            .order_by("-created_at", "-pk")
        )
        data = OrderSummarySerializer(orders, many=True).data
        return Response({"count": len(data), "orders": data})
