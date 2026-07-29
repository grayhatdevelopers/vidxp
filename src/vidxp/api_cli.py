from __future__ import annotations


def main() -> None:
    import uvicorn

    from vidxp.api import create_app
    from vidxp.settings import VidXPSettings

    settings = VidXPSettings()
    settings.validate_http_server()
    uvicorn.run(
        create_app(settings),
        host=settings.http_bind_host,
        port=settings.http_port,
    )
