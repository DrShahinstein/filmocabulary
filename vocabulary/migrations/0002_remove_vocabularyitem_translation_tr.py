from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("vocabulary", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="vocabularyitem",
            name="translation_tr",
        ),
    ]
