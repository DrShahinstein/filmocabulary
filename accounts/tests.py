from django.contrib.auth import get_user_model
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
