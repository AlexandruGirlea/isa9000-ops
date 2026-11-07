from __future__ import annotations

from django.db.models import Count, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404

from app_portal.models import Course, EntraGroup, LibraryGrant, Profile


def is_creator(user):
    return user.is_superuser or user.profile.role == "creator"


def groups_for(user):
    enabled = EntraGroup.objects.filter(status="enabled")
    if user.is_superuser:
        return enabled
    access = Q(pk__in=LibraryGrant.objects.active().filter(profile=user.profile).values("library"))
    # Only this sign-in's groups count. A stored membership may come from another sign-in.
    if user.directory_groups:
        access |= Q(pk__in=user.directory_groups)
    return enabled.filter(access)


def count_of(rows, group_field):
    return Coalesce(
        Subquery(rows.order_by().values(group_field).annotate(total=Count("pk")).values("total")),
        0,
    )


def visible_libraries(user):
    creator = is_creator(user)
    owned = Course.objects.filter(group=OuterRef("pk"))
    if not creator:
        owned = owned.filter(status="ready")
    shared = Course.objects.filter(
        shared_groups=OuterRef("pk"), status="ready", group__status="enabled",
    )
    # Separate branches stay on their indexes; one OR across both scans every course per library.
    newest = {
        f"{branch}_{field}": Subquery(rows.order_by("-updated", "-id").values(field)[:1])
        for branch, rows in (("owned", owned), ("shared", shared))
        for field in ("id", "cover", "updated")
    }
    libraries = groups_for(user).annotate(
        podcast_total=count_of(owned, "group") + count_of(shared, "shared_groups"), **newest,
    )
    return libraries if creator else libraries.filter(podcast_total__gt=0)


def addable_libraries(user):
    if not is_creator(user):
        return EntraGroup.objects.none()
    return groups_for(user)


def group_for(user, group_id, manage=False):
    libraries = addable_libraries(user) if manage else visible_libraries(user)
    return get_object_or_404(libraries, object_id=group_id)


def course_for(user, course_id, manage=False):
    course = get_object_or_404(
        Course.objects.select_related("group"), id=course_id, group__status="enabled"
    )
    if manage or course.status != "ready":
        if not can_manage_course(user, course):
            raise Http404
    elif not (
        groups_for(user).filter(pk=course.group_id).exists()
        or course.shared_groups.filter(pk__in=groups_for(user)).exists()
    ):
        raise Http404
    return course


def can_manage_course(user, course):
    return is_creator(user) and groups_for(user).filter(pk=course.group_id).exists()


def visible_courses(user, group):
    owned = Q(group=group)
    if not is_creator(user):
        owned &= Q(status="ready")
    shared = Course.shared_groups.through.objects.filter(entragroup=group).values("course")
    return Course.objects.filter(owned | Q(pk__in=shared, status="ready"), group__status="enabled")


def people_with_access(library):
    members = Profile.groups.through.objects.filter(
        entragroup=library, profile__membership="complete",
    ).values("profile")
    grants = LibraryGrant.objects.active().filter(library=library).values("profile")
    return Profile.objects.filter(Q(pk__in=members) | Q(pk__in=grants))


def access_sources(profile):
    sources = {}
    if profile.membership == "complete":
        for library in profile.groups.filter(status="enabled"):
            sources.setdefault(library, []).append("Entra membership")
    grants = LibraryGrant.objects.active().filter(profile=profile, library__status="enabled")
    for grant in grants.select_related("library"):
        expiry = f" until {grant.expires:%d %b %Y %H:%M} UTC" if grant.expires else ""
        sources.setdefault(grant.library, []).append(f"Local access{expiry}")
    return sources
