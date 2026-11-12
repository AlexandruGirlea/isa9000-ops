from __future__ import annotations

import json

from core.audit import record
from core.branding import site
from django import forms
from django.contrib import admin
from django.contrib.admin.models import CHANGE, DELETION, LogEntry
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.functional import lazy
from django.utils.html import format_html

from app_portal.library_admin import (
    AuditedAdmin,
    LibraryFilter,
    record_creator,
    without_popup_links,
)
from app_portal.models import (
    ContactRequest,
    Course,
    EntraGroup,
    Profile,
    Run,
    person_name,
)
from app_portal.permissions import count_of, people_with_access
from shared import storage
from shared.descriptions import render_description

admin.site.unregister(User)
admin.site.unregister(Group)
admin.site.site_header = "Admin Dashboard"
admin.site.site_title = lazy(lambda: site()["title"], str)()
admin.site.site_url = "/libraries/"
# Users are stored under their Entra object ID; everywhere in the admin, show who they are.
User.__str__ = person_name
LogEntry._meta.verbose_name = "activity entry"
LogEntry._meta.verbose_name_plural = "activity"


class PersonLibraryFilter(LibraryFilter):
    def filter_library(self, queryset, library):
        return queryset.filter(pk__in=people_with_access(library).values("pk"))


class CreatorLibraryFilter(LibraryFilter):
    title = "creator in library"
    parameter_name = "creator_library"

    def filter_library(self, queryset, library):
        creators = Profile.creator_libraries.through.objects.filter(entragroup=library)
        return queryset.filter(pk__in=creators.values("profile"))


class CreatorFilter(admin.SimpleListFilter):
    title = "creator"
    parameter_name = "creator"

    def lookups(self, request, model_admin):
        return (("yes", "Creator in a library"), ("no", "Not a creator"))

    def queryset(self, request, queryset):
        assigned = Exists(
            Profile.creator_libraries.through.objects.filter(profile=OuterRef("pk"))
        )
        if self.value() == "yes":
            return queryset.filter(assigned)
        return queryset.exclude(assigned) if self.value() == "no" else queryset


class OwningLibraryFilter(LibraryFilter):
    title = "owning library"


class SharedLibraryFilter(LibraryFilter):
    title = "shared library"
    parameter_name = "shared_library"
    lookup = "shared_groups"


class RunLibraryFilter(LibraryFilter):
    lookup = "course__group"


class PersonForm(forms.ModelForm):
    actor = None
    is_active = forms.BooleanField(
        label="Active", required=False,
        help_text="Inactive users cannot sign in or use the website.",
    )

    class Meta:
        model = Profile
        fields = ("creator_libraries", "local_admin", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["is_active"].initial = self.instance.user.is_active
            if self.instance.user == self.actor:
                self.fields["local_admin"].disabled = True
                self.fields["is_active"].disabled = True
            member = Profile.groups.through.objects.filter(profile=self.instance)
            chosen = Profile.creator_libraries.through.objects.filter(profile=self.instance)
            field = self.fields["creator_libraries"]
            field.queryset = EntraGroup.objects.filter(
                Q(pk__in=member.values("entragroup")) | Q(pk__in=chosen.values("entragroup"))
            ).order_by("status", "name", "object_id")
            field.label_from_instance = group_label


def group_label(group):
    # Name and ID together, so the filter box above each list finds a group by either.
    name = "" if group.name == str(group.pk) else f"{group.name} · "
    return f"{name}{group.pk}" + ("" if group.status == "enabled" else " · not enabled")


@admin.register(Profile)
class PersonAdmin(AuditedAdmin):
    form = PersonForm
    list_display = ("__str__", "access_role", "active", "membership", "last_seen")
    list_filter = (
        "user__is_active", CreatorFilter, "local_admin", "membership", PersonLibraryFilter,
        CreatorLibraryFilter,
    )
    list_select_related = ("user",)
    list_per_page = 50
    show_full_result_count = False
    search_fields = ("user__first_name", "user__last_name", "user__email", "user__username")
    readonly_fields = ("user", "membership", "entra_groups", "last_seen")
    fields = (
        "user", "is_active", "creator_libraries", "local_admin", "membership", "entra_groups",
        "last_seen",
    )
    ordering = ("user__first_name", "pk")

    def get_queryset(self, request):
        chosen = Profile.creator_libraries.through.objects.filter(profile=OuterRef("pk"))
        return super().get_queryset(request).select_related("user").annotate(
            creator_total=count_of(chosen.filter(entragroup__status="enabled"), "profile"),
            chosen_total=count_of(chosen, "profile"),
        )

    def get_form(self, request, obj=None, **kwargs):
        form = without_popup_links(super().get_form(request, obj, **kwargs))
        form.actor = request.user
        return form

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "creator_libraries":
            # Django's two-box selector, as on its own user page for groups.
            kwargs["widget"] = FilteredSelectMultiple("Entra groups", is_stacked=False)
            kwargs["help_text"] = (
                "Move a group to the right to make this person a creator there. The left box "
                "lists the Entra groups this person belongs to; type a name or a group ID in "
                "the filter to find one. A group that is not enabled does nothing until it "
                "is enabled as a library."
            )
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def save_related(self, request, form, formsets, change):
        profile = form.instance
        before = set(profile.creator_libraries.values_list("pk", flat=True))
        super().save_related(request, form, formsets, change)
        after = set(profile.creator_libraries.values_list("pk", flat=True))
        for library in sorted(before ^ after):
            record_creator(request.user, profile, library, granted=library in after)
        # The administrator has now decided this person's libraries: their next sign-in
        # must not add any on account of the old global role.
        Profile.objects.filter(pk=profile.pk, role="creator").update(role="viewer")

    @admin.display(boolean=True, description="Active", ordering="user__is_active")
    def active(self, obj):
        return obj.user.is_active

    def save_model(self, request, obj, form, change):
        if "is_active" in form.changed_data:
            obj.user.is_active = form.cleaned_data["is_active"]
            obj.user.save(update_fields=["is_active"])
        super().save_model(request, obj, form, change)
        if "local_admin" in form.changed_data:
            if not obj.local_admin:
                # The next request restores this if the person is still in the Entra group.
                obj.user.is_superuser = obj.user.is_staff = False
                obj.user.save(update_fields=["is_superuser", "is_staff"])
            record(
                request.user,
                f"admin.app_admin_{'granted' if obj.local_admin else 'revoked'}",
                obj, changed_fields=["local_admin"], target_id=obj.user.username,
            )

    @admin.display(description="Role", ordering="creator_total")
    def access_role(self, obj):
        if obj.local_admin or obj.user.is_superuser:
            return "Administrator"
        if not obj.creator_total:
            # The old global role with nothing chosen: they get the libraries of a sign-in.
            pending = obj.role == "creator" and not obj.chosen_total
            return "Creator from next sign-in" if pending else "Viewer"
        return f"Creator in {obj.creator_total} " + (
            "library" if obj.creator_total == 1 else "libraries"
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Entra groups in the last complete sign-in")
    def entra_groups(self, obj):
        return ", ".join(str(group) for group in obj.groups.all())


class CourseSharingForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ("shared_groups",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Existing shares stay selectable so saving never silently removes a hidden library.
        self.fields["shared_groups"].queryset = EntraGroup.objects.filter(
            Q(status="enabled") | Q(shared_courses=self.instance)
        ).exclude(pk=self.instance.group_id).distinct()


@admin.register(Course)
class CourseAdmin(AuditedAdmin):
    form = CourseSharingForm
    autocomplete_fields = ("shared_groups",)
    list_display = ("title", "owning_library", "created_by", "status", "created", "view_page")
    list_filter = (OwningLibraryFilter, SharedLibraryFilter, "status", "created")
    list_per_page = 50
    show_full_result_count = False
    search_fields = ("title", "created_by__email", "created_by__first_name")
    readonly_fields = (
        "owning_library",
        "created_by",
        "last_edited",
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

    def get_form(self, request, obj=None, **kwargs):
        return without_popup_links(super().get_form(request, obj, **kwargs))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    @admin.display(description="Last edited by")
    def last_edited(self, obj):
        entry = (
            LogEntry.objects.filter(
                content_type=ContentType.objects.get_for_model(Course),
                object_id=str(obj.pk), action_flag=CHANGE,
            )
            .exclude(change_message__contains='"actor_id": "worker"')
            .select_related("user")
            .order_by("-action_time")
            .first()
        )
        if entry is None:
            return "-"
        return f"{person_name(entry.user)}, {entry.action_time:%d %b %Y, %H:%M} UTC"

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


@admin.register(ContactRequest)
class ContactRequestAdmin(AuditedAdmin):
    list_display = ("__str__", "status", "created_by", "created", "updated_by", "updated")
    list_filter = ("status", "created")
    list_select_related = ("created_by", "updated_by")
    list_per_page = 50
    show_full_result_count = False
    search_fields = ("body", "created_by__first_name", "created_by__email")
    readonly_fields = ("created_by", "created", "message", "updated_by", "updated")
    fields = ("created_by", "created", "message", "status", "updated_by", "updated")

    @admin.display(description="Message")
    def message(self, obj):
        return render_description(obj.body)

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data:
            obj.updated_by, obj.updated = request.user, timezone.now()
        super().save_model(request, obj, form, change)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser


@admin.register(Run)
class RequestAdmin(admin.ModelAdmin):
    list_display = ("__str__", "requested_by", "task", "status", "created", "correlation_id")
    list_filter = (RunLibraryFilter, "status", "task", "created")
    list_per_page = 50
    show_full_result_count = False
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
                    "course", "task", "requested_by", "status", "canceled_by", "created",
                    "finished", "duration", "error",
                ),
            },
        ),
        (
            "Run details",
            {
                "fields": (
                    "id", "request", "sources", "follow_links", "generation_settings",
                    "text_only", "episodes", "correlation_id",
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
    list_filter = ("action_time", "content_type", "action_flag")
    list_select_related = ("user", "content_type")
    search_fields = ("object_repr", "user__email", "user__first_name", "change_message")
    readonly_fields = tuple(field.name for field in LogEntry._meta.fields)
    fields = readonly_fields

    @admin.display(description="Actor", ordering="user")
    def actor(self, obj):
        if '"actor_id": "worker"' in obj.change_message:
            return "Worker"
        if '"actor_id": "operator_cli"' in obj.change_message:
            return "Operator command"
        return person_name(obj.user)

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
