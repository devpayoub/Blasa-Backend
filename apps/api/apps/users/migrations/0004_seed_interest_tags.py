from django.db import migrations

TAGS = [
    ("music", "Music", "Musique", "موسيقى"),
    ("tech", "Tech", "Tech", "تكنولوجيا"),
    ("entrepreneurship", "Entrepreneurship", "Entrepreneuriat", "ريادة الأعمال"),
    ("sports", "Sports", "Sport", "رياضة"),
    ("culture", "Culture", "Culture", "ثقافة"),
    ("politics", "Politics", "Politique", "سياسة"),
    ("hobbies", "Hobbies", "Loisirs", "هوايات"),
]


def seed(apps, schema_editor):
    InterestTag = apps.get_model("users", "InterestTag")
    for order, (slug, en, fr, ar) in enumerate(TAGS):
        InterestTag.objects.update_or_create(
            slug=slug, defaults={"label_en": en, "label_fr": fr, "label_ar": ar, "sort_order": order},
        )


class Migration(migrations.Migration):
    dependencies = [("users", "0003_interesttag_profile_interests")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
