up:
	docker compose -f infra/docker/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker/docker-compose.yml down

migrate:
	docker compose -f infra/docker/docker-compose.yml exec api python manage.py migrate

test:
	docker compose -f infra/docker/docker-compose.yml exec api pytest

# Live end-to-end auth check against the real .env: real database, real JWT,
# real Brevo. `e2e` seeds a known code and sends no mail; `e2e-email` sends one.
e2e:
	docker compose -f infra/docker/docker-compose.yml exec api python scripts/e2e_email_auth.py

e2e-email:
	docker compose -f infra/docker/docker-compose.yml exec api python scripts/e2e_email_auth.py --mode email --email $(EMAIL)

lint:
	docker compose -f infra/docker/docker-compose.yml exec api ruff check .
