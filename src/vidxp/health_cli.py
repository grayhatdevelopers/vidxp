from __future__ import annotations

import argparse
from collections.abc import Sequence
from time import monotonic, sleep
from urllib.request import urlopen

from sqlalchemy import create_engine, select

from vidxp.core.storage import ChromaClientFactory
from vidxp.settings import VidXPSettings
from vidxp.workflow_runtime import workflow_database_url


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check VidXP dependencies.")
    parser.add_argument("role", choices=("chroma", "database", "worker"))
    options = parser.parse_args(arguments)
    settings = VidXPSettings()
    if options.role == "chroma":
        if settings.chroma_server_url is None:
            raise RuntimeError("Chroma readiness requires a server URL.")
        deadline = monotonic() + 120
        endpoint = settings.chroma_server_url.rstrip("/") + "/api/v2/heartbeat"
        while True:
            try:
                with urlopen(endpoint, timeout=3) as response:
                    if response.status == 200:
                        return
            except OSError:
                if monotonic() >= deadline:
                    raise
                sleep(1)
    engine = create_engine(
        workflow_database_url(settings),
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            connection.execute(select(1)).scalar_one()
    finally:
        engine.dispose()
    if options.role == "worker":
        if settings.chroma_server_url is None:
            raise RuntimeError("Worker readiness requires Chroma server mode.")
        ChromaClientFactory(settings.chroma_server_url).heartbeat()


if __name__ == "__main__":
    main()
