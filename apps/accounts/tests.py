"""Account deletion and Receipt AI LLM settings tests."""

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.accounts.llm_crypto import decrypt_secret, encrypt_secret, key_last4
from apps.accounts.llm_views import active_llm_override_for_user
from apps.accounts.models import UserLlmSettings, get_user_llm_settings
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


class LlmCryptoTests(APITestCase):
    def test_encrypt_decrypt_round_trip(self):
        secret = "sk-test-secret-key-value"
        blob = encrypt_secret(secret)
        self.assertTrue(blob)
        self.assertNotEqual(blob, secret)
        self.assertEqual(decrypt_secret(blob), secret)
        self.assertEqual(key_last4(secret), "alue")


class LlmSettingsApiTests(APITestCase):
    def setUp(self):
        self.user = make_user("llm@example.com", "LLM User")
        self.client.force_authenticate(self.user)

    def test_get_returns_masked_defaults(self):
        res = self.client.get("/api/auth/llm/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["provider"], "openai")
        self.assertFalse(body["openai_key_set"])
        self.assertFalse(body["gemini_key_set"])
        self.assertEqual(body["openai_key_hint"], "")
        self.assertNotIn("openai_api_key", body)
        self.assertNotIn("openai_api_key_encrypted", body)

    def test_patch_stores_encrypted_key_and_last4(self):
        res = self.client.patch(
            "/api/auth/llm/",
            {
                "provider": "openai",
                "openai_api_key": "sk-live-abcdef1234",
                "openai_model": "gpt-4o-mini",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["openai_key_set"])
        self.assertEqual(body["openai_key_hint"], "1234")
        self.assertNotIn("openai_api_key", body)

        row = get_user_llm_settings(self.user)
        self.assertTrue(row.openai_api_key_encrypted)
        self.assertNotIn("sk-live", row.openai_api_key_encrypted)
        self.assertEqual(decrypt_secret(row.openai_api_key_encrypted), "sk-live-abcdef1234")

        override = active_llm_override_for_user(self.user)
        self.assertEqual(override["provider"], "openai")
        self.assertEqual(override["api_key"], "sk-live-abcdef1234")
        self.assertEqual(override["model"], "gpt-4o-mini")

    def test_patch_ignores_masked_placeholder(self):
        self.client.patch(
            "/api/auth/llm/",
            {"openai_api_key": "sk-real-key-9999"},
            format="json",
        )
        res = self.client.patch(
            "/api/auth/llm/",
            {"openai_api_key": "••••9999"},
            format="json",
        )
        self.assertEqual(res.status_code, 200)
        row = get_user_llm_settings(self.user)
        self.assertEqual(decrypt_secret(row.openai_api_key_encrypted), "sk-real-key-9999")

    def test_delete_keys_clears_both(self):
        self.client.patch(
            "/api/auth/llm/",
            {
                "openai_api_key": "sk-openai",
                "gemini_api_key": "AIza-gemini",
            },
            format="json",
        )
        res = self.client.delete("/api/auth/llm/keys/")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["openai_key_set"])
        self.assertFalse(body["gemini_key_set"])
        row = UserLlmSettings.objects.get(user=self.user)
        self.assertEqual(row.openai_api_key_encrypted, "")
        self.assertEqual(row.gemini_api_key_encrypted, "")

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(user=None)
        self.assertEqual(self.client.get("/api/auth/llm/").status_code, 401)
