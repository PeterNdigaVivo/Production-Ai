.PHONY: help up down logs build test lint fmt migrate seed clean ps backend-shell frontend-shell

COMPOSE := docker compose

help:
	@grep -E '^[a-zA-Z_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?##"} {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start dev stack
	$(COMPOSE) up -d --build

down: ## Stop stack
	$(COMPOSE) down

logs: ## Tail all logs
	$(COMPOSE) logs -f --tail=200

ps: ## List services
	$(COMPOSE) ps

build: ## Build all images
	$(COMPOSE) build

migrate: ## Run Alembic migrations
	$(COMPOSE) run --rm backend alembic upgrade head

seed: ## Seed demo factory/line/camera
	$(COMPOSE) run --rm backend python -m app.scripts.seed_demo

test: ## Run backend test suite
	$(COMPOSE) run --rm backend pytest -q

lint: ## Lint backend
	$(COMPOSE) run --rm backend ruff check .

fmt: ## Format backend
	$(COMPOSE) run --rm backend ruff format .

backend-shell:
	$(COMPOSE) exec backend bash

frontend-shell:
	$(COMPOSE) exec frontend sh

clean: ## Remove volumes (DESTRUCTIVE)
	$(COMPOSE) down -v
