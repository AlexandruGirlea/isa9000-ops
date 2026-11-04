from __future__ import annotations

import json

from core.audit import record
from core.branding import site
from django import forms
from django.contrib import admin
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.functional import lazy
from django.utils.html import format_html

from app_portal.models import Course, EntraGroup, Profile, Run
from shared import storage

admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.site_header = "Admin Dashboard"
admin.site.site_title = lazy(lambda: site()["title"], str)()
admin.site.site_url = "/libraries/"
LogEntry._meta.verbose_name = "activity entry"
LogEntry._meta.verbose_name_plural = "activity"


class AuditedAdmin(admin.ModelAdmin):
    def log_change(self, request, obj, message):
        fields = (
            [field for entry in message for field in entry.get("changed", {}).get("fields", [])]
            if isinstance(message, list) else ["role"] if isinstance(obj, Profile) else []
        )
        record(
            request.user, "admin.change", obj, changed_fields=fields,
            state=obj.role if isinstance(obj, Profile) else "",
            target_id=obj.user.username if isinstance(obj, Profile) else str(obj.pk),
        )

@admin.register(EntraGroup)
class EntraGroupAdmin(AuditedAdmin):
    list_display = ("__str__", "object_id")
    search_fields = ("name", "object_id")
    readonly_fields = ("object_id", "people_list", "courses_list")
    fields = ("name", *readonly_fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Users")
    def people_list(self, obj):
        return ", ".join(str(person) for person in obj.people.select_related("user"))

    @admin.display(description="Podcasts")
    def courses_list(self, obj):
        return ", ".join(str(course) for course in Course.objects.filter(
            Q(group=obj) | Q(shared_groups=obj)
        ).distinct())


class PersonForm(forms.ModelForm):
    is_active = forms.BooleanField(
        label="Active", required=False,
        help_text="Inactive users cannot sign in or use the website.",
    )

    class Meta:
        model = Profile
        fields = ("role", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["is_active"].initial = self.instance.user.is_active


@admin.register(Profile)
class PersonAdmin(AuditedAdmin):
    form = PersonForm
    list_display = ("__str__", "access_role", "active", "last_seen")
    list_filter = ("user__is_active", "role", "groups")
    search_fields = ("user__first_name", "user__last_name", "user__email", "user__username")
    readonly_fields = ("user", "entra_groups", "last_seen")
    fields = ("user", "is_active", "role", "entra_groups", "last_seen")
    actions = ("make_creator", "make_viewer")

    @admin.display(boolean=True, description="Active", ordering="user__is_active")
    def active(self, obj):
        return obj.user.is_active

    def save_model(self, request, obj, form, change):
        if "is_active" in form.changed_data:
            obj.user.is_active = form.cleaned_data["is_active"]
            obj.user.save(update_fields=["is_active"])
        super().save_model(request, obj, form, change)

    @admin.display(description="Role", ordering="role")
    def access_role(self, obj):
        return "Administrator" if obj.user.is_superuser else obj.get_role_display()

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Entra groups")
    def entra_groups(self, obj):
        return ", ".join(str(group) for group in obj.groups.all())

    @admin.action(description="Make creator")
    @transaction.atomic
    def make_creator(self, request, queryset):
        for profile in queryset:
            profile.role = "creator"
            profile.save(update_fields=["role"])
            self.log_change(request, profile, "Changed role to creator.")

    @admin.action(description="Make viewer")
    @transaction.atomic
    def make_viewer(self, request, queryset):
        for profile in queryset:
            profile.role = "viewer"
            profile.save(update_fields=["role"])
            self.log_change(request, profile, "Changed role to viewer.")


class CourseSharingForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ("shared_groups",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["shared_groups"].queryset = EntraGroup.objects.exclude(
            pk=self.instance.group_id
        )


@admin.register(Course)
class CourseAdmin(AuditedAdmin):
    form = CourseSharingForm
    filter_horizontal = ("shared_groups",)
    list_display = ("title", "owning_library", "created_by", "status", "created", "view_page")
    list_filter = ("group", "shared_groups", "status", "created")
    search_fields = ("title", "created_by__email", "created_by__first_name")
    readonly_fields = (
        "owning_library",
        "created_by",
        "title",
        "description",
        "cover",
        "status",
        "generation_settings",
        "manifest",
        "created",
        "updated",
        "view_page",
    )
    fieldsets = (
        ("Library access", {"fields": ("owning_library", "shared_groups")}),
        (None, {"fields": tuple(
            field for field in readonly_fields if field not in {"manifest", "owning_library"}
        )}),
        ("Manifest", {"fields": ("manifest",), "classes": ("collapse",)}),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Owning library", ordering="group__name")
    def owning_library(self, obj):
        return obj.group

    def save_model(self, request, obj, form, change):
        if "shared_groups" in form.changed_data:
            obj.save(update_fields=["updated"])

    def save_related(self, request, form, formsets, change):
        course = form.instance
        before = set(course.shared_groups.values_list("pk", flat=True))
        super().save_related(request, form, formsets, change)
        after = set(course.shared_groups.values_list("pk", flat=True))
        changes = (("course.shared", after - before), ("course.unshared", before - after))
        for action, groups in changes:
            for group_id in groups:
                record(
                    request.user, action, course, changed_fields=["shared_groups"],
                    course_id=str(course.id), group_id=str(group_id),
                )

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser and (
            obj is None or not obj.runs.filter(status__in=["queued", "running"]).exists()
        )

    @admin.display(description="View page")
    def view_page(self, obj):
        return format_html('<a href="/courses/{}/">View page</a>', obj.id)

    def get_deleted_objects(self, objs, request):
        deleted, counts, permissions, protected = super().get_deleted_objects(objs, request)
        permissions.discard(str(Run._meta.verbose_name))
        for course in objs:
            if course.runs.filter(status__in=["queued", "running"]).exists():
                protected.append("A run is still in progress.")
            files = {"podcast.json", course.cover}
            for episode in course.manifest.get("episodes", []):
                files.update((episode.get("audio"), episode.get("transcript")))
            deleted.append([f"courses/{course.id}/{name}" for name in sorted(files - {None, ""})])
        return deleted, counts, permissions, protected

    def delete_model(self, request, obj):
        with transaction.atomic():
            course = get_object_or_404(Course.objects.select_for_update(), pk=obj.pk)
            if not self.has_delete_permission(request, course):
                raise PermissionDenied("Wait for the current run to finish before deleting.")
            record(
                request.user, "admin.delete", course, flag=DELETION,
                course_id=str(course.id), group_id=str(course.group_id),
            )
            storage.delete_prefix(f"courses/{course.id}")
            course.delete()

    def log_deletions(self, request, queryset):
        return None

    def delete_queryset(self, request, queryset):
        for course in queryset:
            if self.has_delete_permission(request, course):
                self.delete_model(request, course)


@admin.register(Run)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("__str__", "requested_by", "task", "status", "created", "correlation_id")
    list_filter = ("course__group", "status", "task", "created")
    search_fields = (
        "course__title",
        "requested_by__email",
        "requested_by__first_name",
        "correlation_id",
    )
    readonly_fields = (*(field.name for field in Run._meta.fields), "duration")
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "course", "task", "requested_by", "status", "created", "finished",
                    "duration", "error",
                ),
            },
        ),
        (
            "Run details",
            {
                "fields": (
                    "id", "request", "sources", "follow_links", "generation_settings",
                    "text_only", "correlation_id",
                    "steps", "log", "result", "error_source", "cancel_requested", "last_seen",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Duration")
    def duration(self, obj):
        return obj.finished - obj.created if obj.finished else "In progress"


@admin.register(LogEntry)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("action_time", "actor", "content_type", "object_repr", "summary", "reference")
    list_filter = ("action_time", "user", "content_type", "action_flag")
    list_select_related = ("user", "content_type")
    search_fields = ("object_repr", "user__email", "user__first_name", "change_message")
    readonly_fields = tuple(field.name for field in LogEntry._meta.fields)
    fields = readonly_fields

    @admin.display(description="Actor", ordering="user")
    def actor(self, obj):
        if '"actor_id": "worker"' in obj.change_message:
            return "Worker"
        return obj.user.get_full_name() or obj.user.email or obj.user.username

    @admin.display(description="Action")
    def summary(self, obj):
        return obj.get_change_message()

    @admin.display(description="Correlation ID")
    def reference(self, obj):
        try:
            entries = json.loads(obj.change_message)
        except (ValueError, TypeError):
            return ""
        if not isinstance(entries, list):
            return ""
        return next(
            (entry["audit"].get("correlation_id", "") for entry in entries if "audit" in entry), ""
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
