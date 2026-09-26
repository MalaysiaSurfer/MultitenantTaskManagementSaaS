import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session() as session:
        result = await session.execute(text("SELECT 1"))
        print("DB connection ok:", result.scalar())
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)


class Settings(BaseSettings):
    DATABASE_URL: PostgresDsn

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()

engine = create_async_engine(str(settings.DATABASE_URL), echo=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


@app.get("/health")
def health_check():
    return {"status": "ok"}


async def main():
    async with async_session() as session:
        res = await session.execute(text("SELECT 1"))
        print("Успешное подключение! Результат:", res.scalar())
