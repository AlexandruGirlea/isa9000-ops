import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EntraGroup",
            fields=[
                ("object_id", models.UUIDField(primary_key=True, serialize=False)),
                ("name", models.CharField(blank=True, max_length=255)),
            ],
            options={
                "verbose_name": "Entra group",
                "verbose_name_plural": "Entra groups",
                "ordering": ("name", "object_id"),
            },
        ),
        migrations.CreateModel(
            name="Course",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=500)),
                ("description", models.TextField(blank=True)),
                ("cover", models.CharField(blank=True, max_length=500)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("needs_review", "Needs review"),
                            ("ready", "Ready"),
                        ],
                        default="draft",
                        max_length=12,
                    ),
                ),
                ("manifest", models.JSONField(blank=True, default=dict)),
                ("generation_settings", models.JSONField(blank=True, default=dict)),
                ("html", models.TextField(blank=True)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="courses",
                        to="app_portal.entragroup",
                        verbose_name="Entra group",
                    ),
                ),
                (
                    "shared_groups",
                    models.ManyToManyField(
                        blank=True,
                        help_text="Share published audio with these libraries. Editing stays with the owning library.",
                        related_name="shared_courses",
                        to="app_portal.entragroup",
                        verbose_name="Additional libraries",
                    ),
                ),
            ],
            options={
                "verbose_name": "podcast",
                "verbose_name_plural": "podcasts",
                "ordering": ("-created",),
            },
        ),
        migrations.CreateModel(
            name="LocalTestUser",
            fields=[
                ("number", models.AutoField(primary_key=True, serialize=False)),
                ("session_key", models.CharField(blank=True, max_length=40)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="Profile",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("viewer", "Viewer"), ("creator", "Creator")],
                        default="viewer",
                        max_length=7,
                    ),
                ),
                ("last_seen", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "groups",
                    models.ManyToManyField(
                        blank=True,
                        related_name="people",
                        to="app_portal.entragroup",
                        verbose_name="Entra groups",
                    ),
                ),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={
                "verbose_name": "user",
                "verbose_name_plural": "users",
            },
        ),
        migrations.CreateModel(
            name="Run",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "task",
                    models.CharField(
                        choices=[("create", "Create"), ("record", "Record"), ("render", "Render")],
                        max_length=6,
                    ),
                ),
                ("request", models.TextField(blank=True, verbose_name="prompt")),
                ("sources", models.JSONField(blank=True, default=list)),
                ("follow_links", models.BooleanField(default=False)),
                ("generation_settings", models.JSONField(blank=True, default=dict)),
                ("text_only", models.BooleanField(default=False)),
                ("correlation_id", models.CharField(max_length=32)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Picked up by a worker"),
                            ("ready", "Ready"),
                            ("needs_review", "Needs review"),
                            ("failed", "Failed"),
                            ("stopped", "Stopped"),
                        ],
                        default="queued",
                        max_length=12,
                    ),
                ),
                ("steps", models.JSONField(blank=True, default=list)),
                ("log", models.JSONField(blank=True, default=list)),
                ("result", models.JSONField(blank=True, null=True)),
                ("error", models.TextField(blank=True)),
                ("error_source", models.CharField(blank=True, max_length=7)),
                ("cancel_requested", models.BooleanField(default=False)),
                ("last_seen", models.DateTimeField(default=django.utils.timezone.now)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("finished", models.DateTimeField(blank=True, null=True)),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="runs",
                        to="app_portal.course",
                        verbose_name="podcast",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="started by",
                    ),
                ),
            ],
            options={
                "verbose_name": "run",
                "verbose_name_plural": "runs",
                "ordering": ("-created",),
            },
        ),
    ]
