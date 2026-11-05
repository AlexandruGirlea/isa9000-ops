from __future__ import annotations

from app_portal.models import ContactRequest
from django.contrib.admin.models import LogEntry

from .helpers import ADMIN_OID, OVERAGE, LibraryCase, administrator, token, viewer

PATH = "/contact/"
PREVIEW = "/contact/description-preview/"


class ContactFormTests(LibraryCase):
    def test_only_creators_get_the_button_and_the_page(self):
        self.assertContains(self.client.get("/libraries/", **self.headers), 'href="/contact/"')
        self.assertContains(self.client.get("/libraries/", **administrator()), 'href="/contact/"')
        self.assertNotContains(self.client.get("/libraries/", **viewer()), 'href="/contact/"')
        self.assertEqual(self.client.get(PATH, **viewer()).status_code, 404)
        self.assertEqual(self.client.post(PATH, {"description": "x"}, **viewer()).status_code, 404)
        response = self.client.post(PREVIEW, {"description": "x"}, **viewer())
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ContactRequest.objects.exists())

    def test_creator_without_any_library_can_ask_for_one(self):
        overage = token(**OVERAGE)
        self.assertContains(self.client.get("/libraries/new/", **overage), 'href="/contact/"')
        response = self.client.post(PATH, {"description": "Library for Finance"}, **overage)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ContactRequest.objects.get().created_by, self.user)

    def test_page_explains_itself_and_uses_the_editor(self):
        page = self.client.get(PATH, **self.headers)
        self.assertContains(page, "which department or team")
        self.assertContains(page, 'data-description-preview="/contact/description-preview/"')
        self.assertContains(page, "/static/vendor/vditor/dist/index.min.js")
        self.assertNotContains(page, "What will listeners learn")
        self.assertNotContains(page, "portal-toast")

    def test_sending_saves_who_when_and_what_then_shows_the_popup(self):
        response = self.client.post(
            PATH,
            {
                "description": "  Library for **Finance**  ", "status": "done",
                "created_by": "other", "updated_by": "other",
            },
            **self.headers,
        )
        self.assertEqual(response.url, "/libraries/")
        item = ContactRequest.objects.get()
        self.assertEqual(item.body, "Library for **Finance**")
        self.assertEqual(item.created_by, self.user)
        self.assertIsNotNone(item.created)
        self.assertEqual(item.status, "todo")
        self.assertIsNone(item.updated_by)
        self.assertIsNone(item.updated)
        self.assertTrue(
            LogEntry.objects.filter(change_message__contains="contact.created").exists()
        )
        page = self.client.get(response.url, **self.headers)
        self.assertContains(page, 'class="portal-toast"')
        self.assertContains(page, "Your request was sent to your administrators.")
        self.assertNotContains(self.client.get(response.url, **self.headers), "portal-toast")

    def test_empty_and_oversized_requests_are_refused(self):
        for body in ("", "   ", "x" * 10001):
            with self.subTest(size=len(body)):
                page = self.client.post(PATH, {"description": body}, **self.headers)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, "errorlist")
        self.assertFalse(ContactRequest.objects.exists())

    def test_preview_renders_for_creators(self):
        response = self.client.post(PREVIEW, {"description": "**Finance**"}, **self.headers)
        self.assertIn("<strong>Finance</strong>", response.json()["html"])


class ContactAdminTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.item = ContactRequest.objects.create(
            body="Library for <script>alert(1)</script> **Finance**", created_by=self.user,
        )
        self.path = f"/admin/app_portal/contactrequest/{self.item.pk}/change/"

    def test_admin_identifies_people_by_name_and_email(self):
        self.client.get("/libraries/", **self.headers)
        page = self.client.get(self.path, **administrator())
        self.assertContains(page, "A Person (a@example.invalid)")
        listing = self.client.get("/admin/app_portal/contactrequest/", **administrator())
        self.assertContains(listing, "A Person (a@example.invalid)")
        found = self.client.get(
            "/admin/app_portal/contactrequest/", {"q": "a@example.invalid"}, **administrator(),
        )
        self.assertContains(found, f"Request {self.item.pk}")

    def test_message_is_formatted_and_cannot_inject_script(self):
        page = self.client.get(self.path, **administrator())
        self.assertContains(page, "<strong>Finance</strong>")
        self.assertNotContains(page, "<script>alert(1)</script>")

    def test_status_change_records_who_and_when_and_nothing_else_is_editable(self):
        response = self.client.post(
            self.path,
            {
                "status": "in_progress", "body": "Forged", "created_by": "1",
                "created": "2000-01-01", "updated_by": "1", "updated": "2000-01-01",
                "_save": "Save",
            },
            **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "in_progress")
        self.assertEqual(self.item.updated_by.username, ADMIN_OID)
        self.assertIsNotNone(self.item.updated)
        self.assertEqual(self.item.created_by, self.user)
        self.assertIn("Finance", self.item.body)
        self.assertNotEqual(self.item.created.year, 2000)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.item.pk), change_message__contains="admin.change",
        ).exists())

    def test_saving_without_a_status_change_keeps_the_updated_fields(self):
        self.client.post(self.path, {"status": "todo", "_save": "Save"}, **administrator())
        self.item.refresh_from_db()
        self.assertIsNone(self.item.updated_by)
        self.assertIsNone(self.item.updated)

    def test_admins_cannot_add_or_delete_and_others_cannot_reach_it(self):
        add = "/admin/app_portal/contactrequest/add/"
        delete = f"/admin/app_portal/contactrequest/{self.item.pk}/delete/"
        self.assertEqual(self.client.get(add, **administrator()).status_code, 403)
        response = self.client.post(delete, {"post": "yes"}, **administrator())
        self.assertEqual(response.status_code, 403)
        for headers in (self.headers, viewer()):
            self.assertEqual(self.client.get(self.path, **headers).status_code, 404)
            self.assertEqual(
                self.client.post(self.path, {"status": "done"}, **headers).status_code, 404,
            )
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "todo")
