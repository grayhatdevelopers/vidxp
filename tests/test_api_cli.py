import unittest
from contextlib import redirect_stdout
from io import StringIO

from vidxp.api_cli import main


class ApiCliTests(unittest.TestCase):
    def test_help_exits_without_starting_the_service(self):
        output = StringIO()

        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--help"])

        self.assertEqual(caught.exception.code, 0)
        self.assertIn("VIDXP_* environment variables", output.getvalue())


if __name__ == "__main__":
    unittest.main()
