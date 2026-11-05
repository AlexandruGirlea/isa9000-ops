from __future__ import annotations

import uuid

from core.audit import record
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef, Q
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from app_portal.models import Course, EntraGroup, LibraryGrant, Profile
from app_portal.permissions import count_of


def without_popup_links(form):
    for field in form.base_fields.values():
        if isinstance(field.widget, RelatedFieldWidgetWrapper):
            field.widget.can_add_related = field.widget.can_change_related = False
            field.widget.can_view_related = field.widget.can_delete_related = False
    return form


class AuditedAdmin(admin.ModelAdmin):
    def log_addition(self, request, obj, message):
        record(request.user, "admin.add", obj, flag=ADDITION)

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


ID_AS_NAME = r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$"
UNNAMED = Q(name="") | Q(name__regex=ID_AS_NAME)


def content_checks():
    owned = Course.objects.filter(group=OuterRef("pk"))
    shared = Course.objects.filter(shared_groups=OuterRef("pk"), status="ready")
    return Exists(owned), Exists(owned.filter(status="ready")), Exists(shared)


class LibraryViewFilter(admin.SimpleListFilter):
    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return (
            ("enabled", "Enabled"),
            ("discovered", "Hidden"),
            ("archived", "Archived"),
            ("review", "Unnamed, unreviewed, no content"),
        )

    def queryset(self, request, queryset):
        if self.value() == "review":
            owned, _, shared = content_checks()
            return queryset.filter(UNNAMED, ~owned, ~shared, reviewed=False)
        return queryset.filter(status=self.value()) if self.value() else queryset


class LibraryNameFilter(admin.SimpleListFilter):
    title = "display name"
    parameter_name = "named"

    def lookups(self, request, model_admin):
        return (("custom", "Custom name"), ("missing", "Missing name"))

    def queryset(self, request, queryset):
        if self.value() == "custom":
            return queryset.exclude(UNNAMED)
        return queryset.filter(UNNAMED) if self.value() == "missing" else queryset


class LibraryContentFilter(admin.SimpleListFilter):
    title = "content"
    parameter_name = "content"

    def lookups(self, request, model_admin):
        return (
            ("none", "No content"),
            ("owned", "Owned podcasts"),
            ("shared", "Published shared podcasts"),
            ("drafts", "Drafts only"),
        )

    def queryset(self, request, queryset):
        owned, published, shared = content_checks()
        rule = {
            "none": (~owned, ~shared),
            "owned": (owned,),
            "shared": (shared,),
            "drafts": (owned, ~published, ~shared),
        }.get(self.value())
        return queryset.filter(*rule) if rule else queryset


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


@admin.register(EntraGroup)
class EntraGroupAdmin(AuditedAdmin):
    list_display = (
        "__str__", "status", "show_when_empty", "reviewed", "owned_total", "shared_total",
        "user_total", "grant_total", "object_id",
    )
    ordering = ("-status", "name")
    list_filter = (
        LibraryViewFilter, LibraryNameFilter, "reviewed", LibraryContentFilter, "show_when_empty",
    )
    list_per_page = 50
    show_full_result_count = False
    search_fields = ("name", "object_id")
    actions = ("mark_reviewed",)
    detail_links = ("users_link", "grants_link", "owned_link", "shared_link")

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            owned_total=count_of(Course.objects.filter(group=OuterRef("pk")), "group"),
            shared_total=count_of(
                Course.objects.filter(shared_groups=OuterRef("pk")), "shared_groups",
            ),
            user_total=count_of(
                Profile.groups.through.objects.filter(
                    entragroup=OuterRef("pk"), profile__membership="complete",
                ),
                "entragroup",
            ),
            grant_total=count_of(
                LibraryGrant.objects.active().filter(library=OuterRef("pk")), "library",
            ),
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
        return {"status": "enabled", "reviewed": True, **initial}

    def get_fields(self, request, obj=None):
        fields = ("object_id", "name", "status", "show_when_empty", "reviewed")
        return (*fields, *self.detail_links) if obj else fields

    def get_readonly_fields(self, request, obj=None):
        return ("object_id", *self.detail_links) if obj else ()

    @admin.display(description="Owned", ordering="owned_total")
    def owned_total(self, obj):
        return obj.owned_total

    @admin.display(description="Shared", ordering="shared_total")
    def shared_total(self, obj):
        return obj.shared_total

    @admin.display(description="Entra users", ordering="user_total")
    def user_total(self, obj):
        return obj.user_total

    @admin.display(description="Local access", ordering="grant_total")
    def grant_total(self, obj):
        return obj.grant_total

    @admin.display(description="Everyone with access")
    def users_link(self, obj):
        url = reverse("admin:app_portal_profile_changelist")
        return format_html(
            '<a href="{}?library={}">{} from Entra, {} local</a>',
            url, obj.pk, obj.user_total, obj.grant_total,
        )

    @admin.display(description="Local access records")
    def grants_link(self, obj):
        url = reverse("admin:app_portal_librarygrant_changelist")
        return format_html('<a href="{}?library__object_id__exact={}">View</a>', url, obj.pk)

    @admin.display(description="Owned podcasts")
    def owned_link(self, obj):
        url = reverse("admin:app_portal_course_changelist")
        return format_html('<a href="{}?library={}">{}</a>', url, obj.pk, obj.owned_total)

    @admin.display(description="Shared podcasts")
    def shared_link(self, obj):
        url = reverse("admin:app_portal_course_changelist")
        return format_html('<a href="{}?shared_library={}">{}</a>', url, obj.pk, obj.shared_total)

    @admin.action(description="Mark selected as reviewed")
    @transaction.atomic
    def mark_reviewed(self, request, queryset):
        for library in queryset.filter(reviewed=False):
            library.reviewed = True
            library.save(update_fields=["reviewed"])
            record(
                request.user, "admin.change", library, changed_fields=["reviewed"],
                group_id=str(library.pk),
            )

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser


def close_grant(grant, user, state=""):
    grant.revoked, grant.revoked_by = timezone.now(), user
    grant.save(update_fields=["revoked", "revoked_by"])
    record(
        user, "library_access.revoked", grant, changed_fields=["revoked"], state=state,
        group_id=str(grant.library_id), target_id=grant.profile.user.username,
    )


class LibraryGrantForm(forms.ModelForm):
    actor = None

    class Meta:
        model = LibraryGrant
        fields = ("profile", "library", "reason", "expires")

    def clean(self):
        cleaned = super().clean()
        profile, library = cleaned.get("profile"), cleaned.get("library")
        expires = cleaned.get("expires")
        if expires and expires <= timezone.now():
            self.add_error("expires", "Choose a future time.")
        if profile and profile.user == self.actor:
            self.add_error("profile", "You cannot grant library access to yourself.")
        if profile and library and LibraryGrant.objects.active().filter(
            profile=profile, library=library,
        ).exists():
            self.add_error("library", "This person already has this access.")
        return cleaned


class GrantStateFilter(admin.SimpleListFilter):
    title = "state"
    parameter_name = "state"

    def lookups(self, request, model_admin):
        return (("active", "Active"), ("expired", "Expired"), ("revoked", "Revoked"))

    def queryset(self, request, queryset):
        if self.value() == "active":
            return queryset.active()
        if self.value() == "expired":
            return queryset.filter(revoked__isnull=True, expires__lte=timezone.now())
        return queryset.filter(revoked__isnull=False) if self.value() == "revoked" else queryset


@admin.register(LibraryGrant)
class LibraryGrantAdmin(AuditedAdmin):
    form = LibraryGrantForm
    autocomplete_fields = ("profile", "library")
    list_display = ("profile", "library", "state", "expires", "granted_by", "created")
    list_filter = (GrantStateFilter, "created")
    list_select_related = ("profile__user", "library", "granted_by")
    list_per_page = 50
    show_full_result_count = False
    search_fields = (
        "profile__user__first_name", "profile__user__email", "profile__user__username",
        "library__name",
    )
    actions = ("revoke",)

    def get_fields(self, request, obj=None):
        fields = ("profile", "library", "reason", "expires")
        if obj:
            fields += ("state", "granted_by", "created", "revoked", "revoked_by")
        return fields

    def get_form(self, request, obj=None, **kwargs):
        form = without_popup_links(super().get_form(request, obj, **kwargs))
        form.actor = request.user
        return form

    @admin.display(description="State")
    def state(self, obj):
        if obj.revoked:
            return "Revoked"
        return "Expired" if obj.expires and obj.expires <= timezone.now() else "Active"

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except IntegrityError:
            # A simultaneous submission won; the database kept its single open grant.
            self.message_user(request, "This person already has this access.", messages.WARNING)
            return redirect("admin:app_portal_librarygrant_changelist")

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        # An expired grant still occupies the one open slot until it is closed.
        for expired in LibraryGrant.objects.select_related("profile__user").filter(
            profile=obj.profile, library=obj.library, revoked__isnull=True,
            expires__lte=timezone.now(),
        ):
            close_grant(expired, request.user, state="expired")
        obj.granted_by = request.user
        super().save_model(request, obj, form, change)

    def log_addition(self, request, obj, message):
        record(
            request.user, "library_access.granted", obj, flag=ADDITION,
            group_id=str(obj.library_id), target_id=obj.profile.user.username,
        )

    @admin.action(description="Revoke selected access")
    @transaction.atomic
    def revoke(self, request, queryset):
        grants = LibraryGrant.objects.select_related("profile__user").select_for_update(
            of=("self",),
        ).filter(pk__in=list(queryset.values_list("pk", flat=True)), revoked__isnull=True)
        for grant in grants:
            close_grant(grant, request.user)

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
