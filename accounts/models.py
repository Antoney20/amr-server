import uuid
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin, BaseUserManager
from django.db import models
from django.core.validators import MinLengthValidator
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
# from auditlog.registry import auditlog
 
class UserRole(models.TextChoices):
    ADMIN     = "admin",     "Admin"
    NATIONAL  = "national",  "National Coordinator"
    LAB       = "lab",       "Lab User"
    FACILITY  = "facility",  "Facility User"
    VIEWER    = "viewer",    "Viewer"

class UserStatus(models.TextChoices):
    ACTIVE   = "active",   _("Active")
    INACTIVE = "inactive", _("Inactive")
    BLOCKED  = "blocked",  _("Blocked")


class CustomUserManager(BaseUserManager):
    def _create_user(self, email, username, password=None, **extra_fields):
        if not email:
            raise ValueError(_("The Email field must be set"))
        if not username:
            raise ValueError(_("The Username field must be set"))

        email = self.normalize_email(email)
        user = self.model(email=email, username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("status", UserStatus.ACTIVE)
        return self._create_user(email, username, password, **extra_fields)

    def create_superuser(self, email, username, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", UserStatus.ACTIVE)

        if extra_fields.get("is_staff") is not True:
            raise ValueError(_("Superuser must have is_staff=True"))
        if extra_fields.get("is_superuser") is not True:
            raise ValueError(_("Superuser must have is_superuser=True"))

        return self._create_user(email, username, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """
    Custom user model with email as login field.
    Roles are managed via Django Groups: 'admin', 'staff', 'user'
    """
    email = models.EmailField(
        _("email address"),
        unique=True,
        db_index=True,
    )
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        validators=[MinLengthValidator(3)],
        help_text=_("Required. 3 characters or more. Letters, digits and @/./+/-/_ only."),
    )

    first_name = models.CharField(_("first name"), max_length=150, blank=True)
    last_name  = models.CharField(_("last name"),  max_length=150, blank=True)

    profile_image = models.ImageField(
        upload_to="users/profile/%Y/%m/",
        blank=True,
        null=True,
        verbose_name=_("profile picture")
    )

    # Status & account control
    status       = models.CharField(
        max_length=20,
        choices=UserStatus.choices,
        default=UserStatus.ACTIVE,
        verbose_name=_("account status")
    )
    is_active    = models.BooleanField(
        default=True,
        help_text=_("Designates whether this user should be treated as active.")
    )

    is_staff     = models.BooleanField(
        default=False,
        help_text=_("Designates whether the user can log into this admin site.")
    )
    is_superuser = models.BooleanField(
        default=False,
        help_text=_("Designates that this user has all permissions without explicitly assigning them.")
    )

    date_joined  = models.DateTimeField(_("date joined"), default=timezone.now)
    country           = models.CharField(max_length=100, blank=True, null=True)
    is_email_verified = models.BooleanField(default=False)

    verification_token = models.CharField(max_length=100, blank=True, null=True)
    
    
    invite_token    = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    invited_by      = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name="invited_users")
    invited_at      = models.DateTimeField(null=True, blank=True)
    invite_expires_at = models.DateTimeField(null=True, blank=True)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-date_joined"]

    def __str__(self):
        return f"{self.username} ({self.email})"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.username

    @property
    def is_blocked(self):
        return self.status == UserStatus.BLOCKED

    def has_role(self, role_name: str) -> bool:
        """Check if user belongs to a specific role group"""
        return self.groups.filter(name=role_name).exists()

    @property
    def is_admin(self):
        return self.has_role(UserRole.ADMIN)

    @property
    def is_staff_member(self):
        return self.has_role(UserRole.STAFF) or self.is_staff or self.is_superuser



class EmailLog(models.Model):
    STATUS_CHOICES = [
        ('initial', 'Initial'),
        ('sending', 'Sending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    subject = models.TextField()
    message = models.TextField(blank=True, null=True)
    sender = models.CharField(
        max_length=255,
        default=settings.DEFAULT_FROM_EMAIL
    )
    recipient = models.TextField(help_text="Receiver email address")
    category = models.CharField(max_length=50, default='other')
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='initial'
    )
    error_message = models.TextField(blank=True, null=True)
    retry_count = models.PositiveIntegerField(default=0)
    last_attempt = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    def mark_sending(self):
        self.status = 'sending'
        self.last_attempt = timezone.now()
        self.save(update_fields=['status', 'last_attempt'])

    def mark_sent(self):
        self.status = 'sent'
        self.sent_at = timezone.now()
        self.error_message = None
        self.save(update_fields=['status', 'sent_at', 'error_message'])

    def mark_failed(self, exc):
        self.status = 'failed'
        self.error_message = str(exc)
        self.retry_count += 1
        self.last_attempt = timezone.now()
        self.save(
            update_fields=[
                'status',
                'error_message',
                'retry_count',
                'last_attempt'
            ]
        )

    def __str__(self):
        return f"[{self.category}] {self.subject} → {self.recipient} ({self.status})"
    
