from __future__ import annotations

import uuid

from app_portal.models import Course, EntraGroup
from django.db import connection
from django.test.utils import CaptureQueriesContext

from .helpers import LibraryCase, administrator, token


class ScaleTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.groups = [
            EntraGroup(
                object_id=uuid.uuid4(), name=f"Library {index:03}",
                status="enabled" if index % 3 else "discovered",
            )
            for index in range(300)
        ]
        EntraGroup.objects.bulk_create(self.groups)
        Course.objects.bulk_create([
            Course(
                group=self.groups[index % 300], created_by=self.user, status="ready",
                title=f"Podcast {index}", description="x" * 50, cover=f"c{index}.png",
            )
            for index in range(3000)
        ])
        self.profile.groups.set([group.pk for group in self.groups])

    def count(self, path, headers, **data):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(path, data, **headers)
        self.assertEqual(response.status_code, 200)
        return len(queries), response

    def test_portal_queries_and_rows_stay_bounded(self):
        token_headers = token(groups=[str(group.pk) for group in self.groups[:200]])
        library = self.groups[1]
        for path in ("/libraries/", "/libraries/new/", f"/libraries/{library.pk}/"):
            with self.subTest(path=path):
                total, response = self.count(path, token_headers)
                self.assertLessEqual(total, 20)
                self.assertLessEqual(response.content.count(b'col-md-6 catalog-item'), 24)
        total, response = self.count("/libraries/", token_headers)
        self.assertContains(response, "Page 1 of")
        total, response = self.count("/libraries/", token_headers, q="Library 00")
        self.assertContains(response, "Library 001")
        self.assertContains(response, "found")
        self.assertNotContains(response, "Library 299")
        total, response = self.count("/libraries/", token_headers, q="Library")
        self.assertContains(response, "q=Library&amp;page=2")
        total, response = self.count("/libraries/", token_headers, page=2)
        self.assertContains(response, "Page 2 of")

    def test_search_is_escaped_and_limited_to_accessible_libraries(self):
        marker = '"><script>alert(1)</script>'
        _, response = self.count("/libraries/", self.headers, q=marker)
        self.assertNotContains(response, marker)
        _, response = self.count("/libraries/", token(groups=[]), q="Library")
        self.assertNotContains(response, "Library 001")

    def test_portal_never_lists_discovered_group_ids(self):
        discovered = self.groups[0]
        for path in ("/libraries/", "/libraries/new/"):
            _, response = self.count(path, administrator())
            self.assertNotContains(response, str(discovered.pk))

    def test_admin_lists_stay_bounded_and_default_to_configured_libraries(self):
        headers = administrator()
        for path, limit in (
            ("/admin/app_portal/entragroup/", 25),
            ("/admin/app_portal/profile/", 25),
            ("/admin/app_portal/course/", 25),
            ("/admin/app_portal/run/", 25),
            ("/admin/app_portal/librarygrant/", 25),
            ("/admin/admin/logentry/", 25),
            (f"/admin/app_portal/course/{self.course.pk}/change/", 30),
            (f"/admin/app_portal/profile/{self.profile.pk}/change/", 30),
            (f"/admin/app_portal/entragroup/{self.groups[1].pk}/change/", 30),
        ):
            with self.subTest(path=path):
                total, response = self.count(path, headers)
                self.assertLessEqual(total, limit)
                self.assertLess(len(response.content), 400_000)
        _, response = self.count("/admin/app_portal/entragroup/", headers)
        self.assertNotContains(response, "Library 000")
        self.assertContains(response, "Library 001")
        _, response = self.count(f"/admin/app_portal/course/{self.course.pk}/change/", headers)
        self.assertNotContains(response, "Library 299")

    def test_every_admin_filter_narrows_the_catalogue(self):
        unnamed = EntraGroup.objects.create(object_id=uuid.uuid4())
        draft_only = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Draft only")
        Course.objects.create(group=draft_only, created_by=self.user, title="Draft")
        shared = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Shared only")
        self.course.shared_groups.add(shared)
        headers = administrator()
        path = "/admin/app_portal/entragroup/"
        for params, present, absent in (
            ({"status": "discovered"}, "Library 000", "Library 001"),
            ({"status": "review"}, str(unnamed.pk), "Library 000"),
            ({"status": "all", "named": "missing"}, str(unnamed.pk), "Library 000"),
            ({"status": "all", "named": "custom"}, "Library 000", str(unnamed.pk)),
            ({"status": "all", "content": "drafts"}, "Draft only", "Library 000"),
            ({"status": "all", "content": "shared"}, "Shared only", "Library 000"),
            ({"status": "all", "content": "owned"}, "Library 000", "Shared only"),
            ({"status": "all", "content": "none"}, str(unnamed.pk), "Library 000"),
            ({"q": str(unnamed.pk)}, str(unnamed.pk), "Library 000"),
            ({"q": "Library 000"}, "Library 000", "Library 001"),
        ):
            with self.subTest(params=params):
                _, response = self.count(path, headers, **params)
                self.assertContains(response, present)
                self.assertNotContains(response, absent)
