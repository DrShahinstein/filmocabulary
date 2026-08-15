import json
import os
import subprocess
import sys
from tempfile import TemporaryDirectory

from django.conf import settings
from django.test import SimpleTestCase


class HomeProductionSettingsTests(SimpleTestCase):
    def run_settings_probe(self, **environment):
        with TemporaryDirectory() as temporary_directory:
            probe_environment = os.environ.copy()
            probe_environment.update(
                {
                    "DJANGO_SETTINGS_MODULE": "config.settings.production",
                    "DJANGO_SECRET_KEY": "home-test-secret-" + "x" * 64,
                    "ALLOWED_HOSTS": "localhost,127.0.0.1",
                    "SQLITE_DATABASE_PATH": os.path.join(
                        temporary_directory, "production.sqlite3"
                    ),
                    **environment,
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json; import django; django.setup(); "
                        "from django.conf import settings; from django.db import connection; "
                        "connection.ensure_connection(); connection.close(); "
                        "print(json.dumps({"
                        "'engine': settings.DATABASES['default']['ENGINE'], "
                        "'redirect': settings.SECURE_SSL_REDIRECT, "
                        "'csrf_secure': settings.CSRF_COOKIE_SECURE, "
                        "'session_secure': settings.SESSION_COOKIE_SECURE, "
                        "'signup': settings.SIGNUP_ENABLED}))"
                    ),
                ],
                cwd=settings.BASE_DIR,
                env=probe_environment,
                capture_output=True,
                check=True,
                text=True,
            )
        return json.loads(result.stdout)

    def test_home_defaults_to_sqlite_and_plain_local_http(self):
        configuration = self.run_settings_probe(HOME_HTTPS="False")

        self.assertEqual(configuration["engine"], "django.db.backends.sqlite3")
        self.assertFalse(configuration["redirect"])
        self.assertFalse(configuration["csrf_secure"])
        self.assertFalse(configuration["session_secure"])
        self.assertFalse(configuration["signup"])

    def test_home_enables_transport_security_as_one_switch(self):
        configuration = self.run_settings_probe(HOME_HTTPS="True")

        self.assertTrue(configuration["redirect"])
        self.assertTrue(configuration["csrf_secure"])
        self.assertTrue(configuration["session_secure"])
