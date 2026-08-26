import os

from celery import Celery

# -----------------------------------------------------------------------------
# Celery app bootstrap
# -----------------------------------------------------------------------------
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("dumafund")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


# -----------------------------------------------------------------------------
# Debug task
# -----------------------------------------------------------------------------
@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")
