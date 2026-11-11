from django.contrib import admin

from .models import User, UserLlmSettings, UserSecurity

# -----------------------------------------------------------------------------
# Model registrations
# -----------------------------------------------------------------------------
admin.site.register(User)
admin.site.register(UserSecurity)
admin.site.register(UserLlmSettings)
