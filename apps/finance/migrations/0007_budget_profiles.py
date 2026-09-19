# Budget profiles + sharing; move starting_balance off UserFinance.

import uuid
from decimal import Decimal

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def forwards_profiles(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    UserFinance = apps.get_model("finance", "UserFinance")
    BudgetProfile = apps.get_model("finance", "BudgetProfile")
    Membership = apps.get_model("finance", "BudgetProfileMembership")
    Category = apps.get_model("finance", "Category")
    Transaction = apps.get_model("finance", "Transaction")
    PurchaseRegularMark = apps.get_model("finance", "PurchaseRegularMark")
    PurchaseExclusion = apps.get_model("finance", "PurchaseExclusion")

    finance_by_user = {
        row.user_id: row.starting_balance or Decimal("0.00")
        for row in UserFinance.objects.all()
    }

    for user in User.objects.all().iterator():
        starting = finance_by_user.get(user.id, Decimal("0.00"))
        profile = BudgetProfile.objects.create(
            owner_id=user.id,
            name="Personal",
            starting_balance=starting,
        )
        Membership.objects.create(profile=profile, user_id=user.id, role="owner")
        Category.objects.filter(owner_id=user.id, profile__isnull=True).update(
            profile=profile
        )
        Transaction.objects.filter(owner_id=user.id, profile__isnull=True).update(
            profile=profile
        )
        PurchaseRegularMark.objects.filter(
            owner_id=user.id, profile__isnull=True
        ).update(profile=profile)
        PurchaseExclusion.objects.filter(owner_id=user.id, profile__isnull=True).update(
            profile=profile
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("finance", "0006_expense_multi_categories"),
    ]

    operations = [
        migrations.CreateModel(
            name="BudgetProfile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "starting_balance",
                    models.DecimalField(
                        decimal_places=2, default=Decimal("0.00"), max_digits=14
                    ),
                ),
                (
                    "owner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="owned_budget_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["name", "id"],
            },
        ),
        migrations.CreateModel(
            name="BudgetProfileMembership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("owner", "Owner"),
                            ("editor", "Editor"),
                            ("viewer", "Viewer"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="memberships",
                        to="finance.budgetprofile",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="budget_profile_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="budgetprofilemembership",
            constraint=models.UniqueConstraint(
                fields=("profile", "user"),
                name="finance_membership_unique_user",
            ),
        ),
        migrations.CreateModel(
            name="BudgetProfileInvite",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "uuid",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("editor", "Editor"),
                            ("viewer", "Viewer"),
                        ],
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                            ("revoked", "Revoked"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                (
                    "invited_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sent_budget_profile_invites",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "invited_user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="budget_profile_invites",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "profile",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="invites",
                        to="finance.budgetprofile",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="budgetprofileinvite",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("profile", "invited_user"),
                name="finance_invite_unique_pending",
            ),
        ),
        migrations.AddField(
            model_name="category",
            name="profile",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="categories",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AddField(
            model_name="transaction",
            name="profile",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="transactions",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AddField(
            model_name="purchaseregularmark",
            name="profile",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchase_regular_marks",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AddField(
            model_name="purchaseexclusion",
            name="profile",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchase_exclusions",
                to="finance.budgetprofile",
            ),
        ),
        migrations.RunPython(forwards_profiles, noop_reverse),
        migrations.AlterField(
            model_name="category",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="categories",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="transactions",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AlterField(
            model_name="purchaseregularmark",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchase_regular_marks",
                to="finance.budgetprofile",
            ),
        ),
        migrations.AlterField(
            model_name="purchaseexclusion",
            name="profile",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="purchase_exclusions",
                to="finance.budgetprofile",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="category",
            name="finance_category_unique_name_per_parent",
        ),
        migrations.AddConstraint(
            model_name="category",
            constraint=models.UniqueConstraint(
                fields=("profile", "parent", "name", "kind"),
                name="finance_category_unique_name_per_parent_profile",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="purchaseregularmark",
            name="finance_purchase_regular_unique_owner_key",
        ),
        migrations.AddConstraint(
            model_name="purchaseregularmark",
            constraint=models.UniqueConstraint(
                fields=("profile", "family_key"),
                name="finance_purchase_regular_unique_profile_key",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="purchaseexclusion",
            name="finance_purchase_exclusion_unique_pair",
        ),
        migrations.AddConstraint(
            model_name="purchaseexclusion",
            constraint=models.UniqueConstraint(
                fields=("profile", "key_a", "key_b"),
                name="finance_purchase_exclusion_unique_pair",
            ),
        ),
        migrations.DeleteModel(name="UserFinance"),
    ]
