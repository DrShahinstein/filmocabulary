from django.db import migrations
from django.db.models import Count
from django.db.models.functions import Lower


INDEX_NAME = "auth_user_email_ci_uniq"


def reject_existing_duplicate_emails(apps, schema_editor):
    user_model = apps.get_model("auth", "User")
    duplicates = (
        user_model.objects.using(schema_editor.connection.alias)
        .exclude(email="")
        .annotate(normalized_email=Lower("email"))
        .values("normalized_email")
        .annotate(email_count=Count("pk"))
        .filter(email_count__gt=1)
    )
    if duplicates.exists():
        raise RuntimeError(
            "Case-insensitive duplicate user emails must be resolved before "
            "applying the email uniqueness migration."
        )


class Migration(migrations.Migration):
    # This application currently uses Django's built-in User model. Depend on
    # its final schema migration so later auth table rebuilds preserve the index.
    dependencies = [("auth", "0012_alter_user_first_name_max_length")]

    operations = [
        migrations.RunPython(
            reject_existing_duplicate_emails,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunSQL(
            sql=(
                f'CREATE UNIQUE INDEX "{INDEX_NAME}" '
                'ON "auth_user" (LOWER("email")) WHERE "email" <> \'\''
            ),
            reverse_sql=f'DROP INDEX IF EXISTS "{INDEX_NAME}"',
        ),
    ]
