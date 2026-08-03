import tempfile
import unittest
from pathlib import Path

from utils.verify_published_distribution import (
    distribution_files,
    local_distribution_files,
)


class PublishedDistributionTests(unittest.TestCase):
    def test_publisher_generated_local_attestations_are_ignored(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            wheel = dist / "vidxp-0.4.0.dev20-py3-none-any.whl"
            sdist = dist / "vidxp-0.4.0.dev20.tar.gz"
            wheel.write_bytes(b"wheel")
            sdist.write_bytes(b"sdist")
            (dist / f"{wheel.name}.publish.attestation").write_bytes(b"attestation")
            (dist / f"{sdist.name}.publish.attestation").write_bytes(b"attestation")

            self.assertEqual(
                set(local_distribution_files(dist)),
                {wheel.name, sdist.name},
            )

    def test_unknown_local_files_are_still_compared(self):
        with tempfile.TemporaryDirectory() as temporary:
            dist = Path(temporary)
            unexpected = dist / "unexpected.zip"
            unexpected.write_bytes(b"unexpected")

            self.assertEqual(set(local_distribution_files(dist)), {unexpected.name})

    def test_registry_attestations_are_not_distribution_archives(self):
        payload = {
            "urls": [
                {
                    "filename": "vidxp-0.4.0.dev19-py3-none-any.whl",
                    "digests": {"sha256": "wheel"},
                },
                {
                    "filename": (
                        "vidxp-0.4.0.dev19-py3-none-any.whl.publish.attestation"
                    ),
                    "digests": {"sha256": "wheel-attestation"},
                },
                {
                    "filename": "vidxp-0.4.0.dev19.tar.gz",
                    "digests": {"sha256": "sdist"},
                },
                {
                    "filename": "vidxp-0.4.0.dev19.tar.gz.publish.attestation",
                    "digests": {"sha256": "sdist-attestation"},
                },
            ]
        }

        self.assertEqual(
            distribution_files(payload),
            {
                "vidxp-0.4.0.dev19-py3-none-any.whl": "wheel",
                "vidxp-0.4.0.dev19.tar.gz": "sdist",
            },
        )

    def test_unknown_registry_files_are_still_compared(self):
        payload = {
            "urls": [
                {
                    "filename": "unexpected.zip",
                    "digests": {"sha256": "unexpected"},
                }
            ]
        }

        self.assertEqual(
            distribution_files(payload),
            {"unexpected.zip": "unexpected"},
        )


if __name__ == "__main__":
    unittest.main()
