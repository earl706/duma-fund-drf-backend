from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import User, UserLlmSettings, UserSecurity

# -----------------------------------------------------------------------------
# User post-save: provision security + LLM settings
# -----------------------------------------------------------------------------


@receiver(post_save, sender=User)
def ensure_security(sender, instance, created, **kwargs):

    if created:
        UserSecurity.objects.get_or_create(user=instance)
        UserLlmSettings.objects.get_or_create(user=instance)
