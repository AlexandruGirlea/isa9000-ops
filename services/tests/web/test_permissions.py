from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app_portal.models import Course, EntraGroup, Profile, Run
from azure.core.exceptions import AzureError
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, User
from django.test import Client, RequestFactory

from .helpers import ADMIN, ENGINEERING, SALES, PortalCase, token


class PermissionTests(PortalCase):
    def test_portal_permission_table(self):
        other = Course.objects.create(
            group=self.engineering, created_by=self.user, status="ready", title="Other course",
            html="Other course",
        )
        draft = Course.objects.create(group=self.sales, created_by=self.user, title="Hidden draft")
        for role in ("viewer", "creator", "admin"):
            self.profile.role = "creator" if role == "creator" else "viewer"
            self.profile.save()
            header = token(groups=[SALES, ADMIN] if role == "admin" else [SALES])
            allowed = role != "viewer"
            cases = [
                ("/runs/", 200 if allowed else 404),
                ("/libraries/new/", 200 if allowed else 404),
                (f"/libraries/{SALES}/", 200),
                (f"/libraries/{ENGINEERING}/", 200 if role == "admin" else 404),
                (f"/courses/{self.course.id}/", 200),
                (f"/courses/{other.id}/", 200 if role == "admin" else 404),
                (f"/courses/{draft.id}/", 302 if allowed else 404),
                (f"/courses/{draft.id}/manage/", 200 if allowed else 404),
                (f"/libraries/{SALES}/new/", 200 if allowed else 404),
                (f"/libraries/{ENGINEERING}/new/", 200 if role == "admin" else 404),
                (f"/courses/{self.course.id}/manage/", 200 if allowed else 404),
                (f"/courses/{other.id}/manage/", 200 if role == "admin" else 404),
                (f"/runs/{self.run.id}/", 200 if allowed else 404),
                ("/admin/", 200 if role == "admin" else 404),
            ]
            for path, status in cases:
                with self.subTest(role=role, path=path):
                    response = self.client.get(path, **header)
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_sharing_gives_read_access_only_and_ends_when_removed(self):
        draft = Course.objects.create(group=self.sales, created_by=self.user, title="Hidden draft")
        self.course.shared_groups.add(self.engineering)
        draft.shared_groups.add(self.engineering)
        header = token(groups=[ENGINEERING])
        course = f"/courses/{self.course.id}/"
        for role in ("viewer", "creator"):
            self.profile.role = role
            self.profile.save()
            with self.subTest(role=role):
                listing = self.client.get(f"/libraries/{ENGINEERING}/", **header)
                self.assertContains(listing, self.course.title)
                self.assertNotContains(listing, draft.title)
                self.assertNotContains(listing, f"{course}manage/")
                self.assertEqual(self.client.get(course, **header).status_code, 200)
                for path in (
                    f"/courses/{draft.id}/", f"{course}manage/", f"/runs/{self.run.id}/",
                    f"{course}episodes/1/transcript/", f"/libraries/{SALES}/",
                ):
                    self.assertEqual(self.client.get(path, **header).status_code, 404)
        self.course.shared_groups.remove(self.engineering)
        self.assertEqual(self.client.get(course, **header).status_code, 404)

    def test_every_mutation_requires_creator_in_course_group(self):
        self.course.shared_groups.add(self.engineering)
        self.profile.role = "viewer"
        self.profile.save()
        mutations = [
            *[
                (f"/courses/{self.course.id}/manage/", {"action": action, "episode": 1})
                for action in ("rename", "record", "delete_episode", "delete_course")
            ],
            *[(f"/runs/{self.run.id}/", {"action": action}) for action in ("stop", "retry")],
            (f"/libraries/{SALES}/new/", {"request": "Test", "title": "Test"}),
            (f"/libraries/{SALES}/check/", {"source": "https://example.com/"}),
            (f"/libraries/{SALES}/description-preview/", {"description": "Text"}),
            (f"/courses/{self.course.id}/description-preview/", {"description": "Text"}),
        ]
        # A viewer of the owning library, then a creator who only receives it as shared.
        for headers in (self.headers, token(groups=[ENGINEERING])):
            for path, data in mutations:
                with self.subTest(path=path, data=data):
                    self.assertEqual(self.client.post(path, data, **headers).status_code, 404)
            self.profile.role = "creator"
            self.profile.save()

    def test_transcript_edit_needs_access_and_csrf(self):
        other = Course.objects.create(
            group=self.engineering, created_by=self.user, status="needs_review",
            manifest=self.manifest,
        )
        path = f"/courses/{self.course.id}/episodes/1/transcript/"
        body = json.dumps({"text": "Edit", "revision": self.course.updated.isoformat()})
        with patch("app_portal.transcripts.storage.save_upload") as upload:
            for client, url, headers, code in (
                (self.client, path, token(groups=[ENGINEERING]), 404),
                (self.client, f"/courses/{other.id}/episodes/1/transcript/", self.headers, 404),
                (Client(enforce_csrf_checks=True), path, self.headers, 403),
            ):
                response = client.post(url, body, content_type="application/json", **headers)
                self.assertEqual(response.status_code, code)
            upload.assert_not_called()

    def test_media_checks_access_then_streams(self):
        other_library = token(groups=[ENGINEERING])
        media = f"/media/{self.course.id}"

        def status(name, headers):
            return self.client.get(f"{media}/{name}", **headers).status_code

        with (
            patch("app_portal.media.storage.blob_properties", return_value=SimpleNamespace(
                size=5, etag='"version"',
                content_settings=SimpleNamespace(content_type="audio/mpeg"),
            )),
            patch(
                "app_portal.media.storage.open_blob", side_effect=lambda *a, **kw: iter([b"audio"]),
            ) as stream,
        ):
            response = self.client.get(f"{media}/01%20-%20First.mp3", **self.headers)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(b"".join(response.streaming_content), b"audio")
            stream.reset_mock()
            self.assertEqual(status("cover.png", other_library), 404)
            for filename in ("unknown", "../secret", "podcast.json"):
                self.assertEqual(status(filename, self.headers), 404)
            stream.assert_not_called()
            self.course.shared_groups.add(self.engineering)
            self.assertEqual(status("cover.png", other_library), 200)
            self.profile.role = "viewer"
            self.profile.save()
            self.course.status = "needs_review"
            self.course.save()
            self.assertEqual(status("01%20-%20First.txt", self.headers), 404)

    def test_transcript_needs_edit_rights_and_hides_storage_errors(self):
        path = f"/courses/{self.course.id}/episodes/1/transcript/"
        with patch("app_portal.transcripts.storage.read_text", return_value="Text") as read:
            self.assertEqual(self.client.get(path, **token(groups=[ENGINEERING])).status_code, 404)
            self.profile.role = "viewer"
            self.profile.save()
            self.assertEqual(self.client.get(path, **self.headers).status_code, 404)
            self.profile.role = "creator"
            self.profile.save()
            read.assert_not_called()
            self.assertEqual(self.client.get(path, **self.headers).json()["text"], "Text")
            read.assert_called_once_with(f"courses/{self.course.id}/01 - First.txt")
            read.reset_mock()
            for filename in ("..", "../secret.txt", "a/b.txt", "a\\b.txt"):
                self.course.manifest = {
                    **self.manifest,
                    "episodes": [{**self.manifest["episodes"][0], "transcript": filename}],
                }
                self.course.save(update_fields=["manifest"])
                self.assertEqual(self.client.get(path, **self.headers).status_code, 404)
            read.assert_not_called()
            self.course.manifest = self.manifest
            self.course.save(update_fields=["manifest"])
            read.side_effect = AzureError("private storage details")
            response = self.client.get(path, **self.headers)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(b"private storage details", response.content)

    def test_course_page_serves_stored_html_and_hides_a_private_owner(self):
        course = f"/courses/{self.course.id}/"
        self.assertEqual(self.client.get(course, **self.headers).content, self.course.html.encode())
        self.course.shared_groups.add(self.engineering)
        self.course.html = (
            '<nav class="course-breadcrumbs" aria-label="Breadcrumb"><div class="container">'
            '<a href="/libraries/">Libraries</a><span>&rsaquo;</span>'
            f'<a href="/libraries/{SALES}/">Private owner library</a>'
            '<span>&rsaquo;</span><span>Podcast</span></div></nav>'
        )
        self.course.save(update_fields=["html"])
        for query in ("", f"?library={SALES}"):
            response = self.client.get(f"{course}{query}", **token(groups=[ENGINEERING]))
            self.assertContains(response, f"/libraries/{ENGINEERING}/")
            self.assertNotContains(response, "Private owner library")
            self.assertNotContains(response, f"/libraries/{SALES}/")

    def test_unfinished_podcast_opens_its_run_or_review(self):
        draft = Course.objects.create(group=self.sales, created_by=self.user, title="New podcast")
        run = Run.objects.create(course=draft, requested_by=self.user, task="create")
        path = f"/courses/{draft.id}/"
        self.assertEqual(self.client.get(path, **self.headers).url, f"/runs/{run.id}/")
        run.status = "stopped"
        run.save()
        draft.status = "needs_review"
        draft.save()
        self.assertEqual(self.client.get(path, **self.headers).url, f"{path}manage/")

    def test_admin_permission_table(self):
        objects = {
            EntraGroup: self.sales,
            Profile: self.profile,
            Course: self.course,
            Run: self.run,
            LogEntry: None,
        }
        self.run.status = "ready"
        self.run.save()
        for superuser in (False, True):
            self.user.is_superuser = superuser
            request = RequestFactory().get("/admin/")
            request.user = self.user
            for model, obj in objects.items():
                model_admin = admin.site._registry[model]
                expected = (
                    superuser and model == EntraGroup,
                    superuser,
                    superuser and model in {EntraGroup, Profile, Course},
                    superuser and model == Course,
                )
                actual = (
                    model_admin.has_add_permission(request),
                    model_admin.has_view_permission(request, obj),
                    model_admin.has_change_permission(request, obj),
                    model_admin.has_delete_permission(request, obj),
                )
                with self.subTest(superuser=superuser, model=model.__name__):
                    self.assertEqual(actual, expected)
        self.assertNotIn(User, admin.site._registry)
        self.assertNotIn(Group, admin.site._registry)

    def test_admin_can_only_change_library_status_person_role_and_sharing(self):
        self.user.is_superuser = self.user.is_staff = True
        request = RequestFactory().get("/admin/")
        request.user = self.user
        for model, obj, fields in (
            (EntraGroup, self.sales, {"name", "status"}),
            (Profile, self.profile, {"role", "local_admin", "is_active"}),
            (Course, self.course, {"shared_groups"}),
        ):
            with self.subTest(model=model.__name__):
                form = admin.site._registry[model].get_form(request, obj)
                self.assertEqual(set(form.base_fields), fields)
        path = f"/admin/app_portal/course/{self.course.pk}/change/"
        share = {"shared_groups": [ENGINEERING], "_save": "Save"}
        self.assertEqual(self.client.post(path, share, **self.headers).status_code, 404)
        self.assertFalse(self.course.shared_groups.exists())
        forged = {**share, "group": ENGINEERING, "title": "Forged", "status": "draft"}
        self.assertEqual(self.client.post(path, forged, **token(groups=[ADMIN])).status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(list(self.course.shared_groups.all()), [self.engineering])
        self.assertEqual(self.course.group_id, self.sales.pk)
        self.assertEqual(self.course.title, "Sales course")
        self.assertEqual(self.course.status, "ready")

    def test_admin_bulk_roles_record_audit_activity(self):
        self.user.is_superuser = True
        request = RequestFactory().post("/admin/")
        request.user = self.user
        model_admin = admin.site._registry[Profile]
        queryset = Profile.objects.filter(pk=self.profile.pk)
        model_admin.make_viewer(request, queryset)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, "viewer")
        model_admin.make_creator(request, queryset)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.role, "creator")
        self.assertEqual(LogEntry.objects.count(), 2)

    def test_admin_delete_confirmation_lists_files_and_deletes_blobs(self):
        self.run.status = "ready"
        self.run.save()
        self.user.is_superuser = True
        request = RequestFactory().get("/admin/")
        request.user = self.user
        model_admin = admin.site._registry[Course]
        deleted, *_ = model_admin.get_deleted_objects([self.course], request)
        self.assertIn("01 - First.mp3", str(deleted))
        with patch("app_portal.admin.storage.delete_prefix") as delete:
            model_admin.delete_model(request, self.course)
        delete.assert_called_once()
        self.assertFalse(Course.objects.exists())
        self.assertTrue(get_user_model().objects.filter(pk=self.user.pk).exists())
