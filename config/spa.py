from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404

# -----------------------------------------------------------------------------
# Desktop SPA index response
# -----------------------------------------------------------------------------
def spa_index_response():
    dist = Path(settings.FRONTEND_DIST)
    index = dist / "index.html"
    if not index.is_file():
        raise Http404("Frontend build not found.")
    return FileResponse(index.open("rb"), content_type="text/html")
