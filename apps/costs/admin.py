from django.contrib import admin

from .models import CostItem, CostList

# -----------------------------------------------------------------------------
# Model registrations
# -----------------------------------------------------------------------------
admin.site.register(CostList)
admin.site.register(CostItem)
