from __future__ import annotations

import argparse
from importlib.resources import as_file, files
from typing import Sequence

from alembic import command
from alembic.config import Config

from vidxp.settings import ApplicationMode, VidXPSettings
from vidxp.workflow_runtime import workflow_database_url


def upgrade_database(database_url: str) -> None:
    migration_root = files("vidxp.migrations")
    with as_file(migration_root) as path:
        config = Config()
        config.set_main_option("script_location", str(path))
        config.set_main_option(
            "sqlalchemy.url",
            database_url.replace("%", "%%"),
        )
        command.upgrade(config, "head")


def main(arguments: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Apply VidXP relational database migrations."
    )
    parser.parse_args(arguments)
    settings = VidXPSettings(
        mode=ApplicationMode.server,
        runtime_backend="cpu",
    )
    upgrade_database(workflow_database_url(settings))


if __name__ == "__main__":
    main()
