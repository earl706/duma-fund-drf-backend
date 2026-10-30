from django.contrib import admin

from .models import (
    BudgetProfile,
    BudgetProfileInvite,
    BudgetProfileMembership,
    Category,
    PaymentContact,
    PurchaseExclusion,
    PurchaseRegularMark,
    Transaction,
    TransactionItem,
)

admin.site.register(BudgetProfile)
admin.site.register(BudgetProfileMembership)
admin.site.register(BudgetProfileInvite)
admin.site.register(Category)
admin.site.register(PaymentContact)
admin.site.register(Transaction)
admin.site.register(TransactionItem)
admin.site.register(PurchaseRegularMark)
admin.site.register(PurchaseExclusion)
