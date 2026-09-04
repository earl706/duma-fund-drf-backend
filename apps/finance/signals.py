from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .seeds import ensure_finance_ready


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def provision_finance(sender, instance, created, **kwargs):
    if created:
        ensure_finance_ready(instance)
