FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS base

LABEL org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.source="https://github.com/AlexandruGirlea/isa9000-ops"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app:/app/web \
    DJANGO_SETTINGS_MODULE=core.settings \
    ISA9000_CONFIG=/config/isa9000.toml

# The Python image can lag Debian's security fixes, and the release scan blocks on them.
RUN apt-get update && apt-get upgrade --yes && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/share/licenses/isa9000-ops/
COPY LICENSES/ /usr/share/licenses/isa9000-ops/LICENSES/
COPY local/start.py /usr/local/bin/start.py
EXPOSE 8000
ENTRYPOINT ["python", "/usr/local/bin/start.py"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "core.wsgi"]

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.10.3@sha256:7a88d4c4e6f44200575000638453a5a381db0ae31ad5c3a51b14f8687c9d93a3 /uv /usr/local/bin/uv
WORKDIR /app
COPY services/pyproject.toml services/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY services/worker ./worker
COPY services/shared ./shared
COPY services/web ./web
RUN uv sync --frozen --no-dev && \
    DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    ISA9000_DJANGO_SECRET_KEY=build-only-static-collection \
    python web/manage.py collectstatic --noinput && \
    rm web/app_portal/management/commands/fake_user.py && \
    chown -R app:app /app

FROM build AS development
COPY --chown=app:app services/tests ./tests
COPY --chown=app:app services/web/app_portal/management/commands ./web/app_portal/management/commands
RUN uv sync --frozen
USER 10001:10001
WORKDIR /app/web

FROM base AS production
RUN /usr/local/bin/python -m pip uninstall --yes pip
COPY --from=build /opt/venv /opt/venv
COPY --from=build --chown=app:app /app /app
USER 10001:10001
WORKDIR /app/web
