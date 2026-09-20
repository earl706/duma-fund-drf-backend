from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

# Dist-root files Vite copies from frontend/public (app-icon.svg, favicon, …).
SPA_STATIC_SUFFIXES = {
    ".ico",
    ".map",
    ".png",
    ".svg",
    ".txt",
    ".webmanifest",
    ".webp",
    ".woff",
    ".woff2",
}


# -----------------------------------------------------------------------------
# Desktop SPA index response
# -----------------------------------------------------------------------------
def spa_index_response():
    dist = Path(settings.FRONTEND_DIST)
    index = dist / "index.html"
    if not index.is_file():
        raise Http404("Frontend build not found.")
    return FileResponse(index.open("rb"), content_type="text/html")


# -----------------------------------------------------------------------------
# Desktop SPA static files (public/ copies at dist root)
# -----------------------------------------------------------------------------
def spa_static_response(path):
    dist = Path(settings.FRONTEND_DIST).resolve()
    candidate = (dist / path).resolve()
    try:
        candidate.relative_to(dist)
    except ValueError as exc:
        raise Http404() from exc
    if not candidate.is_file() or candidate.suffix.lower() not in SPA_STATIC_SUFFIXES:
        raise Http404()
    return FileResponse(candidate.open("rb"))
