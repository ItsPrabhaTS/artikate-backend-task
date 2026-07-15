from django.urls import path

from .views import TenantOrderListView

urlpatterns = [
    path("orders/", TenantOrderListView.as_view(), name="tenant-orders"),
]
