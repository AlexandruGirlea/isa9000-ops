from __future__ import annotations

from core.audit import record
from core.views import page
from django.contrib import messages
from django.contrib.admin.models import ADDITION
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from app_portal.forms import ContactForm
from app_portal.models import ContactRequest
from app_portal.permissions import is_creator


@require_http_methods(["GET", "POST"])
def contact(request):
    if not is_creator(request.user):
        raise Http404
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            item = ContactRequest.objects.create(
                body=form.cleaned_data["description"], created_by=request.user,
            )
            record(request.user, "contact.created", item, flag=ADDITION)
        messages.success(request, "Thank you. Your request was sent to your administrators.")
        return redirect("libraries")
    return page(request, "contact.html", {"form": form})
