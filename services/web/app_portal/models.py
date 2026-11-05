from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class EntraGroup(models.Model):
    object_id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)
    # The db_default values let the previous release keep writing during a rolling update.
    status = models.CharField(
        max_length=10,
        default="discovered",
        db_default="discovered",
        db_index=True,
        choices=[
            ("discovered", "Discovered"),
            ("enabled", "Enabled"),
            ("archived", "Archived"),
        ],
        help_text="Only enabled libraries are available in the portal. "
        "Archiving revokes portal access and keeps all data.",
    )
    show_when_empty = models.BooleanField(
        "show when empty",
        default=False,
        db_default=False,
        help_text="List this library for creators and administrators before it has podcasts. "
        "This does not grant membership or creator rights.",
    )
    reviewed = models.BooleanField(default=False, db_default=False)

    class Meta:
        verbose_name = "library"
        verbose_name_plural = "libraries"
        ordering = ("name", "object_id")

    def __str__(self):
        return self.name or f"Unnamed group {str(self.object_id)[:8]}"

    def clean(self):
        self.name = self.name.strip()
        if self.status == "enabled" and not self.name:
            raise ValidationError({"name": "Enter a display name before enabling this library."})


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=7, choices=[("viewer", "Viewer"), ("creator", "Creator")], default="viewer"
    )
    groups = models.ManyToManyField(
        EntraGroup, related_name="people", blank=True, verbose_name="Entra groups"
    )
    membership = models.CharField(
        "directory membership",
        max_length=8,
        default="complete",
        db_default="complete",
        choices=[
            ("complete", "Complete"),
            ("overage", "Too many groups for sign-in"),
            ("missing", "No groups in sign-in"),
            ("failed", "Unreadable groups in sign-in"),
        ],
    )
    local_admin = models.BooleanField(
        "application administrator",
        default=False,
        db_default=False,
        help_text="Grants administrator access independently of Entra groups. "
        "Takes effect on this person's next request.",
    )
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        name = self.user.get_full_name() or self.user.username
        return f"{name} ({self.user.email})" if self.user.email else name


class LibraryGrantQuerySet(models.QuerySet):
    def active(self):
        return self.filter(
            Q(expires__isnull=True) | Q(expires__gt=timezone.now()), revoked__isnull=True
        )


class LibraryGrant(models.Model):
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="library_grants", verbose_name="user"
    )
    library = models.ForeignKey(
        EntraGroup,
        on_delete=models.PROTECT,
        related_name="grants",
        limit_choices_to={"status": "enabled"},
    )
    reason = models.CharField(max_length=500)
    expires = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty for access until it is revoked."
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created = models.DateTimeField(auto_now_add=True)
    revoked = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    objects = LibraryGrantQuerySet.as_manager()

    class Meta:
        verbose_name = "local library access"
        verbose_name_plural = "local library access"
        ordering = ("-created",)
        constraints = (
            models.UniqueConstraint(
                fields=("profile", "library"),
                condition=Q(revoked__isnull=True),
                name="one_open_library_grant",
            ),
        )

    def __str__(self):
        return f"{self.profile} \u2192 {self.library}"


class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        EntraGroup, on_delete=models.PROTECT, related_name="courses", verbose_name="Entra group"
    )
    shared_groups = models.ManyToManyField(
        EntraGroup, related_name="shared_courses", blank=True, verbose_name="Additional libraries",
        limit_choices_to={"status": "enabled"},
        help_text="Share published audio with these libraries. "
        "Editing stays with the owning library.",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=500, blank=True)
    description = models.TextField(blank=True)
    cover = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=12,
        default="draft",
        choices=[
            ("draft", "Draft"),
            ("needs_review", "Needs review"),
            ("ready", "Ready"),
        ],
    )
    manifest = models.JSONField(default=dict, blank=True)
    generation_settings = models.JSONField(default=dict, blank=True)
    html = models.TextField(blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "podcast"
        verbose_name_plural = "podcasts"
        ordering = ("-created",)

    def __str__(self):
        return self.title or f"Untitled podcast {str(self.id)[:8]}"


class LocalTestUser(models.Model):
    number = models.AutoField(primary_key=True)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    session_key = models.CharField(max_length=40, blank=True)

    def __str__(self):
        return f"Fake user {self.number}"


class Run(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="runs", verbose_name="podcast"
    )
    task = models.CharField(
        max_length=6,
        choices=[
            ("create", "Create"),
            ("record", "Record"),
            ("render", "Render"),
        ],
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name="started by"
    )
    request = models.TextField(blank=True, verbose_name="prompt")
    sources = models.JSONField(default=list, blank=True)
    follow_links = models.BooleanField(default=False)
    generation_settings = models.JSONField(default=dict, blank=True)
    text_only = models.BooleanField(default=False)
    correlation_id = models.CharField(max_length=32)
    status = models.CharField(
        max_length=12,
        default="queued",
        choices=[
            ("queued", "Queued"),
            ("running", "Picked up by a worker"),
            ("ready", "Ready"),
            ("needs_review", "Needs review"),
            ("failed", "Failed"),
            ("stopped", "Stopped"),
        ],
    )
    steps = models.JSONField(default=list, blank=True)
    log = models.JSONField(default=list, blank=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    error_source = models.CharField(max_length=7, blank=True)
    cancel_requested = models.BooleanField(default=False)
    last_seen = models.DateTimeField(default=timezone.now)
    created = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "run"
        verbose_name_plural = "runs"
        ordering = ("-created",)

    def __str__(self):
        return f"{self.get_task_display()}: {self.course} — {self.get_status_display()}"
