import unittest

from pydantic import ValidationError

from vidxp.settings import ApplicationMode, VidXPSettings


class SettingsTests(unittest.TestCase):
    def test_unimplemented_remote_mode_is_rejected_explicitly(self):
        with self.assertRaisesRegex(
            ValidationError,
            "Remote client mode is not available",
        ):
            VidXPSettings(mode=ApplicationMode.remote)


if __name__ == "__main__":
    unittest.main()
