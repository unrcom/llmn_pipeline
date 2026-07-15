from fastapi import FastAPI

from app.exceptions import register_exception_handlers
from app.routers import health, projects


def create_app() -> FastAPI:
    app = FastAPI(title="llmn_pipeline")
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(projects.router)
    return app


app = create_app()
