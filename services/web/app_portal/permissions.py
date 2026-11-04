from __future__ import annotations

from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from app_portal.models import Course, EntraGroup


def is_creator(user):
    return user.is_superuser or user.profile.role == "creator"


def groups_for(user):
    return EntraGroup.objects.all() if user.is_superuser else user.profile.groups.all()


def group_for(user, group_id, manage=False):
    if manage and not is_creator(user):
        raise Http404
    return get_object_or_404(groups_for(user), object_id=group_id)


def course_for(user, course_id, manage=False):
    course = get_object_or_404(Course.objects.select_related("group"), id=course_id)
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
    return Course.objects.filter(owned | Q(shared_groups=group, status="ready")).distinct()
