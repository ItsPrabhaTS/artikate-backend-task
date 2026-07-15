from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Order


class TenantOrderListView(APIView):
    """/api/tenants/orders/

    Note what is *absent* here: no tenant filter, no awareness that tenants
    exist. The manager scopes the query from the context the middleware
    bound. This is exactly the "developer forgot to filter" scenario the
    design defends against.
    """

    def get(self, request):
        orders = Order.objects.all()
        return Response({
            "orders": [
                {"reference": o.reference, "amount": str(o.amount)} for o in orders
            ]
        })
