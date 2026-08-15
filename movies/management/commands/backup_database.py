import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Create a consistent backup of the Home SQLite database."

    def add_arguments(self, parser):
        parser.add_argument(
            "output",
            nargs="?",
            type=Path,
            help="Destination file (default: backups/filmocabulary-<UTC timestamp>.sqlite3)",
        )

    def handle(self, *args, **options):
        if connection.vendor != "sqlite":
            raise CommandError("The Home backup command supports SQLite only.")

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = options["output"] or (
            settings.BASE_DIR / "backups" / f"filmocabulary-{timestamp}.sqlite3"
        )
        destination = destination.expanduser().resolve()
        database_path = Path(settings.DATABASES["default"]["NAME"]).resolve()

        if destination == database_path:
            raise CommandError("The backup destination cannot be the active database.")
        if destination.exists():
            raise CommandError(f"Refusing to overwrite existing backup: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        connection.ensure_connection()
        try:
            with sqlite3.connect(destination) as backup_connection:
                connection.connection.backup(backup_connection)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        os.chmod(destination, 0o600)
        self.stdout.write(self.style.SUCCESS(f"Database backup created: {destination}"))
