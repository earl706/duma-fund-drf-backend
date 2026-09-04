from django.contrib import admin

from .models import Category, Transaction, TransactionItem, UserFinance

admin.site.register(UserFinance)
admin.site.register(Category)
admin.site.register(Transaction)
admin.site.register(TransactionItem)
