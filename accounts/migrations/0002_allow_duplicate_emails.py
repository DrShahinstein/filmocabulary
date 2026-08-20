from django.db import migrations


INDEX_NAME = "auth_user_email_ci_uniq"


class Migration(migrations.Migration):
    dependencies = [("accounts", "0001_enforce_email_uniqueness")]

    operations = [
        migrations.RunSQL(
            sql=f'DROP INDEX IF EXISTS "{INDEX_NAME}"',
            reverse_sql=(
                f'CREATE UNIQUE INDEX "{INDEX_NAME}" '
                'ON "auth_user" (LOWER("email")) WHERE "email" <> \'\''
            ),
        ),
    ]
