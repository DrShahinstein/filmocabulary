from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse


class SignUpViewTests(TestCase):
    def test_signup_creates_and_logs_in_user(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "username": "learner",
                "email": "learner@example.com",
                "password1": "A-secure-passphrase-42",
                "password2": "A-secure-passphrase-42",
            },
        )

        self.assertRedirects(response, reverse("movies:dashboard"))
        self.assertTrue(get_user_model().objects.filter(username="learner").exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), 1)

    def test_database_rejects_case_insensitive_duplicate_email(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="first-learner",
            email="Learner@Example.com",
            password="A-secure-passphrase-42",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            user_model.objects.create_user(
                username="second-learner",
                email="learner@example.com",
                password="A-secure-passphrase-42",
            )

    def test_database_allows_multiple_users_without_email(self):
        user_model = get_user_model()

        user_model.objects.create_user(username="no-email-one")
        user_model.objects.create_user(username="no-email-two")

        self.assertEqual(user_model.objects.filter(email="").count(), 2)
