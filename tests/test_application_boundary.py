import unittest

from vidxp.application_boundary import application_boundary
from vidxp.application_models import ApplicationError
from vidxp.artifact_service import ArtifactNotFoundError, ArtifactNotReadyError
from vidxp.core.artifacts import ArtifactRendererUnavailableError
from vidxp.core.media import MediaProbeUnavailableError


class ApplicationBoundaryTests(unittest.TestCase):
    def test_artifact_delivery_distinguishes_not_found_from_not_ready(self):
        cases = (
            (ArtifactNotFoundError("missing"), "artifact_not_found", False),
            (ArtifactNotReadyError("pending"), "artifact_not_ready", True),
        )
        for source, code, retryable in cases:
            with self.subTest(code=code):
                @application_boundary
                def operation():
                    raise source

                with self.assertRaises(ApplicationError) as raised:
                    operation()

                self.assertEqual(raised.exception.code, code)
                self.assertEqual(raised.exception.retryable, retryable)

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
