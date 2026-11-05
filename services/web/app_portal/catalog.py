from __future__ import annotations

from urllib.parse import quote

from core.views import page
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_safe
from isa9000.presentation_website import _total_duration

from app_portal.permissions import (
    addable_libraries,
    group_for,
    groups_for,
    is_creator,
    visible_courses,
    visible_libraries,
)

CATALOG_PAGE_SIZE = 24


def catalog_query(request):
    return request.GET.get("q", "").strip()[:200]


def catalog_page(request, rows):
    return Paginator(rows, CATALOG_PAGE_SIZE).get_page(request.GET.get("page"))


@require_safe
def libraries(request):
    query = catalog_query(request)
    rows = visible_libraries(request.user)
    if query:
        rows = rows.filter(name__icontains=query)
    pages = catalog_page(request, rows)
    items = []
    for group in pages:
        shared_is_newer = group.shared_updated and (
            not group.owned_updated or group.shared_updated > group.owned_updated
        )
        course_id, cover, updated = (
            (group.shared_id, group.shared_cover, group.shared_updated)
            if shared_is_newer else (group.owned_id, group.owned_cover, group.owned_updated)
        )
        items.append(
            {
                "group": group,
                "count": group.podcast_total,
                "updated": updated,
                "cover": f"/media/{course_id}/{quote(cover)}" if cover else "",
            }
        )
    return page(
        request,
        "libraries.html",
        {
            "libraries": items,
            "pages": pages,
            "query": query,
            "creator": is_creator(request.user),
            "directory_unresolved": not request.user.is_superuser
            and request.user.profile.membership != "complete",
        },
    )


@require_safe
def add_podcast(request):
    if not is_creator(request.user):
        raise Http404
    query = catalog_query(request)
    rows = addable_libraries(request.user)
    if query:
        rows = rows.filter(name__icontains=query)
    return page(
        request,
        "add_podcast.html",
        {"pages": catalog_page(request, rows), "query": query},
    )


@require_safe
def library(request, group_id):
    group = group_for(request.user, group_id)
    creator = is_creator(request.user)
    query = catalog_query(request)
    rows = visible_courses(request.user, group).defer("html").order_by("-created", "-id")
    if query:
        rows = rows.filter(Q(title__icontains=query) | Q(description__icontains=query))
    pages = catalog_page(request, rows)
    managed_groups = (
        set(
            groups_for(request.user)
            .filter(pk__in={course.group_id for course in pages})
            .values_list("pk", flat=True)
        )
        if creator else set()
    )
    items = []
    for course in pages:
        episodes = course.manifest.get("episodes", [])
        seconds = int(sum(episode.get("seconds") or 0 for episode in episodes))
        items.append(
            {
                "course": course,
                "can_manage": course.group_id in managed_groups,
                "episodes": len(episodes),
                "duration": _total_duration(seconds) if seconds else "",
                "cover": f"/media/{course.id}/{quote(course.cover)}" if course.cover else "",
            }
        )
    return page(
        request,
        "library.html",
        {
            "group": group,
            "courses": items,
            "pages": pages,
            "query": query,
            "creator": creator,
            "can_add": addable_libraries(request.user).filter(pk=group.pk).exists(),
        },
    )


@require_safe
def library_index(request, group_id):
    group_for(request.user, group_id)
    return redirect("library", group_id=group_id)
