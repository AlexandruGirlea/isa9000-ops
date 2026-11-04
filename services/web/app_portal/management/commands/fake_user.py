from __future__ import annotations

import os
import uuid

from core.audit import record
from core.dev import SALES
from core.local_users import enabled
from core.sessions import start
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app_portal.models import EntraGroup, LocalTestUser, Profile


class Command(BaseCommand):
    help = "Create a limited local viewer, or refresh an existing numbered fake user's session."

    def add_arguments(self, parser):
        parser.add_argument("--number", default=None, type=int)
        parser.add_argument("--group", default=SALES, type=uuid.UUID)

    def handle(self, *args, **options):
        if not enabled():
            raise CommandError("Fake users are available only in the local Entra test stack.")
        number = options["number"]
        if number is None and os.environ.get("ISA9000_FAKE_USER_NUMBER"):
            try:
                number = int(os.environ["ISA9000_FAKE_USER_NUMBER"])
            except ValueError as error:
                raise CommandError("Use a positive user number.") from error
        if number is not None and number <= 0:
            raise CommandError("Use a positive user number.")
        with transaction.atomic():
            if number is not None:
                entry = LocalTestUser.objects.select_for_update(of=("self",)).select_related(
                    "user__profile"
                ).filter(number=number).first()
                if entry is None:
                    self.stdout.write("No user found")
                    return
            else:
                group = EntraGroup.objects.filter(pk=options["group"]).first()
                if group is None or str(group.pk) == settings.ADMIN_GROUP_ID:
                    raise CommandError("Choose an existing library that is not the admin group.")
                user = get_user_model().objects.create_user(username=str(uuid.uuid4()))
                profile = Profile.objects.create(user=user, role="viewer")
                profile.groups.add(group)
                entry = LocalTestUser.objects.create(user=user)
                user.first_name = f"Fake user {entry.number}"
                user.email = f"fake-user-{entry.number}@example.invalid"
                user.save(update_fields=["first_name", "email"])
            user = entry.user
            groups = list(user.profile.groups.all())
            if (
                not user.is_active or user.is_staff or user.is_superuser
                or any(str(group.pk) == settings.ADMIN_GROUP_ID for group in groups)
            ):
                raise CommandError("Fake sessions cannot use disabled or administrator accounts.")
            session = SessionStore()
            session["local_test_user"] = entry.number
            start(session, user.username)
            session.create()
            entry.session_key = session.session_key
            entry.save(update_fields=["session_key"])
            record(
                user, "auth.local_test_session_issued", entry, actor_id="local_cli",
                changed_fields=["session"], state="refreshed" if number is not None else "created",
            )
        self.stdout.write(f"Fake user #{entry.number} · {user.profile.get_role_display()}")
        for group in groups:
            self.stdout.write(f"Library: {group} ({group.pk})")
        self.stdout.write(f"Cookie name: {settings.SESSION_COOKIE_NAME}")
        self.stdout.write(f"Cookie value: {session.session_key}")
        self.stdout.write("Domain: localhost | Path: / | Session: 2 hours idle, 8 hours maximum")
