from django.urls import path
from rest_framework.routers import DefaultRouter

from .analytics import FinanceAnalyticsView
from .balance import BalanceView
from .receipt_views import CommitReceiptView, ReceiptScanView
from .views import CategoryViewSet, TransactionItemViewSet, TransactionViewSet


router = DefaultRouter()
router.register("finance/categories", CategoryViewSet, basename="finance-category")
router.register(
    "finance/transactions", TransactionViewSet, basename="finance-transaction"
)

# Specific transaction paths before router detail routes.
urlpatterns = [
    path(
        "finance/balance/",
        BalanceView.as_view(),
        name="finance-balance",
    ),
    path(
        "finance/analytics/",
        FinanceAnalyticsView.as_view(),
        name="finance-analytics",
    ),
    path(
        "finance/transactions/scan-receipt/",
        ReceiptScanView.as_view(),
        name="finance-scan-receipt",
    ),
    path(
        "finance/transactions/commit-receipt/",
        CommitReceiptView.as_view(),
        name="finance-commit-receipt",
    ),
    path(
        "finance/transactions/<int:transaction_pk>/items/",
        TransactionItemViewSet.as_view({"get": "list", "post": "create"}),
        name="finance-transaction-item-list",
    ),
    path(
        "finance/transactions/<int:transaction_pk>/items/<int:pk>/",
        TransactionItemViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="finance-transaction-item-detail",
    ),
] + router.urls
