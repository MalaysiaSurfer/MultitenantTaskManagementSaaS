# Multitenant Task Management SaaS

Мини-Trello/Jira: организации, роли, проекты, задачи. FastAPI, PostgreSQL,
SQLAlchemy 2.0 (async), Alembic, Redis, Docker Compose.

## Запуск
cp .env.example .env
docker compose up -d --build
docker compose exec api alembic upgrade head
# Swagger: http://localhost:8000/docs

## Зависимости
uv add <пакет>, затем `uv export --no-hashes --no-dev --no-emit-project -o requirements.txt`
и `uv export --no-hashes --only-dev --no-emit-project -o requirements-dev.txt`.

## Известные ограничения
Refresh-токен не отзывается до истечения срока. План: denylist по `jti` в Redis.