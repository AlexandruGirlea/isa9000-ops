from __future__ import annotations

import uuid

from core.audit import record
from django import forms
from django.contrib import admin
from django.contrib.admin.models import ADDITION
from django.contrib.admin.widgets import FilteredSelectMultiple, RelatedFieldWidgetWrapper
from django.db.models import OuterRef, Q
from django.urls import reverse
from django.utils.html import format_html

from app_portal.models import Course, EntraGroup, Profile
from app_portal.permissions import count_of, people_with_access


def without_popup_links(form):
    for field in form.base_fields.values():
        if isinstance(field.widget, RelatedFieldWidgetWrapper):
            field.widget.can_add_related = field.widget.can_change_related = False
            field.widget.can_view_related = field.widget.can_delete_related = False
    return form


def record_creator(actor, profile, library_id, granted):
    record(
        actor, f"library_creator.{'granted' if granted else 'revoked'}", profile,
        changed_fields=["creator_libraries"], group_id=str(library_id),
        target_id=profile.user.username,
    )


class AuditedAdmin(admin.ModelAdmin):
    def log_addition(self, request, obj, message):
        record(request.user, "admin.add", obj, flag=ADDITION)

    def log_change(self, request, obj, message):
        fields = (
            [field for entry in message for field in entry.get("changed", {}).get("fields", [])]
            if isinstance(message, list) else []
        )
        record(
            request.user, "admin.change", obj, changed_fields=fields,
            target_id=obj.user.username if isinstance(obj, Profile) else str(obj.pk),
        )


class LibraryFilter(admin.SimpleListFilter):
    title = "library"
    parameter_name = "library"
    lookup = "group"

    def lookups(self, request, model_admin):
        libraries = EntraGroup.objects.filter(status="enabled")[:50]
        return [(str(library.pk), str(library)) for library in libraries]

    def filter_library(self, queryset, library):
        return queryset.filter(**{self.lookup: library})

    def queryset(self, request, queryset):
        try:
            return self.filter_library(queryset, uuid.UUID(self.value() or ""))
        except ValueError:
            return queryset


class LibraryForm(forms.ModelForm):
    creators = forms.ModelMultipleChoiceField(
        queryset=Profile.objects.none(), required=False,
        widget=RelatedFieldWidgetWrapper(
            FilteredSelectMultiple("users", is_stacked=False),
            Profile.creator_libraries.through._meta.get_field("profile").remote_field,
            admin.site, can_add_related=False,
        ),
        help_text="Move a person to the right to make them a creator in this library. The left "
        "box lists the members of this Entra group; type a name or an email address in the "
        "filter to find one.",
    )

    class Meta:
        model = EntraGroup
        fields = ("object_id", "name", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance._state.adding:
            chosen = Profile.creator_libraries.through.objects.filter(entragroup=self.instance)
            self.fields["creators"].queryset = Profile.objects.select_related("user").filter(
                Q(pk__in=people_with_access(self.instance).values("pk"))
                | Q(pk__in=chosen.values("profile"))
            ).order_by("user__first_name", "pk")
            self.initial["creators"] = list(self.instance.creators.values_list("pk", flat=True))


@admin.register(EntraGroup)
class EntraGroupAdmin(AuditedAdmin):
    form = LibraryForm
    list_display = (
        "__str__", "status", "owned_total", "shared_total", "people_total", "creators_total",
        "object_id",
    )
    ordering = ("status", "name")
    list_filter = ("status",)
    list_per_page = 50
    show_full_result_count = False
    search_fields = ("name", "object_id")
    detail_links = ("users_link", "creators_link", "owned_link", "shared_link")

    def get_queryset(self, request):
        members = Profile.groups.through.objects.filter(profile__membership="complete")
        creators = Profile.creator_libraries.through.objects.filter(entragroup=OuterRef("pk"))
        return super().get_queryset(request).annotate(
            creators_total=count_of(creators, "entragroup"),
            owned_total=count_of(Course.objects.filter(group=OuterRef("pk")), "group"),
            shared_total=count_of(
                Course.objects.filter(shared_groups=OuterRef("pk")), "shared_groups",
            ),
            people_total=count_of(members.filter(entragroup=OuterRef("pk")), "entragroup"),
        )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if "object_id" in form.base_fields:
            form.base_fields["object_id"].label = "Entra group ID"
            form.base_fields["object_id"].help_text = (
                "The group's Object ID in Entra. Groups appear here by themselves once a member "
                "signs in, so you rarely need this form."
            )
        return form

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        return {"status": "enabled", **initial}

    def get_fields(self, request, obj=None):
        fields = ("object_id", "name", "status")
        return (*fields, "creators", *self.detail_links) if obj else fields

    def get_readonly_fields(self, request, obj=None):
        return ("object_id", *self.detail_links) if obj else ()

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        library = form.instance
        before = set(library.creators.values_list("pk", flat=True))
        after = {profile.pk for profile in form.cleaned_data["creators"]}
        library.creators.set(after)
        changed = Profile.objects.select_related("user").filter(pk__in=before ^ after)
        for profile in changed.order_by("pk"):
            record_creator(request.user, profile, library.pk, granted=profile.pk in after)
        # An administrator has now decided for these people: see PersonAdmin.save_related.
        changed.filter(role="creator").update(role="viewer")

    @admin.display(description="Owned", ordering="owned_total")
    def owned_total(self, obj):
        return obj.owned_total

    @admin.display(description="Shared", ordering="shared_total")
    def shared_total(self, obj):
        return obj.shared_total

    @admin.display(description="People with access", ordering="people_total")
    def people_total(self, obj):
        return obj.people_total

    @admin.display(description="Creators", ordering="creators_total")
    def creators_total(self, obj):
        return obj.creators_total

    @admin.display(description="People with access")
    def users_link(self, obj):
        url = reverse("admin:app_portal_profile_changelist")
        label = "1 person" if obj.people_total == 1 else f"{obj.people_total} people"
        return format_html('<a href="{}?library={}">{}</a>', url, obj.pk, label)

    @admin.display(description="Creators in the users list")
    def creators_link(self, obj):
        url = reverse("admin:app_portal_profile_changelist")
        label = "1 creator" if obj.creators_total == 1 else f"{obj.creators_total} creators"
        return format_html('<a href="{}?creator_library={}">{}</a>', url, obj.pk, label)

    @admin.display(description="Owned podcasts")
    def owned_link(self, obj):
        url = reverse("admin:app_portal_course_changelist")
        return format_html('<a href="{}?library={}">{}</a>', url, obj.pk, obj.owned_total)

    @admin.display(description="Shared podcasts")
    def shared_link(self, obj):
        url = reverse("admin:app_portal_course_changelist")
        return format_html('<a href="{}?shared_library={}">{}</a>', url, obj.pk, obj.shared_total)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
