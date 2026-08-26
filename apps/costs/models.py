from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from apps.common.models import OwnedModel


def today():
    return timezone.localdate()


STATUS_CHOICES = [
    ("active", "Active"),
    ("archived", "Archived"),
]


# -----------------------------------------------------------------------------
# Cost list
# -----------------------------------------------------------------------------
class CostList(OwnedModel):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    date_created = models.DateField(default=today)
    date_last_modified = models.DateField(auto_now=True)

    class Meta(OwnedModel.Meta):
        pass

    def __str__(self):
        return self.title


# -----------------------------------------------------------------------------
# Cost item (line on a list)
# -----------------------------------------------------------------------------
class CostItem(OwnedModel):
    cost_list = models.ForeignKey(
        CostList,
        on_delete=models.CASCADE,
        related_name="items",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    cost = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    date_created = models.DateField(default=today)
    date_last_modified = models.DateField(auto_now=True)

    class Meta(OwnedModel.Meta):
        pass

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.cost_list_id:
            self.cost_list.save()

    def delete(self, *args, **kwargs):
        cost_list = self.cost_list
        super().delete(*args, **kwargs)
        cost_list.save()
