import asyncio

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fastapi import FastAPI

app = FastAPI()


class Settings(BaseSettings):
    DATABASE_URL: PostgresDsn

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()

engine = create_async_engine(str(settings.DATABASE_URL), echo=True)
async_session = async_sessionmaker(engine, expire_on_commit=False)


@app.get("/health")
def healyh_check():
    return {"status": "ok"}


async def main():
    async with async_session() as session:
        res = await session.execute("SELECT 1")
        print("Успешное подключение! Результат:", res.scalar())


if __name__ == "__main__":
    asyncio.run(main())
