from django.contrib import admin

from .models import (
    Category,
    PurchaseExclusion,
    PurchaseRegularMark,
    Transaction,
    TransactionItem,
    UserFinance,
)

admin.site.register(UserFinance)
admin.site.register(Category)
admin.site.register(Transaction)
admin.site.register(TransactionItem)
admin.site.register(PurchaseRegularMark)
admin.site.register(PurchaseExclusion)
