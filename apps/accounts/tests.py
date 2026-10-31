"""Account deletion and related auth tests."""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.finance.models import BudgetProfile, BudgetProfileMembership
from apps.finance.seeds import ensure_finance_ready

User = get_user_model()


def make_user(email, name=""):
    user = User.objects.create_user(email=email, password="pass12345", full_name=name)
    EmailAddress.objects.create(user=user, email=email, verified=True, primary=True)
    ensure_finance_ready(user)
    return user


class AccountDeletionTests(APITestCase):
    def setUp(self):
        self.owner = make_user("owner@example.com", "Owner")
        self.friend = make_user("friend@example.com", "Friend")
        self.owned = BudgetProfile.objects.get(owner=self.owner)
        self.shared = BudgetProfile.objects.create(owner=self.friend, name="Shared")
        BudgetProfileMembership.objects.create(
            profile=self.shared,
            user=self.owner,
            role="viewer",
        )
        self.client.force_authenticate(self.owner)

    def test_delete_me_removes_user_and_owned_profile(self):
        owner_id = self.owner.id
        owned_id = self.owned.id
        shared_id = self.shared.id

        res = self.client.delete("/api/auth/me/")
        self.assertEqual(res.status_code, 204)

        self.assertFalse(User.objects.filter(id=owner_id).exists())
        self.assertFalse(BudgetProfile.objects.filter(id=owned_id).exists())
        self.assertTrue(BudgetProfile.objects.filter(id=shared_id).exists())
        self.assertFalse(
            BudgetProfileMembership.objects.filter(
                profile_id=shared_id, user_id=owner_id
            ).exists()
        )

    def test_delete_me_requires_auth(self):
        self.client.force_authenticate(user=None)
        res = self.client.delete("/api/auth/me/")
        self.assertEqual(res.status_code, 401)
