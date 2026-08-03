import json
import tempfile
import unittest
from pathlib import Path

from utils.release_contract import validate, version_sources
from utils.prepare_nightly import prepare


class ReleaseContractTests(unittest.TestCase):
    def current_release(self) -> tuple[str, str]:
        versions = set(version_sources().values())
        self.assertEqual(len(versions), 1)
        version = versions.pop()
        channel = "beta" if "-b" in version else "stable"
        return version, channel

    def copy_contract(self, destination: Path) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "pyproject.toml",
            "desktop/package.json",
            "desktop/package-lock.json",
            "desktop/runtime-manifest.json",
            "desktop/src-tauri/Cargo.toml",
            "desktop/src-tauri/tauri.conf.json",
        ):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / relative).read_bytes())

    def test_current_contract_matches_every_release_source(self):
        version, channel = self.current_release()
        self.assertEqual(validate(channel, f"v{version}"), version)

    def test_rejects_a_divergent_desktop_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.copy_contract(root)
            package = json.loads((root / "desktop/package.json").read_text())
            package["version"] = "9.9.9"
            (root / "desktop/package.json").write_text(json.dumps(package))
            with self.assertRaisesRegex(ValueError, "sources disagree"):
                validate("beta", None, root)

    def test_beta_and_stable_channels_are_not_interchangeable(self):
        _, channel = self.current_release()
        other_channel = "stable" if channel == "beta" else "beta"
        with self.assertRaisesRegex(ValueError, f"{other_channel} release version"):
            validate(other_channel, None)

    def test_tag_must_match_the_combined_version(self):
        _, channel = self.current_release()
        with self.assertRaisesRegex(ValueError, "does not match"):
            validate(channel, "v999.999.999")

    def test_nightly_version_is_unique_without_changing_release_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "pyproject.toml"
            project.write_text('[project]\nname = "vidxp"\nversion = "0.4.0-b"\n')
            self.assertEqual(prepare(project, 12345), "0.4.0.dev12345")
            self.assertIn('version = "0.4.0.dev12345"', project.read_text())


if __name__ == "__main__":
    unittest.main()
