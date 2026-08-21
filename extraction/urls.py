from django.urls import path

from .views import ExtractInvoiceView, HealthView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    path("invoices/extract/", ExtractInvoiceView.as_view(), name="extract-invoice"),
]
