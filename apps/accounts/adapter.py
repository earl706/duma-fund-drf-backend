from allauth.account.adapter import DefaultAccountAdapter
from django.conf import settings


class AccountAdapter(DefaultAccountAdapter):
    """SPA-friendly confirmation links and branded sender name."""

    def get_email_confirmation_url(self, request, emailconfirmation):
        return f"{settings.FRONTEND_URL}/verify-email?key={emailconfirmation.key}"

    def send_mail(self, template_prefix, email, context):
        context.setdefault("site_name", "DumaFund")
        return super().send_mail(template_prefix, email, context)

    def add_message(
        self, request, level, message_template, message_context=None, extra_tags=""
    ):
        # JWT/SPA API — no Django session flash messages.
        return None
