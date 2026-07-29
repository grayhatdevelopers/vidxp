import unittest

from vidxp.application_boundary import application_boundary
from vidxp.application_models import ApplicationError
from vidxp.core.artifacts import ArtifactRendererUnavailableError
from vidxp.core.media import MediaProbeUnavailableError


class ApplicationBoundaryTests(unittest.TestCase):
    def test_missing_media_probe_carries_init_remediation(self):
        @application_boundary
        def operation():
            raise MediaProbeUnavailableError("missing")

        with self.assertRaises(ApplicationError) as raised:
            operation()

        self.assertEqual(raised.exception.code, "media_probe_unavailable")
        self.assertEqual(
            raised.exception.detail.details["remediation"],
            "vidxp init",
        )

    def test_missing_artifact_renderer_carries_init_remediation(self):
        @application_boundary
        def operation():
            raise ArtifactRendererUnavailableError("missing")

        with self.assertRaises(ApplicationError) as raised:
            operation()

        self.assertEqual(
            raised.exception.code,
            "artifact_renderer_unavailable",
        )
        self.assertEqual(
            raised.exception.detail.details["remediation"],
            "vidxp init",
        )


if __name__ == "__main__":
    unittest.main()
