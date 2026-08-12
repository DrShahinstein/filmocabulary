from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("vocabulary", "0002_remove_vocabularyitem_translation_tr"),
    ]

    operations = [
        migrations.AlterField(
            model_name="vocabularyitem",
            name="cefr_level",
            field=models.CharField(
                choices=[
                    ("B1", "B1"),
                    ("B2", "B2"),
                    ("C1", "C1"),
                    ("C2", "C2"),
                ],
                max_length=2,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="vocabularyitem",
            name="vocab_valid_cefr",
        ),
        migrations.AddConstraint(
            model_name="vocabularyitem",
            constraint=models.CheckConstraint(
                condition=models.Q(cefr_level__in=("B1", "B2", "C1", "C2")),
                name="vocab_valid_cefr",
            ),
        ),
    ]
