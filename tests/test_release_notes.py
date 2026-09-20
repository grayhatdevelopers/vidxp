import tempfile
import unittest
from pathlib import Path

from utils.render_release_notes import render


ROOT = Path(__file__).resolve().parents[1]


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.assets = Path(self.temporary.name)
        for name in (
            "VidXP Setup.exe",
            "VidXP.dmg",
            "VidXP.AppImage",
            "SHA256SUMS",
        ):
            (self.assets / name).touch()
        self.template = (
            ROOT / ".github" / "release-intro.md"
        ).read_text(encoding="utf-8")

    def render(self, notes: str, channel: str = "stable") -> str:
        return render(
            template=self.template,
            existing_notes=notes,
            assets=self.assets,
            repository="GrayhatDevelopers/VidXP",
            tag="v0.4.0-b",
            version="0.4.0-b",
            channel=channel,
        )

    def test_release_page_links_exact_assets_before_changelog(self):
        result = self.render("## Changelog\n\n* Added evidence boards.")

        self.assertLess(result.index("## Download VidXP"), result.index("## Changelog"))
        self.assertIn("VidXP%20Setup.exe", result)
        self.assertIn(
            "uv tool install --python 3.14 --torch-backend cpu", result
        )
        self.assertIn("vidxp[local-worker,mcp]==0.4.0-b", result)
        self.assertIn("vidxp[local-worker,mcp,frontend]==0.4.0-b", result)
        self.assertNotIn("python -m pip install", result)
        self.assertIn("ghcr.io/grayhatdevelopers/vidxp:0.4.0-b", result)
        self.assertIn(
            "https://github.com/grayhatdevelopers/vidxp/pkgs/container/vidxp",
            result,
        )
        self.assertNotIn("Beta release", result)

    def test_beta_warning_and_rendering_are_idempotent(self):
        first = self.render("* Fixed upload recovery.", channel="beta")
        second = self.render(first, channel="beta")

        self.assertEqual(first, second)
        self.assertEqual(first.count("Beta release"), 1)
        self.assertEqual(first.count("Fixed upload recovery"), 1)
        self.assertIn(
            "https://github.com/grayhatdevelopers/vidxp/pkgs/container/vidxp",
            first,
        )

    def test_rejects_an_ambiguous_platform_asset(self):
        (self.assets / "another.exe").touch()
        with self.assertRaisesRegex(ValueError, "exactly one .*exe"):
            self.render("changes")


if __name__ == "__main__":
    unittest.main()
