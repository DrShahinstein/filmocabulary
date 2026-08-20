from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vocabulary", "0003_add_b1_cefr_level"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vocabularyitem",
            name="blank_sentence",
            field=models.TextField(blank=True, null=True),
        ),
    ]
