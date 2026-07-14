from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/orders/", include("orders.urls")),
    path("api/tenants/", include("tenants.urls")),
]

if settings.DEBUG:
    urlpatterns.append(path("silk/", include("silk.urls", namespace="silk")))
