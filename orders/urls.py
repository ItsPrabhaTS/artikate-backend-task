from django.urls import path

from .views import BrokenOrderSummaryView, OrderSummaryView

urlpatterns = [
    path("summary/", OrderSummaryView.as_view(), name="order-summary"),
    path("summary-broken/", BrokenOrderSummaryView.as_view(), name="order-summary-broken"),
]
