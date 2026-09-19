from django.urls import path
from rest_framework.routers import DefaultRouter

from .analytics import FinanceAnalyticsView, FinanceBreakdownView
from .balance import BalanceView
from .profile_views import (
    MyInviteAcceptView,
    MyInviteDeclineView,
    MyInviteListView,
    ProfileDetailView,
    ProfileInviteRevokeView,
    ProfileListCreateView,
    ProfileMemberDetailView,
    ProfileMemberListCreateView,
)
from .purchases import (
    PurchaseExcludeView,
    PurchaseInsightsView,
    PurchaseLookupView,
    PurchaseMarkRegularView,
    PurchaseNotificationsView,
)
from .receipt_views import (
    BulkCommitReceiptView,
    BulkScanReceiptView,
    CommitReceiptView,
    ReceiptScanView,
)
from .views import CategoryViewSet, TransactionItemViewSet, TransactionViewSet


router = DefaultRouter()
router.register("finance/categories", CategoryViewSet, basename="finance-category")
router.register(
    "finance/transactions", TransactionViewSet, basename="finance-transaction"
)

# Specific transaction paths before router detail routes.
urlpatterns = [
    path(
        "finance/profiles/",
        ProfileListCreateView.as_view(),
        name="finance-profile-list",
    ),
    path(
        "finance/profiles/<int:pk>/",
        ProfileDetailView.as_view(),
        name="finance-profile-detail",
    ),
    path(
        "finance/profiles/<int:pk>/members/",
        ProfileMemberListCreateView.as_view(),
        name="finance-profile-member-list",
    ),
    path(
        "finance/profiles/<int:pk>/members/<int:user_id>/",
        ProfileMemberDetailView.as_view(),
        name="finance-profile-member-detail",
    ),
    path(
        "finance/profiles/<int:pk>/invites/<int:invite_id>/",
        ProfileInviteRevokeView.as_view(),
        name="finance-profile-invite-revoke",
    ),
    path(
        "finance/invites/",
        MyInviteListView.as_view(),
        name="finance-invite-list",
    ),
    path(
        "finance/invites/<int:pk>/accept/",
        MyInviteAcceptView.as_view(),
        name="finance-invite-accept",
    ),
    path(
        "finance/invites/<int:pk>/decline/",
        MyInviteDeclineView.as_view(),
        name="finance-invite-decline",
    ),
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
        "finance/analytics/breakdown/",
        FinanceBreakdownView.as_view(),
        name="finance-analytics-breakdown",
    ),
    path(
        "finance/purchases/lookup/",
        PurchaseLookupView.as_view(),
        name="finance-purchase-lookup",
    ),
    path(
        "finance/purchases/insights/",
        PurchaseInsightsView.as_view(),
        name="finance-purchase-insights",
    ),
    path(
        "finance/purchases/notifications/",
        PurchaseNotificationsView.as_view(),
        name="finance-purchase-notifications",
    ),
    path(
        "finance/purchases/mark-regular/",
        PurchaseMarkRegularView.as_view(),
        name="finance-purchase-mark-regular",
    ),
    path(
        "finance/purchases/exclude/",
        PurchaseExcludeView.as_view(),
        name="finance-purchase-exclude",
    ),
    path(
        "finance/transactions/scan-receipt/",
        ReceiptScanView.as_view(),
        name="finance-scan-receipt",
    ),
    path(
        "finance/transactions/bulk-scan-receipts/",
        BulkScanReceiptView.as_view(),
        name="finance-bulk-scan-receipts",
    ),
    path(
        "finance/transactions/commit-receipt/",
        CommitReceiptView.as_view(),
        name="finance-commit-receipt",
    ),
    path(
        "finance/transactions/bulk-commit-receipts/",
        BulkCommitReceiptView.as_view(),
        name="finance-bulk-commit-receipts",
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
