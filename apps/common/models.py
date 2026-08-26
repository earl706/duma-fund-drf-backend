import uuid

from django.conf import settings
from django.db import models

# -----------------------------------------------------------------------------
# Timestamp / UUID base model
# -----------------------------------------------------------------------------


class TimeStampedModel(models.Model):

    uuid = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True, db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


# -----------------------------------------------------------------------------
# Owner-scoped base model
# -----------------------------------------------------------------------------


class OwnedModel(TimeStampedModel):

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(class)ss",
    )

    class Meta(TimeStampedModel.Meta):
        abstract = True
