from django.contrib import admin

from .models import User, UserSecurity

# -----------------------------------------------------------------------------
# Model registrations
# -----------------------------------------------------------------------------
admin.site.register(User)
admin.site.register(UserSecurity)
