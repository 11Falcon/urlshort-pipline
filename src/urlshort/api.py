"""The HTTP surface. Four endpoints, and two of them exist for the pipeline."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from urlshort import __version__
from urlshort.store import InvalidURL, Store, UnknownCode

app = FastAPI(title="urlshort", version=__version__)
store = Store()


class ShortenRequest(BaseModel):
    url: str


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness. Cheap, dependency-free, and never authenticated."""
    return JSONResponse({"status": "ok"})


@app.get("/version")
def version() -> JSONResponse:
    """What is actually running here.

    A deploy you cannot identify is a deploy you cannot roll back with
    confidence, so the pipeline stamps the commit and image tag into the
    environment and this endpoint reads them back out.
    """
    return JSONResponse(
        {
            "version": __version__,
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "image_tag": os.environ.get("IMAGE_TAG", "unknown"),
            "environment": os.environ.get("APP_ENV", "local"),
        }
    )


@app.post("/shorten")
def shorten(req: ShortenRequest) -> JSONResponse:
    try:
        code = store.shorten(req.url)
    except InvalidURL as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"code": code, "url": store.resolve(code)}, status_code=201)


@app.get("/r/{code}")
def redirect(code: str) -> RedirectResponse:
    try:
        target = store.resolve(code)
    except UnknownCode as exc:
        raise HTTPException(status_code=404, detail="unknown code") from exc
    return RedirectResponse(target, status_code=307)
