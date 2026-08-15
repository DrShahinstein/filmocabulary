import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase


class BackupDatabaseCommandTests(TransactionTestCase):
    def test_backup_contains_committed_application_data(self):
        get_user_model().objects.create_user(username="backup-learner")

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "snapshot.sqlite3"
            call_command("backup_database", destination, verbosity=0)

            with sqlite3.connect(destination) as backup:
                usernames = backup.execute(
                    "SELECT username FROM auth_user WHERE username = ?",
                    ("backup-learner",),
                ).fetchall()

            self.assertEqual(usernames, [("backup-learner",)])
            self.assertEqual(destination.stat().st_mode & 0o777, 0o600)

    def test_backup_refuses_to_overwrite_an_existing_file(self):
        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "snapshot.sqlite3"
            destination.touch()

            with self.assertRaisesMessage(CommandError, "Refusing to overwrite"):
                call_command("backup_database", destination, verbosity=0)
