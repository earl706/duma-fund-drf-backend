from django.urls import path
from rest_framework.routers import DefaultRouter

from .analytics import CostAnalyticsView
from .views import CostItemViewSet, CostListViewSet


# -----------------------------------------------------------------------------
# Router & URL patterns
# -----------------------------------------------------------------------------
router = DefaultRouter()
router.register("cost-lists", CostListViewSet, basename="cost-list")

urlpatterns = router.urls + [
    path(
        "costs/analytics/",
        CostAnalyticsView.as_view(),
        name="cost-analytics",
    ),
    path(
        "cost-lists/<int:list_pk>/items/",
        CostItemViewSet.as_view({"get": "list", "post": "create"}),
        name="cost-item-list",
    ),
    path(
        "cost-lists/<int:list_pk>/items/<int:pk>/",
        CostItemViewSet.as_view(
            {
                "get": "retrieve",
                "put": "update",
                "patch": "partial_update",
                "delete": "destroy",
            }
        ),
        name="cost-item-detail",
    ),
]
