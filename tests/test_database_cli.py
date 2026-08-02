import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from vidxp.database_cli import main
from vidxp.workflow_runtime import BUNDLED_POSTGRES_DATABASE_URL


class DatabaseCliTests(unittest.TestCase):
    @staticmethod
    def _sqlite_migration_config(path: Path) -> Config:
        config = Config()
        config.set_main_option("script_location", "src/vidxp/migrations")
        config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
        return config

    def test_migrations_use_the_bundled_server_database(self):
        with patch("vidxp.database_cli.upgrade_database") as upgrade:
            main([])

        upgrade.assert_called_once_with(BUNDLED_POSTGRES_DATABASE_URL)

    def test_database_url_command_line_override_is_not_supported(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--database-url", "postgresql://external.example/vidxp"])

        self.assertEqual(raised.exception.code, 2)

    def test_native_ingestion_migration_is_the_only_head(self):
        config = Config()
        config.set_main_option(
            "script_location",
            "src/vidxp/migrations",
        )

        scripts = ScriptDirectory.from_config(config)

        self.assertEqual(scripts.get_heads(), ["20260802_01"])

    def test_sqlite_upgrade_downgrade_and_reupgrade_from_pre_feature(self):
        with TemporaryDirectory() as temporary:
            database = Path(temporary) / "migrations.sqlite3"
            config = self._sqlite_migration_config(database)
            command.upgrade(config, "20260729_01")
            command.upgrade(config, "head")

            engine = create_engine(f"sqlite:///{database.as_posix()}")
            try:
                upgraded = inspect(engine)
                self.assertIn("upload_sessions", upgraded.get_table_names())
                intent_columns = {
                    item["name"] for item in upgraded.get_columns("upload_intents")
                }
                self.assertIn("content_sha256", intent_columns)
                self.assertIn("index_job_id", intent_columns)
                self.assertIn("index_command", intent_columns)
                self.assertTrue(
                    any(
                        index["name"] == "upload_intents_index_job_id"
                        and not index["unique"]
                        for index in upgraded.get_indexes("upload_intents")
                    )
                )

                command.downgrade(config, "20260729_01")
                downgraded = inspect(engine)
                self.assertNotIn("upload_sessions", downgraded.get_table_names())
                self.assertNotIn(
                    "index_job_id",
                    {
                        item["name"]
                        for item in downgraded.get_columns("upload_intents")
                    },
                )

                command.upgrade(config, "head")
                with engine.connect() as connection:
                    self.assertEqual(
                        connection.execute(
                            text("SELECT version_num FROM alembic_version")
                        ).scalar_one(),
                        "20260802_01",
                    )
                self.assertIn("upload_sessions", inspect(engine).get_table_names())
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
