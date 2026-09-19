"""Permission tests for budget profiles and sharing."""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import (
    BudgetProfile,
    BudgetProfileInvite,
    BudgetProfileMembership,
    Category,
    Transaction,
)
from .seeds import ensure_finance_ready, get_default_expense_category

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
