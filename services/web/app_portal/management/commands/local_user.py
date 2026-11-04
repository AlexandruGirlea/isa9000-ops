from __future__ import annotations

import uuid

from core.dev import SALES
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import transaction

from app_portal.models import EntraGroup, Profile


class Command(BaseCommand):
    help = "Create a local employee and print their session cookie."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Demo Employee")
        parser.add_argument("--email", default="employee@example.invalid")
        parser.add_argument("--group", default=SALES, type=uuid.UUID)

    def handle(self, *args, **options):
        if settings.AUTH_MODE != "dev" or not settings.DEBUG:
            raise CommandError("Local employees require ISA9000_AUTH=dev and DEBUG=true.")
        try:
            validate_email(options["email"])
            group = EntraGroup.objects.get(pk=options["group"])
        except (ValidationError, EntraGroup.DoesNotExist) as error:
            raise CommandError("Use a valid email and an existing library group ID.") from error
        with transaction.atomic():
            user, _ = get_user_model().objects.get_or_create(
                email=options["email"],
                defaults={"username": str(uuid.uuid4()), "first_name": options["name"][:150]},
            )
            profile, _ = Profile.objects.get_or_create(user=user)
            profile.groups.add(group)
            session = SessionStore()
            session["dev_user"] = user.username
            session["dev_authenticated"] = True
            session.create()
        self.stdout.write(f"{settings.SESSION_COOKIE_NAME}={session.session_key}")
