from __future__ import annotations

from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce
from django.http import Http404
from django.shortcuts import get_object_or_404

from app_portal.models import Course, EntraGroup, Profile


def groups_for(user):
    enabled = EntraGroup.objects.filter(status="enabled")
    if user.is_superuser:
        return enabled
    # Only this sign-in's groups count. A stored membership may come from another sign-in.
    return enabled.filter(pk__in=user.directory_groups)


def assigned_libraries(user):
    # Read once per request: every request builds its own user object. A group chosen before
    # it is enabled as a library gives nothing yet, so only enabled ones count.
    if not hasattr(user, "assigned_libraries"):
        user.assigned_libraries = set(
            user.profile.creator_libraries.filter(status="enabled").values_list("pk", flat=True)
        )
    return user.assigned_libraries


def assigned_rows(user):
    return Profile.creator_libraries.through.objects.filter(profile=user.profile).values(
        "entragroup"
    )


def is_creator(user):
    # Opens the creator pages. Each of them lists only the libraries the person can use.
    return user.is_superuser or bool(assigned_libraries(user))


def creator_libraries(user):
    # Being a creator never opens a library: the person also needs access to it.
    libraries = groups_for(user)
    return libraries if user.is_superuser else libraries.filter(pk__in=assigned_rows(user))


def creates_in(user, library_id):
    return (
        user.is_superuser or library_id in assigned_libraries(user)
    ) and groups_for(user).filter(pk=library_id).exists()


def count_of(rows, group_field):
    return Coalesce(
        Subquery(rows.order_by().values(group_field).annotate(total=Count("pk")).values("total")),
        0,
    )


def visible_libraries(user):
    owned = Course.objects.filter(group=OuterRef("pk"))
    if not user.is_superuser:
        # Drafts count only in the libraries where this person is a creator.
        owned = owned.filter(Q(status="ready") | Q(group__in=assigned_rows(user)))
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
    if user.is_superuser:
        return libraries
    # Pagination only needs to know whether a podcast exists. Counting every matching
    # podcast here repeats both correlated aggregates across all accessible libraries.
    return libraries.filter(
        Q(pk__in=assigned_rows(user)) | Q(Exists(owned)) | Q(Exists(shared))
    )


def addable_libraries(user):
    if not is_creator(user):
        return EntraGroup.objects.none()
    return creator_libraries(user)


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
    return creates_in(user, course.group_id)


def visible_courses(group, drafts):
    owned = Q(group=group)
    if not drafts:
        owned &= Q(status="ready")
    shared = Course.shared_groups.through.objects.filter(entragroup=group).values("course")
    return Course.objects.filter(owned | Q(pk__in=shared, status="ready"), group__status="enabled")


def people_with_access(library):
    members = Profile.groups.through.objects.filter(
        entragroup=library, profile__membership="complete",
    ).values("profile")
    return Profile.objects.filter(pk__in=members)
