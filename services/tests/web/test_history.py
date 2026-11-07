from __future__ import annotations

from app_portal.models import Course, Run

from .helpers import LibraryCase, token, viewer

PATH = "/runs/"


class HistoryUpdateTests(LibraryCase):
    def rows(self, headers=None):
        response = self.client.get(
            PATH, HTTP_ACCEPT="application/json", **(headers or self.headers),
        )
        self.assertEqual(response.status_code, 200)
        return {row["id"]: row for row in response.json()["rows"]}

    def row(self):
        return self.rows()[str(self.course.id)]

    def set_state(self, run, course):
        Run.objects.filter(pk=self.run.pk).update(status=run)
        Course.objects.filter(pk=self.course.pk).update(status=course)

    def test_each_state_gives_the_status_and_button_to_show(self):
        run_url = f"/runs/{self.run.id}/"
        manage_url = f"/courses/{self.course.id}/manage/"
        for run, course, label, action, url in (
            ("queued", "draft", "Queued", "View progress", run_url),
            ("running", "draft", "In progress", "View progress", run_url),
            ("ready", "ready", "Ready", "View result", run_url),
            ("needs_review", "needs_review", "Needs review", "Review transcripts", manage_url),
            ("failed", "draft", "Failed", "View details", run_url),
            ("stopped", "draft", "User canceled", "View details", run_url),
        ):
            with self.subTest(run=run):
                self.set_state(run, course)
                row = self.row()
                self.assertEqual((row["label"], row["action"], row["url"]), (label, action, url))

    def test_updates_cover_only_your_own_accessible_podcasts(self):
        self.assertIn(str(self.course.id), self.rows())
        self.assertEqual(self.rows(token(groups=[])), {})
        response = self.client.get(PATH, HTTP_ACCEPT="application/json", **viewer())
        self.assertEqual(response.status_code, 404)
