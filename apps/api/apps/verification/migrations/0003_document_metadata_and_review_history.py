import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("verification", "0002_verificationdocument_uploaded_at"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="verificationdocument", name="content_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="verificationdocument", name="size_bytes",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="verificationdocument", name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending review"), ("APPROVED", "Approved"), ("REJECTED", "Rejected"),
                    ("NEEDS_MORE_DOCUMENTS", "Needs more documents"), ("SUPERSEDED", "Superseded"),
                ],
                db_index=True, default="PENDING", max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="VerificationReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision", models.CharField(
                    choices=[
                        ("APPROVE", "Approve"), ("REJECT", "Reject"), ("REQUEST_MORE", "Request more documents"),
                        ("SUPERSEDE", "Supersede"), ("REVOKE", "Revoke"),
                    ], max_length=20,
                )),
                ("reason", models.TextField(blank=True)),
                ("timestamp", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL,
                )),
                ("document", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT, related_name="history", to="verification.verificationdocument",
                )),
            ],
            options={"ordering": ["timestamp", "id"]},
        ),
    ]
