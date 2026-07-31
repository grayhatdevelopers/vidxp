from __future__ import annotations

import argparse
from collections.abc import Sequence
from time import monotonic, sleep
from urllib.request import urlopen

from sqlalchemy import create_engine, select

from vidxp.core.storage import (
    BUNDLED_CHROMA_SERVER_URL,
    ChromaClientFactory,
)
from vidxp.settings import VidXPSettings
from vidxp.workflow_runtime import workflow_database_url


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Check VidXP dependencies.")
    parser.add_argument("role", choices=("chroma", "database", "worker"))
    options = parser.parse_args(arguments)
    settings = VidXPSettings()
    if options.role == "chroma":
        deadline = monotonic() + 120
        endpoint = BUNDLED_CHROMA_SERVER_URL + "/api/v2/heartbeat"
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
        ChromaClientFactory(BUNDLED_CHROMA_SERVER_URL).heartbeat()


if __name__ == "__main__":
    main()
