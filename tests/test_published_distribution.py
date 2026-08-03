import unittest

from utils.verify_published_distribution import distribution_files


class PublishedDistributionTests(unittest.TestCase):
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
