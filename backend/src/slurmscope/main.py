from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from slurmscope.api.routes import router


def create_app() -> FastAPI:
    app = FastAPI(title="SlurmScope", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
