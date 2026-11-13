"""EdgeFlow API entrypoint."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from auth.site_login import configured as site_login_configured, site_gate
from config import settings
from db.init_db import init_db
from services.scheduler import start_scheduler, shutdown_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.LEGACY_MODE and not site_login_configured() and not settings.JWT_JWKS_URL:
        logging.getLogger(__name__).warning(
            "LEGACY_MODE is off but no sign-in is configured (SITE_USERNAME, SITE_PASSWORD, "
            "JWT_HS256_SECRET): every request will be refused"
        )
    await init_db()
    # Pre-load FinBERT in a thread so it's ready before the first pipeline run
    import asyncio
    from utils.finbert import preload
    asyncio.get_event_loop().run_in_executor(None, preload)
    start_scheduler()
    yield
    shutdown_scheduler()


app = FastAPI(
    title="EdgeFlow API",
    version="1.0.0",
    lifespan=lifespan,
)

# Registered before CORS so CORS stays outermost and a 401 from the gate still carries its headers.
app.middleware("http")(site_gate)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/")
async def root():
    return {"message": "EdgeFlow API", "docs": "/docs"}
