from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models, transaction
from django.db.models.functions import Lower, Trim

from core.models import TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        return (email or "").strip().lower()

    def create_user(self, phone_number=None, password=None, **extra):
        email = self.normalize_email(extra.pop("email", ""))
        phone_number = (phone_number or "").strip() or None
        if not phone_number and not email:
            raise ValueError("At least one contact is required")
        with transaction.atomic(using=self.db):
            user = self.model(phone_number=phone_number, email=email, **extra)
            user.set_password(password)
            user.save(using=self.db)
            Profile.objects.using(self.db).create(user=user, full_name="")
        return user

    def create_superuser(self, phone_number=None, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("Superuser must have is_staff=True and is_superuser=True")
        return self.create_user(phone_number, password, **extra)


class User(AbstractUser, TimeStampedModel):
    """An account identified by at least one phone or email contact."""

    username = None
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    email = models.EmailField(blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)
    email_verified_at = models.DateTimeField(null=True, blank=True)

    class Role(models.TextChoices):
        PASSENGER = "PASSENGER", "Passenger"
        DRIVER = "DRIVER", "Driver"
        BRAND = "BRAND", "Brand"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PASSENGER)
    preferred_language = models.CharField(
        max_length=2, choices=[("ar", "Arabic"), ("fr", "French"), ("en", "English")], default="fr"
    )
    is_verified = models.BooleanField(default=False)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.UniqueConstraint(
                Lower(Trim("email")),
                condition=~models.Q(email=""),
                name="users_email_ci_unique",
            ),
            models.CheckConstraint(
                condition=(
                    (models.Q(phone_number__isnull=False) & ~models.Q(phone_number__regex=r"^\s*$"))
                    | ~models.Q(email="")
                ),
                name="users_contact_required",
            ),
            models.CheckConstraint(
                condition=models.Q(email=Lower(Trim("email"))),
                name="users_email_normalized",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = UserManager.normalize_email(self.email)
        self.phone_number = (self.phone_number or "").strip() or None
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.phone_number or self.email


class InterestTag(models.Model):
    """Personal interest taxonomy. Slugs are stable API values; labels are display-only."""

    slug = models.SlugField(max_length=40, unique=True)
    label_en = models.CharField(max_length=60)
    label_fr = models.CharField(max_length=60)
    label_ar = models.CharField(max_length=60)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "slug"]

    def __str__(self):
        return self.slug


class Profile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=150, blank=True, default="")
    avatar_key = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    interests = models.ManyToManyField(InterestTag, blank=True, related_name="profiles")


# Importable alias for SPECTACULAR_SETTINGS.ENUM_NAME_OVERRIDES (stable schema names).
USER_ROLE_CHOICES = User.Role.choices
