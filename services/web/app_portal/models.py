from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class EntraGroup(models.Model):
    object_id = models.UUIDField(primary_key=True)
    name = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "Entra group"
        verbose_name_plural = "Entra groups"
        ordering = ("name", "object_id")

    def __str__(self):
        return self.name or f"Unnamed group {str(self.object_id)[:8]}"


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=7, choices=[("viewer", "Viewer"), ("creator", "Creator")], default="viewer"
    )
    groups = models.ManyToManyField(
        EntraGroup, related_name="people", blank=True, verbose_name="Entra groups"
    )
    last_seen = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"

    def __str__(self):
        name = self.user.get_full_name() or self.user.username
        return f"{name} ({self.user.email})" if self.user.email else name


class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(
        EntraGroup, on_delete=models.PROTECT, related_name="courses", verbose_name="Entra group"
    )
    shared_groups = models.ManyToManyField(
        EntraGroup, related_name="shared_courses", blank=True, verbose_name="Additional libraries",
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
