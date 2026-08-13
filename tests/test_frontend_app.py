import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from vidxp.application_models import (
    DependencyCheckResult,
    FusedMoment,
    FusedSearchResult,
    FusionProvenance,
    IndexStatus,
    IndexStatusSummary,
    Job,
    JobKind,
    JobProgress,
    JobQueue,
    JobState,
    MediaAsset,
    MediaPage,
    SearchHit,
    SearchJobResult,
)
from vidxp.capabilities.registry import create_capability_registry
from vidxp.capability_service import CapabilityService
from vidxp.core.media import MediaState, MediaStream
from vidxp.ports import LocalFileResource


MEDIA_ID = "123456781234423481234567890abcde"
GENERATION_ID = "223456781234423481234567890abcde"
SNAPSHOT_ID = "323456781234423481234567890abcde"
INDEX_JOB_ID = "423456781234423481234567890abcde"
SEARCH_JOB_ID = "523456781234423481234567890abcde"


def render_frontend(service, jobs):
    from unittest.mock import patch

    from vidxp import frontend

    with (
        patch.object(
            frontend,
            "_configured_service",
            return_value=service,
        ),
        patch.object(
            frontend,
            "_configured_jobs",
            return_value=jobs,
        ),
    ):
        frontend.run()


class FrontendApplicationStub:
    def __init__(self, root: Path, status: IndexStatus):
        self.layout = SimpleNamespace(root=root)
        self.model_cache = root / "models"
        self._status = status
        self._capabilities = CapabilityService(
            create_capability_registry()
        )
        self._media = MediaAsset(
            media_id=MEDIA_ID,
            video_id=MEDIA_ID,
            original_filename="video.mp4",
            sha256="1" * 64,
            byte_size=10,
            detected_mime_type="video/mp4",
            container="mp4",
            duration_seconds=27.2,
            streams=(
                MediaStream(
                    index=0,
                    kind="video",
                    codec="h264",
                    width=1,
                    height=1,
                ),
            ),
            state=MediaState.ready,
            created_at=datetime.now(timezone.utc),
        )
        self._video_path = root / "video.mp4"
        self._video_path.write_bytes(b"test video")

    def list_capabilities(self):
        return self._capabilities.list()

    def get_capability(self, name):
        return self._capabilities.get(name)

    def check_dependencies(self, command):
        return DependencyCheckResult(
            ok=True,
            modalities=command.modalities,
            checks=(),
        )

    def model_readiness(self, modalities):
        return DependencyCheckResult(
            ok=True,
            modalities=modalities,
            checks=(),
        )

    def index_status(self):
        return self._status

    def list_media(self, command):
        del command
        return MediaPage(items=(self._media,), total=1)

    def open_media_content(self, media_id):
        if media_id != MEDIA_ID:
            raise AssertionError(f"unexpected media ID: {media_id}")
        return LocalFileResource(
            path=self._video_path,
            filename=self._media.original_filename,
            mime_type=self._media.detected_mime_type,
            byte_size=self._media.byte_size,
            etag=self._media.sha256,
        )


class FrontendJobStub:
    def __init__(self):
        self.jobs = {}
        self.submitted_searches = []

    def get(self, job_id):
        return self.jobs.get(job_id)

    def submit_search(self, command):
        self.submitted_searches.append(command)
        job = Job(
            job_id=SEARCH_JOB_ID,
            kind=JobKind.search,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        self.jobs[job.job_id] = job
        return job

    def cancel(self, job_id):
        raise AssertionError(f"unexpected cancellation: {job_id}")


def ready_status() -> IndexStatus:
    return IndexStatus(
        schema_version=1,
        state="ready",
        stage="complete",
        message="The video index is ready.",
        summary=IndexStatusSummary(
            index_schema_version=1,
            snapshot_id=SNAPSHOT_ID,
            media_count=1,
            media_ids=(MEDIA_ID,),
            modalities=("dialogue", "scene", "actor", "videoprism"),
        ),
    )


def scene_search_result() -> FusedSearchResult:
    hit = SearchHit(
        rank=1,
        media_id=MEDIA_ID,
        video_id=MEDIA_ID,
        generation_id=GENERATION_ID,
        start=3.0,
        end=4.0,
        score=0.8,
        raw_distance=0.2,
        modality="scene",
        source_id="scene:3",
    )
    return FusedSearchResult(
        query_id="query-1",
        query="man in gray pants",
        modalities=("scene",),
        moments=(
            FusedMoment(
                rank=1,
                score=0.8,
                media_id=MEDIA_ID,
                start=3.0,
                end=4.0,
                modalities=("scene",),
                hits=(hit,),
            ),
        ),
        fusion=FusionProvenance(
            requested_modalities=("scene",),
            searched_modalities=("scene",),
        ),
    )


class FrontendAppTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def widget(elements, label):
        return next(item for item in elements if item.label == label)

    def app(self, service, jobs):
        return AppTest.from_function(
            render_frontend,
            args=(service, jobs),
            default_timeout=10,
        )

    def test_ready_page_renders_once_and_submits_first_search(self):
        service = FrontendApplicationStub(self.root, ready_status())
        jobs = FrontendJobStub()
        app = self.app(service, jobs).run()

        self.assertEqual(tuple(app.exception), ())
        self.assertEqual(len(app.get("video")), 1)
        search_form = app.get("form")
        self.assertEqual(len(search_form), 1)
        self.assertTrue(search_form[0].proto.form.enter_to_submit)
        search_type = self.widget(app.selectbox, "Search type")
        search_query = app.text_input(key="video_search_query")
        search_button = self.widget(app.button, "Search")
        self.assertFalse(search_type.disabled)
        self.assertFalse(search_query.disabled)
        self.assertFalse(search_button.disabled)

        search_type.select("scene")
        search_query.input("man in gray pants")
        search_button.click()
        app.run()

        self.assertEqual(tuple(app.exception), ())
        self.assertEqual(len(jobs.submitted_searches), 1)
        self.assertEqual(
            jobs.submitted_searches[0].query,
            "man in gray pants",
        )
        self.assertTrue(
            any(
                item.value == "⏳ Starting scene search..."
                for item in app.markdown
            )
        )

    def test_ready_page_rejects_empty_search_without_disabling_form(self):
        service = FrontendApplicationStub(self.root, ready_status())
        jobs = FrontendJobStub()
        app = self.app(service, jobs).run()

        search_button = self.widget(app.button, "Search")
        search_button.click()
        app.run()

        self.assertEqual(tuple(app.exception), ())
        self.assertFalse(self.widget(app.button, "Search").disabled)
        self.assertEqual(app.warning[-1].value, "Enter a search query.")
        self.assertEqual(jobs.submitted_searches, [])

    def test_ready_page_exposes_videoprism_as_temporal_action_search(self):
        service = FrontendApplicationStub(self.root, ready_status())
        jobs = FrontendJobStub()
        app = self.app(service, jobs).run()

        capability_picker = self.widget(app.multiselect, "Capabilities")
        self.assertIn(
            "Temporal action search (VideoPrism)",
            capability_picker.options,
        )
        temporal_control = self.widget(app.selectbox, "Temporal clip length")
        self.assertEqual(temporal_control.value, 2.0)

        search_type = self.widget(app.selectbox, "Search type")
        search_type.select("videoprism")
        app.text_input(key="video_search_query").input("a person walks out")
        self.widget(app.button, "Search").click()
        app.run()

        self.assertEqual(jobs.submitted_searches[0].modalities, ("videoprism",))

    def test_running_index_keeps_one_preview_and_disables_mutations(self):
        service = FrontendApplicationStub(self.root, ready_status())
        jobs = FrontendJobStub()
        jobs.jobs[INDEX_JOB_ID] = Job(
            job_id=INDEX_JOB_ID,
            kind=JobKind.index,
            state=JobState.running,
            queue=JobQueue.cpu,
            progress=JobProgress(
                stage="scene",
                message="Indexing sampled scenes.",
                current=2,
                total=10,
                updated_at=datetime.now(timezone.utc),
            ),
        )
        app = self.app(service, jobs)
        app.query_params["index_job"] = INDEX_JOB_ID
        app.run()

        self.assertEqual(tuple(app.exception), ())
        self.assertEqual(len(app.get("video")), 1)
        self.assertTrue(
            self.widget(app.selectbox, "Registered video").disabled
        )
        self.assertTrue(app.file_uploader[0].disabled)
        self.assertTrue(app.multiselect[0].disabled)
        self.assertTrue(self.widget(app.selectbox, "Scene detail").disabled)
        self.assertTrue(self.widget(app.selectbox, "Search type").disabled)
        self.assertTrue(
            app.text_input(key="video_search_query").disabled
        )
        self.assertTrue(self.widget(app.button, "Index video").disabled)
        self.assertTrue(self.widget(app.button, "Search").disabled)
        self.assertFalse(
            self.widget(app.button, "Cancel indexing").disabled
        )
        self.assertTrue(
            any(
                item.value == "⏳ Indexing sampled scenes."
                for item in app.markdown
            )
        )

    def test_restored_search_job_moves_from_queued_to_typed_result(self):
        service = FrontendApplicationStub(self.root, ready_status())
        jobs = FrontendJobStub()
        jobs.jobs[SEARCH_JOB_ID] = Job(
            job_id=SEARCH_JOB_ID,
            kind=JobKind.search,
            state=JobState.queued,
            queue=JobQueue.cpu,
        )
        app = self.app(service, jobs)
        app.query_params["search_job"] = SEARCH_JOB_ID
        app.query_params["search_type"] = "scene"
        app.run()

        self.assertEqual(tuple(app.exception), ())
        self.assertTrue(
            any(
                item.value == "⏳ Starting scene search..."
                for item in app.markdown
            )
        )

        jobs.jobs[SEARCH_JOB_ID] = Job(
            job_id=SEARCH_JOB_ID,
            kind=JobKind.search,
            state=JobState.succeeded,
            queue=JobQueue.cpu,
            result=SearchJobResult(result=scene_search_result()),
        )
        app.run()

        self.assertEqual(tuple(app.exception), ())
        self.assertEqual(len(app.get("video")), 2)
        self.assertEqual(
            app.success[-1].value,
            "Closest sampled scene: 3.0 seconds",
        )
        self.assertNotIn("search_job", app.query_params)
        self.assertNotIn("search_type", app.query_params)


if __name__ == "__main__":
    unittest.main()
