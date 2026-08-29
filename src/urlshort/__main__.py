"""`python -m urlshort` — the entrypoint the container runs."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "urlshort.api:app",
        host=os.environ.get("HOST", "0.0.0.0"),  # noqa: S104 - it is a container
        port=int(os.environ.get("PORT", "8000")),
        log_level=os.environ.get("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
