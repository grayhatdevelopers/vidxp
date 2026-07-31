import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from vidxp.network_share import load_or_create_api_share_token


class NetworkShareTests(unittest.TestCase):
    def test_managed_token_is_stable_and_private(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "api-share-token"

            first = load_or_create_api_share_token(path)
            second = load_or_create_api_share_token(path)

            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_existing_token_is_rejected(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "api-share-token"
            path.write_text("short\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "token is invalid"):
                load_or_create_api_share_token(path)


if __name__ == "__main__":
    unittest.main()
