import unittest
from unittest.mock import patch

from vidxp.database_cli import main
from vidxp.workflow_runtime import BUNDLED_POSTGRES_DATABASE_URL


class DatabaseCliTests(unittest.TestCase):
    def test_migrations_use_the_bundled_server_database(self):
        with patch("vidxp.database_cli.upgrade_database") as upgrade:
            main([])

        upgrade.assert_called_once_with(BUNDLED_POSTGRES_DATABASE_URL)

    def test_database_url_command_line_override_is_not_supported(self):
        with self.assertRaises(SystemExit) as raised:
            main(["--database-url", "postgresql://external.example/vidxp"])

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
