from __future__ import annotations

import uvicorn

from vidxp.hook_app import create_hook_app
from vidxp.settings import VidXPSettings


def main() -> None:
    settings = VidXPSettings()
    uvicorn.run(
        create_hook_app(settings),
        host=settings.http_bind_host,
        port=settings.http_port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
