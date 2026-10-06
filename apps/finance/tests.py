"""Permission tests for budget profiles and sharing."""

from datetime import timedelta
from decimal import Decimal

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from .models import (
    BudgetProfile,
    BudgetProfileInvite,
    BudgetProfileMembership,
    Category,
    Transaction,
)
from .seeds import (
    ensure_finance_ready,
    get_default_expense_category,
    get_default_income_category,
)

User = get_user_model()


def make_user(email, name=""):
    user = User.objects.create_user(email=email, password="pass12345", full_name=name)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    ensure_finance_ready(user)
    return user


class ProfileSharingTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", "Owner")
        self.invitee = make_user("friend@example.com", "Friend")
        self.other = make_user("other@example.com", "Other")
        self.personal = BudgetProfile.objects.get(owner=self.owner)
        self.client.force_authenticate(self.owner)

    def auth(self, user):
        self.client.force_authenticate(user)

    def profile_header(self, profile=None):
        return {"HTTP_X_BUDGET_PROFILE_ID": str((profile or self.personal).id)}

    def test_list_includes_personal(self):
        res = self.client.get("/api/finance/profiles/")
        self.assertEqual(res.status_code, 200)
        names = [p["name"] for p in res.data["results"]]
        self.assertIn("Personal", names)
        self.assertEqual(res.data["pending_invite_count"], 0)

    def test_finance_without_header_400(self):
        res = self.client.get("/api/finance/balance/")
        self.assertEqual(res.status_code, 400)

    def test_foreign_profile_header_403(self):
        foreign = BudgetProfile.objects.get(owner=self.invitee)
        res = self.client.get(
            "/api/finance/balance/",
            **{"HTTP_X_BUDGET_PROFILE_ID": str(foreign.id)},
        )
        self.assertEqual(res.status_code, 403)

    def test_invite_unknown_email(self):
        res = self.client.post(
            f"/api/finance/profiles/{self.personal.id}/members/",
            {"email": "nobody@example.com", "role": "viewer"},
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("No DumaFund account", str(res.data))

    def test_invite_accept_then_viewer_cannot_write(self):
        family = self.client.post(
            "/api/finance/profiles/",
            {"name": "Family"},
            format="json",
        )
        self.assertEqual(family.status_code, 201)
        family_id = family.data["id"]

        invited = self.client.post(
            f"/api/finance/profiles/{family_id}/members/",
            {"email": self.invitee.email, "role": "viewer"},
            format="json",
        )
        self.assertEqual(invited.status_code, 201)
        invite_id = invited.data["id"]

        self.auth(self.invitee)
        before = self.client.get("/api/finance/profiles/")
        ids = [p["id"] for p in before.data["results"]]
        self.assertNotIn(family_id, ids)
        self.assertEqual(before.data["pending_invite_count"], 1)

        pending = self.client.get("/api/finance/invites/")
        self.assertEqual(len(pending.data["results"]), 1)

        accepted = self.client.post(f"/api/finance/invites/{invite_id}/accept/")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.data["role"], "viewer")

        hdr = {"HTTP_X_BUDGET_PROFILE_ID": str(family_id)}
        bal = self.client.get("/api/finance/balance/", **hdr)
        self.assertEqual(bal.status_code, 200)

        cat = Category.objects.filter(profile_id=family_id, kind="expense").first()
        txn = self.client.post(
            "/api/finance/transactions/",
            {
                "type": "expense",
                "title": "Coffee",
                "amount": "4.50",
                "category": cat.id,
                "categories": [cat.id],
            },
            format="json",
            **hdr,
        )
        self.assertEqual(txn.status_code, 403)

        cat_write = self.client.post(
            "/api/finance/categories/",
            {"name": "Snacks", "kind": "expense"},
            format="json",
            **hdr,
        )
        self.assertEqual(cat_write.status_code, 403)

        start = self.client.patch(
            "/api/finance/balance/",
            {"starting_balance": "99.00"},
            format="json",
            **hdr,
        )
        self.assertEqual(start.status_code, 403)

    def test_editor_writes_ledger_not_categories(self):
        family = self.client.post(
            "/api/finance/profiles/", {"name": "Biz"}, format="json"
        )
        family_id = family.data["id"]
        invited = self.client.post(
            f"/api/finance/profiles/{family_id}/members/",
            {"email": self.invitee.email, "role": "editor"},
            format="json",
        )
        self.auth(self.invitee)
        self.client.post(f"/api/finance/invites/{invited.data['id']}/accept/")
        hdr = {"HTTP_X_BUDGET_PROFILE_ID": str(family_id)}
        cat = Category.objects.filter(profile_id=family_id, kind="expense").first()
        txn = self.client.post(
            "/api/finance/transactions/",
            {
                "type": "expense",
                "title": "Lunch",
                "amount": "12.00",
                "category": cat.id,
                "categories": [cat.id],
            },
            format="json",
            **hdr,
        )
        self.assertEqual(txn.status_code, 201)

        cat_write = self.client.post(
            "/api/finance/categories/",
            {"name": "Ads", "kind": "expense"},
            format="json",
            **hdr,
        )
        self.assertEqual(cat_write.status_code, 403)

        start = self.client.patch(
            "/api/finance/balance/",
            {"starting_balance": "50.00"},
            format="json",
            **hdr,
        )
        self.assertEqual(start.status_code, 403)

    def test_decline_does_not_grant_access(self):
        invited = self.client.post(
            f"/api/finance/profiles/{self.personal.id}/members/",
            {"email": self.invitee.email, "role": "editor"},
            format="json",
        )
        self.auth(self.invitee)
        declined = self.client.post(
            f"/api/finance/invites/{invited.data['id']}/decline/"
        )
        self.assertEqual(declined.status_code, 200)
        res = self.client.get("/api/finance/balance/", **self.profile_header())
        self.assertEqual(res.status_code, 403)

    def test_cannot_delete_last_owned_profile(self):
        res = self.client.delete(f"/api/finance/profiles/{self.personal.id}/")
        self.assertEqual(res.status_code, 400)

    def test_owner_balance_and_transaction_scoped(self):
        hdr = self.profile_header()
        cat = get_default_expense_category(self.personal)
        created = self.client.post(
            "/api/finance/transactions/",
            {
                "type": "expense",
                "title": "Solo",
                "amount": "3.00",
                "category": cat.id,
                "categories": [cat.id],
            },
            format="json",
            **hdr,
        )
        self.assertEqual(created.status_code, 201)
        family = self.client.post(
            "/api/finance/profiles/", {"name": "Family"}, format="json"
        )
        family_hdr = {"HTTP_X_BUDGET_PROFILE_ID": str(family.data["id"])}
        listed = self.client.get("/api/finance/transactions/", **family_hdr)
        self.assertEqual(listed.status_code, 200)
        results = listed.data.get("results", listed.data)
        if isinstance(results, dict):
            results = results.get("results", [])
        titles = [row["title"] for row in results]
        self.assertNotIn("Solo", titles)


class FinanceAnalyticsWindowTests(APITestCase):
    def setUp(self):
        self.user = make_user("ledger@example.com", "Ledger")
        self.profile = BudgetProfile.objects.get(owner=self.user)
        self.client.force_authenticate(self.user)
        self.headers = {"HTTP_X_BUDGET_PROFILE_ID": str(self.profile.id)}
        self.expense_cat = get_default_expense_category(self.profile)
        self.income_cat = get_default_income_category(self.profile)
        self.today = timezone.localdate()

    def _txn(self, **kwargs):
        defaults = {
            "owner": self.user,
            "profile": self.profile,
            "title": "row",
            "date_effective": self.today,
        }
        defaults.update(kwargs)
        return Transaction.objects.create(**defaults)

    def test_breakdown_uses_rolling_days_ending_today(self):
        self._txn(
            type="expense",
            amount=Decimal("40.00"),
            category=self.expense_cat,
            date_effective=self.today - timedelta(days=40),
        )
        self._txn(
            type="expense",
            amount=Decimal("12.50"),
            category=self.expense_cat,
            date_effective=self.today,
        )
        res = self.client.get(
            "/api/finance/analytics/breakdown/?period=28",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["period"], 28)
        self.assertEqual(res.data["end"], self.today.isoformat())
        self.assertEqual(
            res.data["start"],
            (self.today - timedelta(days=27)).isoformat(),
        )
        self.assertEqual(res.data["totals"]["expense"], "12.50")

    def test_invalid_period_falls_back_to_28(self):
        res = self.client.get(
            "/api/finance/analytics/breakdown/?period=week",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["period"], 28)

    def test_analytics_money_in_out_includes_transfers(self):
        self._txn(type="income", amount=Decimal("100.00"), category=self.income_cat)
        self._txn(type="transfer_in", amount=Decimal("20.00"))
        self._txn(
            type="expense",
            amount=Decimal("30.00"),
            category=self.expense_cat,
        )
        self._txn(type="transfer_out", amount=Decimal("5.00"))
        day = self.today.isoformat()
        res = self.client.get(
            f"/api/finance/analytics/?grain=day&start={day}&end={day}",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["points"]), 1)
        point = res.data["points"][0]
        self.assertEqual(point["money_in"], "120.00")
        self.assertEqual(point["money_out"], "35.00")
        self.assertEqual(point["txn_spend"], "30.00")

    def test_breakdown_pie_folds_named_other_into_one_slice(self):
        cats = {
            row.name: row
            for row in Category.objects.filter(
                profile=self.profile, kind="expense", parent=None
            )
        }
        spend = {
            "Food": Decimal("100.00"),
            "Transport": Decimal("90.00"),
            "Housing": Decimal("80.00"),
            "Utilities": Decimal("70.00"),
            "Health": Decimal("60.00"),
            "Shopping": Decimal("50.00"),
            "Other": Decimal("40.00"),
            "Entertainment": Decimal("30.00"),
        }
        for name, amount in spend.items():
            self._txn(
                type="expense",
                amount=amount,
                category=cats[name],
                title=name,
            )
        res = self.client.get(
            "/api/finance/analytics/breakdown/?period=28",
            **self.headers,
        )
        self.assertEqual(res.status_code, 200)
        names = [row["name"] for row in res.data["categories"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(names.count("Other"), 1)
        other = next(row for row in res.data["categories"] if row["name"] == "Other")
        self.assertIsNone(other["id"])
        self.assertEqual(other["amount"], "120.00")
