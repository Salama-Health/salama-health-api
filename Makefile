.PHONY: help install dev seed refresh test migrate revision up down logs fmt

help:
	@echo "install   Install Python dependencies"
	@echo "dev       Run the API locally with autoreload (uvicorn)"
	@echo "seed      Populate facilities, workers, demo children + scores"
	@echo "refresh   Recompute all CDI + risk scores once"
	@echo "test      Run the test suite"
	@echo "migrate   Apply Alembic migrations"
	@echo "revision  Autogenerate a new migration (m='message')"
	@echo "up        docker compose up -d --build"
	@echo "down      docker compose down"
	@echo "logs      Tail docker compose logs"

install:
	pip install -r requirements.txt

dev:
	uvicorn app.main:app --reload --port 8000

seed:
	python -m scripts.seed

refresh:
	python -m scripts.refresh_scores

test:
	pytest -q

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
