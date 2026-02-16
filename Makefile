.PHONY: help install install-dev run migrate mm makemigrations superuser shell db-shell test cov format lint clean


# Colors
YELLOW = \033[33m
GREEN  = \033[32m
RED    = \033[31m
RESET  = \033[0m

help:
	@echo "$(YELLOW)Available commands:$(RESET)"
	@echo "  $(GREEN)install$(RESET)          - Install production dependencies"
	@echo "  $(GREEN)install-dev$(RESET)      - Install all dependencies (incl. dev)"
	@echo "  $(GREEN)run$(RESET)              - Start Django dev server (0.0.0.0:8000)"
	@echo "  $(GREEN)migrate$(RESET)          - Apply migrations"
	@echo "  $(GREEN)mm$(RESET)               - Make migrations"
	@echo "  $(GREEN)superuser$(RESET)        - Create superuser"
	@echo "  $(GREEN)shell$(RESET)            - Django shell_plus (with IPython + SQL print)"
	@echo "  $(GREEN)db-shell$(RESET)         - Open PostgreSQL shell"
	@echo "  $(GREEN)test$(RESET)             - Run tests"
	@echo "  $(GREEN)cov$(RESET)              - Run tests with coverage report"
	@echo "  $(GREEN)format$(RESET)           - Format code (black + isort)"
	@echo "  $(GREEN)lint$(RESET)             - Run flake8 + mypy"
	@echo "  $(GREEN)clean$(RESET)            - Remove .pyc, cache files, etc."

install:
	@echo "$(YELLOW)Installing production dependencies...$(RESET)"
	poetry install --only main --sync

install-dev:
	@echo "$(YELLOW)Installing all dependencies (including dev)...$(RESET)"
	poetry install --sync

run:
	poetry run python manage.py runserver 0.0.0.0:8000

migrate:
	poetry run python manage.py migrate $(filter-out $@,$(MAKECMDGOALS))

mm: makemigrations
makemigrations:
	poetry run python manage.py makemigrations $(filter-out $@,$(MAKECMDGOALS))

superuser:
	poetry run python manage.py createsuperuser

su: superuser

leave:
	poetry run python manage.py leave

app:
	@if [ -z "$(filter-out $@,$(MAKECMDGOALS))" ]; then \
		echo "$(RED)Error: No app name provided. Usage: make app <name>$(RESET)"; \
		exit 1; \
	fi
	poetry run python manage.py startapp $(filter-out $@,$(MAKECMDGOALS))
shell:
	poetry run python manage.py shell_plus --print-sql

db-shell:
	poetry run python manage.py dbshell

test:
	poetry run pytest

cov:
	poetry run pytest --cov=. --cov-report=term-missing --cov-report=html

# Versioning & Release - sematic releases
version-check:
	@echo "Checking next version (dry-run)..."
	poetry run semantic-release version --print

version-info:
	@echo "Current version: $$(poetry version -s)"
	@echo "Next version: $$(poetry run semantic-release version --print)"

changelog:
	poetry run semantic-release changelog

release:
	@echo "Creating new release..."
	poetry run semantic-release version
	@echo ""
	@echo "Release created! Push to GitHub with:"
	@echo "  git push --follow-tags origin main"

publish:
	poetry run semantic-release publish

format:
	poetry run isort .
	poetry run black .

lint:
	@echo "$(YELLOW)Running flake8...$(RESET)"
	poetry run flake8 .
	@echo "$(YELLOW)Running mypy...$(RESET)"
	poetry run mypy .

clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	rm -rf htmlcov .coverage

%:
	@: