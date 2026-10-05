from fastapi import FastAPI
from contextlib import asynccontextmanager
from sqlalchemy import text
from app.db.session import engine, async_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with async_session() as session:
        result = await session.execute(text("SELECT 1"))
        print("DB connection ok:", result.scalar())
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health_check():
    return {"status": "ok"}
