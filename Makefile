up:
	docker compose -f infra/docker/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker/docker-compose.yml down

migrate:
	docker compose -f infra/docker/docker-compose.yml exec api python manage.py migrate

test:
	docker compose -f infra/docker/docker-compose.yml exec api pytest

lint:
	docker compose -f infra/docker/docker-compose.yml exec api ruff check .
