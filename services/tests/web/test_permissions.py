from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from app_portal.models import Course, EntraGroup, Profile, Run
from azure.core.exceptions import AzureError, ResourceNotFoundError
from core.dev import ENGINEERING, SALES
from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, User
from django.test import Client, RequestFactory
from django.utils import timezone

from .helpers import ADMIN, PortalCase, principal


class PermissionTests(PortalCase):
    def test_portal_permission_table(self):
        other = Course.objects.create(
            group=self.engineering,
            created_by=self.user,
            status="ready",
            title="Other course",
            html="Other course",
        )
        draft = Course.objects.create(group=self.sales, created_by=self.user, title="Hidden draft")
        for role in ("viewer", "creator", "admin"):
            self.profile.role = "creator" if role == "creator" else "viewer"
            self.profile.save()
            header = {
                "HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(
                    groups=[SALES, ADMIN] if role == "admin" else [SALES]
                )
            }
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
            listing = self.client.get(f"/libraries/{SALES}/", **header).content.decode()
            self.assertEqual("Hidden draft" in listing, allowed)
            self.assertEqual("New podcast" in listing, allowed)
        self.course.shared_groups.add(self.engineering)
        draft.shared_groups.add(self.engineering)
        for role in ("viewer", "creator"):
            self.profile.role = role
            self.profile.save()
            header = {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(groups=[ENGINEERING])}
            with self.subTest(shared_role=role):
                listing = self.client.get(f"/libraries/{ENGINEERING}/", **header)
                self.assertContains(listing, self.course.title)
                self.assertContains(
                    listing,
                    f'class="catalog-card-link" href="/courses/{self.course.id}/'
                    f'?library={ENGINEERING}"',
                    count=1,
                )
                self.assertNotContains(listing, f"/courses/{self.course.id}/manage/")
                self.assertNotContains(listing, draft.title)
                self.assertEqual(
                    self.client.get(f"/courses/{self.course.id}/", **header).status_code, 200,
                )
                for path in (
                    f"/courses/{draft.id}/", f"/courses/{draft.id}/manage/",
                    f"/courses/{self.course.id}/manage/", f"/runs/{self.run.id}/",
                    f"/courses/{self.course.id}/episodes/1/transcript/", f"/libraries/{SALES}/",
                ):
                    self.assertEqual(self.client.get(path, **header).status_code, 404)
        self.course.shared_groups.add(self.sales)
        listing = self.client.get(f"/libraries/{SALES}/", **self.headers)
        self.assertContains(
            listing,
            f'class="catalog-card-link" href="/courses/{self.course.id}/?library={SALES}"', count=1,
        )
        self.course.shared_groups.remove(self.engineering)
        self.assertEqual(
            self.client.get(f"/courses/{self.course.id}/", **header).status_code, 404,
        )
        self.assertNotContains(
            self.client.get(f"/libraries/{ENGINEERING}/", **header), self.course.title,
        )

    def test_every_mutation_requires_creator_in_course_group(self):
        self.course.shared_groups.add(self.engineering)
        self.profile.role = "viewer"
        self.profile.save()
        for headers in (
            self.headers,
            {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(groups=[ENGINEERING])},
        ):
            for action in ("rename", "record", "delete_episode", "delete_course"):
                with self.subTest(action=action, headers=headers):
                    self.assertEqual(
                        self.client.post(
                            f"/courses/{self.course.id}/manage/",
                            {"action": action, "episode": 1},
                            **headers,
                        ).status_code,
                        404,
                    )
            for action in ("stop", "retry"):
                self.assertEqual(
                    self.client.post(
                        f"/runs/{self.run.id}/", {"action": action}, **headers
                    ).status_code,
                    404,
                )
            self.assertEqual(
                self.client.post(
                    f"/libraries/{SALES}/new/", {"request": "Test", "title": "Test"}, **headers
                ).status_code,
                404,
            )
            with patch("app_portal.transcripts.storage.save_upload") as upload:
                self.assertEqual(
                    self.client.post(
                        f"/courses/{self.course.id}/episodes/1/transcript/",
                        json.dumps({"text": "Unauthorized edit", "revision": "stale"}),
                        content_type="application/json", **headers,
                    ).status_code,
                    404,
                )
                upload.assert_not_called()
            self.profile.role = "creator"
            self.profile.save()
        with patch("app_portal.transcripts.storage.save_upload") as upload:
            other = Course.objects.create(
                group=self.engineering, created_by=self.user,
                status="needs_review", manifest=self.manifest,
            )
            response = self.client.post(
                f"/courses/{other.id}/episodes/1/transcript/",
                json.dumps({"text": "Forged course id", "revision": other.updated.isoformat()}),
                content_type="application/json", **self.headers,
            )
            self.assertEqual(response.status_code, 404)
            response = Client(enforce_csrf_checks=True).post(
                f"/courses/{self.course.id}/episodes/1/transcript/",
                json.dumps({"text": "CSRF edit", "revision": self.course.updated.isoformat()}),
                content_type="application/json", **self.headers,
            )
            self.assertEqual(response.status_code, 403)
            upload.assert_not_called()

    def test_media_checks_group_and_visibility_then_streams(self):
        with (
            patch("app_portal.media.storage.blob_properties", return_value=SimpleNamespace(
                size=5, etag='"version"',
                content_settings=SimpleNamespace(content_type="audio/mpeg"),
            )),
            patch(
                "app_portal.media.storage.open_blob", side_effect=lambda *a, **kw: iter([b"audio"]),
            ) as stream,
        ):
            response = self.client.get(
                f"/media/{self.course.id}/01%20-%20First.mp3", **self.headers
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(b"".join(response.streaming_content), b"audio")
            stream.assert_called_once_with(
                f"courses/{self.course.id}/01 - First.mp3", offset=0, length=5, etag='"version"',
            )
            self.course.shared_groups.add(self.engineering)
            for role in ("viewer", "creator"):
                self.profile.role = role
                self.profile.save()
                for filename in ("01 - First.mp3", "01 - First.txt", "cover.png"):
                    with self.subTest(shared_role=role, filename=filename):
                        response = self.client.get(
                            f"/media/{self.course.id}/{filename}",
                            HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]),
                        )
                        self.assertEqual(response.status_code, 200)
                        stream.assert_called_with(
                            f"courses/{self.course.id}/{filename}",
                            offset=0, length=5, etag='"version"',
                        )
            self.course.shared_groups.remove(self.engineering)
            stream.reset_mock()
            self.assertEqual(self.client.get(
                f"/media/{self.course.id}/cover.png",
                HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]),
            ).status_code, 404)
            stream.assert_not_called()
            self.course.shared_groups.add(self.engineering)
            for filename in ("unknown", "../secret", "podcast.json"):
                self.assertEqual(
                    self.client.get(
                        f"/media/{self.course.id}/{filename}", **self.headers
                    ).status_code,
                    404,
                )
            self.profile.role = "viewer"
            self.profile.save()
            self.course.status = "needs_review"
            self.course.save()
            self.assertEqual(
                self.client.get(
                    f"/media/{self.course.id}/01%20-%20First.txt", **self.headers
                ).status_code,
                404,
            )
        self.assertEqual(
            self.client.get(
                f"/media/{self.course.id}/cover.png",
                HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]),
            ).status_code,
            404,
        )
        path = f"/courses/{self.course.id}/episodes/1/transcript/"
        content = "Résumé <script>plain text</script>"
        with patch("app_portal.transcripts.storage.read_text", return_value=content) as read:
            self.assertEqual(self.client.get(path, **self.headers).status_code, 404)
            read.assert_not_called()
            self.profile.role = "creator"
            self.profile.save()
            self.assertEqual(
                self.client.get(
                    path, HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]),
                ).status_code,
                404,
            )
            read.assert_not_called()
            response = self.client.get(path, **self.headers)
            self.assertEqual(response.json()["title"], "First")
            self.assertEqual(response.json()["text"], content)
            self.assertEqual(response.json()["revision"], self.course.updated.isoformat())
            self.assertFalse(response.json()["editable"])
            self.assertTrue(response.json()["edit_reason"])
            self.assertFalse(response.json()["published"])
            self.assertEqual(response["Cache-Control"], "private, no-store")
            read.assert_called_once_with(f"courses/{self.course.id}/01 - First.txt")
            read.reset_mock()
            self.assertEqual(
                self.client.get(path.replace("/1/", "/999/"), **self.headers).status_code, 404,
            )
            self.assertEqual(self.client.delete(path, **self.headers).status_code, 405)
            read.assert_not_called()
            for filename in (None, "", ".", "..", "../secret.txt", "a/b.txt", "a\\b.txt", "a\nb"):
                with self.subTest(filename=filename):
                    self.course.manifest = {
                        **self.manifest,
                        "episodes": [{**self.manifest["episodes"][0], "transcript": filename}],
                    }
                    self.course.save(update_fields=["manifest"])
                    self.assertEqual(self.client.get(path, **self.headers).status_code, 404)
                    read.assert_not_called()
            self.course.manifest = self.manifest
            self.course.save(update_fields=["manifest"])
            self.profile.role = "viewer"
            self.profile.save()
            response = self.client.get(
                path, HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ADMIN]),
            )
            self.assertEqual(response.json()["text"], content)
            self.profile.role = "creator"
            self.profile.save()
            for error, code in (
                (ResourceNotFoundError("missing"), 404),
                (ValueError("size limit"), 422),
                (UnicodeDecodeError("utf8", b"\xff", 0, 1, "invalid"), 422),
                (AzureError("private storage details"), 503),
            ):
                with self.subTest(error=type(error).__name__):
                    read.side_effect = error
                    response = self.client.get(path, **self.headers)
                    self.assertEqual(response.status_code, code)
                    self.assertNotIn(b"private storage details", response.content)
                    self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_nonempty_library_list_and_course_html_is_unchanged(self):
        response = self.client.get("/libraries/", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'/libraries/{SALES}/')
        self.assertNotContains(response, f'/libraries/{ENGINEERING}/')
        self.assertEqual(
            self.client.get(f"/libraries/{SALES}/index.html", **self.headers).url,
            f"/libraries/{SALES}/",
        )
        self.assertEqual(
            self.client.get(f"/courses/{self.course.id}/", **self.headers).content,
            self.course.html.encode(),
        )
        draft = Course.objects.create(group=self.sales, created_by=self.user, title="New podcast")
        run = Run.objects.create(course=draft, requested_by=self.user, task="create")
        path = f"/courses/{draft.id}/"
        for state in ("queued", "running", "failed", "stopped"):
            with self.subTest(state=state):
                run.status = state
                run.save()
                response = self.client.get(path, **self.headers)
                self.assertEqual(response.url, f"/runs/{run.id}/")
                self.assertEqual(response["Cache-Control"], "private, no-store")
        listing = self.client.get(f"/libraries/{SALES}/", **self.headers)
        self.assertContains(listing, f'class="catalog-card-link" href="{path}?library={SALES}"')
        history = self.client.get("/runs/", **self.headers)
        self.assertContains(history, f'href="{path}">New podcast</a>')
        draft.status = "needs_review"
        draft.save()
        self.assertEqual(self.client.get(path, **self.headers).url, f"{path}manage/")
        self.profile.role = "viewer"
        self.profile.save()
        self.assertEqual(self.client.get(path, **self.headers).status_code, 404)
        draft.status, draft.html = "ready", "<!doctype html>Ready podcast"
        draft.save()
        self.assertEqual(self.client.get(path, **self.headers).content, draft.html.encode())
        self.assertEqual(
            self.client.get(
                path, HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING])
            ).status_code,
            404,
        )
        self.course.shared_groups.add(self.engineering)
        self.engineering.name = "Engineering & <partners>"
        self.engineering.save(update_fields=["name"])
        self.course.html = (
            '<nav class="course-breadcrumbs" aria-label="Breadcrumb"><div class="container">'
            '<a href="/libraries/">Libraries</a><span>&rsaquo;</span>'
            f'<a href="/libraries/{SALES}/">Private owner library</a>'
            '<span>&rsaquo;</span><span>Podcast</span></div></nav>'
        )
        self.course.save(update_fields=["html"])
        headers = {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(groups=[ENGINEERING])}
        listing = self.client.get("/libraries/", **headers)
        self.assertContains(listing, f'/libraries/{ENGINEERING}/')
        self.assertNotContains(listing, f'/libraries/{SALES}/')
        response = self.client.get(f"/courses/{self.course.id}/", **headers)
        self.assertContains(response, f'/libraries/{ENGINEERING}/')
        self.assertContains(response, "Engineering &amp; &lt;partners&gt;")
        self.assertNotContains(response, "Private owner library")
        self.assertNotContains(response, f"/libraries/{SALES}/")
        for requested in (SALES, "not-a-group"):
            response = self.client.get(
                f"/courses/{self.course.id}/?library={requested}", **headers,
            )
            self.assertContains(response, f'/libraries/{ENGINEERING}/')
            self.assertNotContains(response, "Private owner library")

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

    def test_admin_only_library_name_and_person_role_are_editable(self):
        self.user.is_superuser = self.user.is_staff = True
        request = RequestFactory().get("/admin/")
        request.user = self.user
        for model, obj, fields in (
            (EntraGroup, self.sales, {"name", "status", "show_when_empty"}),
            (Profile, self.profile, {"role", "local_admin", "is_active"}),
            (Course, self.course, {"shared_groups"}),
        ):
            with self.subTest(model=model.__name__):
                form = admin.site._registry[model].get_form(request, obj)
                self.assertEqual(set(form.base_fields), fields)
        groups = form(instance=self.course).fields["shared_groups"].queryset
        self.assertIn(self.engineering, groups)
        self.assertNotIn(self.sales, groups)
        model_admin = admin.site._registry[Course]
        path = f"/admin/app_portal/course/{self.course.pk}/change/"
        for role in ("viewer", "creator"):
            self.profile.role = role
            self.profile.save(update_fields=["role"])
            self.assertEqual(self.client.post(
                path, {"shared_groups": [ENGINEERING], "_save": "Save"}, **self.headers,
            ).status_code, 404)
            self.assertFalse(self.course.shared_groups.exists())
        headers = {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(groups=[ADMIN])}
        self.assertEqual(self.client.post(
            path, {"shared_groups": [SALES], "_save": "Save"}, **headers,
        ).status_code, 200)
        self.assertFalse(self.course.shared_groups.exists())
        worker_updated = timezone.now()
        worker_manifest = {**self.manifest, "description": "Worker completed"}
        marketing = EntraGroup.objects.create(
            object_id="44444444-4444-4444-8444-444444444444", name="Marketing", status="enabled",
        )
        marketing.refresh_from_db()
        original_save = model_admin.save_model

        def worker_finishes(request, obj, form, change):
            Course.objects.filter(pk=obj.pk).update(
                title="Worker title", manifest=worker_manifest, html="Worker HTML",
                status="needs_review", updated=worker_updated,
            )
            return original_save(request, obj, form, change)

        with (
            patch.object(model_admin, "save_model", side_effect=worker_finishes),
            patch("app_portal.runs.dispatch") as dispatch,
            patch("app_portal.admin.storage.delete_prefix") as delete,
        ):
            response = self.client.post(
                path, {
                    "shared_groups": [ENGINEERING, str(marketing.pk)], "_save": "Save",
                    "group": ENGINEERING, "title": "Forged title", "status": "ready",
                    "manifest": "{}", "html": "Forged HTML",
                }, **headers,
            )
            self.assertEqual(response.status_code, 302)
            self.course.refresh_from_db()
            self.assertEqual(list(self.course.shared_groups.all()), [self.engineering, marketing])
            self.assertEqual(self.course.group_id, self.sales.pk)
            self.assertEqual(self.course.title, "Worker title")
            self.assertEqual(self.course.manifest, worker_manifest)
            self.assertEqual(self.course.html, "Worker HTML")
            self.assertEqual(self.course.status, "needs_review")
            self.assertGreaterEqual(self.course.updated, worker_updated)
            Run.objects.filter(pk=self.run.pk).update(status="needs_review")
            review = self.client.get(f"/courses/{self.course.pk}/manage/", **headers)
            self.assertContains(review, "<strong>Engineering</strong>")
            self.assertContains(review, "<strong>Marketing</strong>")
            response = self.client.post(path, {"_save": "Save"}, **headers)
            self.assertEqual(response.status_code, 302)
            self.assertFalse(self.course.shared_groups.exists())
            self.course.refresh_from_db()
            self.assertGreaterEqual(self.course.updated, worker_updated)
            for action in ("course.shared", "course.unshared"):
                entries = LogEntry.objects.filter(change_message__contains=f'"action": "{action}"')
                self.assertEqual(entries.count(), 2)
                for group in (self.engineering, marketing):
                    self.assertTrue(entries.filter(
                        change_message__contains=f'"group_id": "{group.pk}"',
                    ).exists())
            dispatch.assert_not_called()
            delete.assert_not_called()

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
