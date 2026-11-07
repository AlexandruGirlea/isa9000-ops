.DEFAULT_GOAL := help
COMPOSE ?= docker compose
ENV ?= dev
export PYTHONDONTWRITEBYTECODE := 1

.PHONY: help configure local-run fake-user up up-real scale logs ps shell test check image preview down clean

help:
	@printf '%s\n' \
	  'local-run Build and start locally with real Entra sign-in and the configured worker' \
	  'configure Generate private local settings; no cloud changes' \
	  'fake-user Create a local viewer and print its cookie: ARGS="--group LIBRARY-ID"; n=3 refreshes user #3' \
	  'up       Build and start the configured local stack; defaults to the free stub' \
	  'up-real  Start real isa9000; requires approval and spends Foundry credit' \
	  'scale    Set worker count: make scale n=3' \
	  'logs     Follow logs; optionally s=worker' \
	  'ps       Show services' \
	  'shell    Open a Django shell' \
	  'test     Run ruff and pytest in the image' \
	  'check    Run Django production deployment checks' \
	  'image    Build services:local' \
	  'preview  Read-only Pulumi preview; ENV=dev|test|prod' \
	  'down     Stop services, preserve volumes' \
	  'clean    Stop services and DELETE local database, blobs and queue volumes'

local-run: up
	@printf '%s\n' 'Open http://localhost:8000 and sign in with Microsoft. Entra configuration is required.'

configure:
	python3 local/configure.py

fake-user: export ISA9000_FAKE_USER_NUMBER = $(n)
fake-user:
	@$(COMPOSE) exec -T -e ISA9000_FAKE_USER_NUMBER --workdir /app/web web python manage.py fake_user $(ARGS)

up: configure image
	$(COMPOSE) up --no-build --detach --wait web worker watchdog
	@printf '%s\n' 'Open http://localhost:8000 (or ISA9000_WEB_PORT from .env).'

up-real: configure image
	ISA9000_CLI='python -m worker.cli' \
	  $(COMPOSE) up --no-build --detach --wait web worker watchdog

scale: n ?= 1
scale:
	$(COMPOSE) up --detach --no-recreate --scale worker=$(n) worker

logs:
	$(COMPOSE) logs --follow $(s)

ps:
	$(COMPOSE) ps

shell:
	$(COMPOSE) exec web python manage.py shell

test:
	$(COMPOSE) run --rm --workdir /app web sh -c 'ruff check --no-cache . && pytest -q -p no:cacheprovider'

check:
	$(COMPOSE) run --rm --no-deps -e DEBUG=false -e ISA9000_AUTH=easyauth -e ISA9000_LOCAL_TEST_USERS=false \
	  web python manage.py check --deploy --fail-level WARNING

image:
	docker build --file services/Dockerfile --target development --tag services:local .

preview:
	cd infra && uv run pulumi preview --stack $(ENV)

down:
	$(COMPOSE) down --remove-orphans

clean:
	$(COMPOSE) down --volumes --remove-orphans
