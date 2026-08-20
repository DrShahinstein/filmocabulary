from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
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

    def test_signup_allows_a_simple_password(self):
        response = self.client.post(
            reverse("accounts:signup"),
            {
                "username": "simple-learner",
                "email": "simple@example.com",
                "password1": "p",
                "password2": "p",
            },
        )

        self.assertRedirects(response, reverse("movies:dashboard"))
        self.assertTrue(get_user_model().objects.filter(username="simple-learner").exists())

    def test_signup_allows_case_insensitive_duplicate_email(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="first-learner",
            email="Learner@Example.com",
            password="anything",
        )

        response = self.client.post(
            reverse("accounts:signup"),
            {
                "username": "second-learner",
                "email": "learner@example.com",
                "password1": "anything",
                "password2": "anything",
            },
        )

        self.assertRedirects(response, reverse("movies:dashboard"))
        self.assertEqual(user_model.objects.filter(email__iexact="learner@example.com").count(), 2)

    def test_database_allows_duplicate_email(self):
        user_model = get_user_model()
        user_model.objects.create_user(
            username="first-learner",
            email="learner@example.com",
        )
        user_model.objects.create_user(
            username="second-learner",
            email="learner@example.com",
        )

        self.assertEqual(user_model.objects.filter(email="learner@example.com").count(), 2)

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_returns_not_found_when_registration_is_disabled(self):
        response = self.client.get(reverse("accounts:signup"))

        self.assertEqual(response.status_code, 404)

    @override_settings(SIGNUP_ENABLED=False)
    def test_login_does_not_offer_signup_when_registration_is_disabled(self):
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, reverse("accounts:signup"))

    def test_database_allows_multiple_users_without_email(self):
        user_model = get_user_model()

        user_model.objects.create_user(username="no-email-one")
        user_model.objects.create_user(username="no-email-two")

        self.assertEqual(user_model.objects.filter(email="").count(), 2)
