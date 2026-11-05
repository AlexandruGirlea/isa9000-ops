from __future__ import annotations

import uuid

from core.audit import record
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app_portal.models import Profile
from shared import telemetry


class Command(BaseCommand):
    help = "Recover or remove application-administrator access for one Entra identity."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=("grant", "revoke"))
        parser.add_argument("--tenant", required=True, type=uuid.UUID)
        parser.add_argument("--object-id", required=True, type=uuid.UUID)
        parser.add_argument(
            "--activate", action="store_true", help="Also reactivate a suspended account.",
        )

    def handle(self, *args, **options):
        if str(options["tenant"]) != settings.ENTRA_TENANT_ID.lower():
            raise CommandError("This tenant does not match the deployment's Entra tenant.")
        telemetry.setup("isa9000-web")
        granted = options["action"] == "grant"
        username = str(options["object_id"])
        if not granted and not Profile.objects.filter(user__username=username).exists():
            raise CommandError("No one with this object ID has used this deployment.")
        with transaction.atomic():
            user, created = get_user_model().objects.get_or_create(
                username=username, defaults={"password": make_password(None)},
            )
            profile, _ = Profile.objects.get_or_create(user=user)
            changed = ["local_admin by operator command"]
            profile.local_admin = granted
            profile.save(update_fields=["local_admin"])
            if not granted:
                # The next request restores this if the person is still in the Entra group.
                user.is_superuser = user.is_staff = False
                user.save(update_fields=["is_superuser", "is_staff"])
            if options["activate"] and not user.is_active:
                user.is_active = True
                user.save(update_fields=["is_active"])
                changed.append("is_active by operator command")
            record(
                user, f"admin.app_admin_{'granted' if granted else 'revoked'}", profile,
                actor_id="operator_cli", changed_fields=changed, target_id=user.username,
            )
        self.stdout.write(
            f"Application administrator {'granted to' if granted else 'revoked from'} "
            f"{user.username}. It takes effect on their next request."
        )
        if created:
            self.stdout.write("No one has signed in with this object ID yet; check it is correct.")
        if not user.is_active:
            self.stdout.write("This account is suspended and stays blocked; use --activate.")
        telemetry.flush()
