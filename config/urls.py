from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from .spa import spa_index_response

# -----------------------------------------------------------------------------
# API route includes
# -----------------------------------------------------------------------------
api_patterns = [
    path("auth/", include("apps.accounts.urls")),
    path("", include("apps.finance.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

# -----------------------------------------------------------------------------
# Root urlpatterns
# -----------------------------------------------------------------------------
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include(api_patterns)),
]

# -----------------------------------------------------------------------------
# Local media (development)
# -----------------------------------------------------------------------------
if settings.STORAGE_BACKEND == "local":
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# -----------------------------------------------------------------------------
# Desktop SPA fallback
# -----------------------------------------------------------------------------
if getattr(settings, "DESKTOP_MODE", False):
    frontend_dist = settings.FRONTEND_DIST

    def spa_view(_request, *_args, **_kwargs):
        return spa_index_response()

    urlpatterns += [
        path(
            "assets/<path:path>",
            serve,
            {"document_root": frontend_dist / "assets"},
        ),
        path(
            "favicon.svg",
            serve,
            {"document_root": frontend_dist, "path": "favicon.svg"},
        ),
        re_path(r"^(?!api/|admin/|media/).*$", spa_view),
    ]
