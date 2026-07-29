import argparse
import logging
import shutil
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import streamlit as st

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ApplicationError,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    DependencyCheckCommand,
    ImportMediaCommand,
    JobState,
    SearchCommand,
)
from vidxp.composition import create_application, create_job_service
from vidxp.index_state import IndexNotReadyError
from vidxp.job_service import JobService
from vidxp.settings import LocalExecutionSettings, VidXPSettings


LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _configured_service(
    settings: VidXPSettings | None = None,
) -> VidXPApplication:
    return create_application(settings or _settings_from_arguments())


@lru_cache(maxsize=1)
def _configured_jobs(settings: VidXPSettings | None = None) -> JobService:
    return create_job_service(settings or _settings_from_arguments())


def _settings_from_arguments(
    arguments: Sequence[str] | None = None,
) -> VidXPSettings:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--vidxp-settings-json")
    parsed, _ = parser.parse_known_args(
        list(sys.argv[1:] if arguments is None else arguments)
    )
    if parsed.vidxp_settings_json is None:
        return VidXPSettings()
    return LocalExecutionSettings.model_validate_json(
        parsed.vidxp_settings_json
    ).application_settings()


INDEX_REQUESTED_KEY = "_vidxp_index_requested"
INDEX_ERROR_KEY = "_vidxp_index_error"
INDEX_JOB_ID_KEY = "_vidxp_index_job_id"
SEARCH_RESULT_KEY = "_vidxp_search_result"
CANCEL_REQUESTED_KEY = "_vidxp_cancel_requested"
MEDIA_ID_KEY = "_vidxp_media_id"
UPLOAD_TOKEN_KEY = "_vidxp_upload_token"


def _upload_token(uploaded_video):
    if uploaded_video is None:
        return None
    return (
        getattr(uploaded_video, "file_id", None),
        uploaded_video.name,
        getattr(uploaded_video, "size", None),
    )


def _is_search_ready(status, media_id: str | None) -> bool:
    if not status or status.get("state") != "ready":
        return False
    return media_id is not None and media_id in (
        (status.get("summary") or {}).get("media_ids") or ()
    )


def _render_summary(summary):
    if not summary:
        return
    st.caption(
        " · ".join(
            (
                f"Media: {summary.get('media_count', 0):,}",
                "Capabilities: "
                + ", ".join(summary.get("modalities", ())),
            )
        )
    )


def _render_progress(event):
    st.markdown(f"⏳ {event['message']}")
    current, total = event.get("current"), event.get("total")
    if current is not None and total:
        st.progress(
            min(current / total, 1.0),
            text=f"{current:,} of {total:,}",
        )


def _get_job(jobs: JobService, job_id: str | None):
    if job_id is None:
        return None
    try:
        return jobs.get(job_id)
    except ApplicationError:
        LOGGER.warning("Ignoring unavailable background job %s.", job_id)
        return None


def _render_index_status(status, active, media_id, request_error=None):
    if request_error:
        st.error(request_error)
        return

    if active:
        event = status or {
            "state": "indexing",
            "stage": "initializing",
            "message": "Indexing is running.",
        }
        _render_progress(event)
    elif not status or status.get("state") == "missing":
        st.caption("First indexing may download missing runtime model weights.")
    elif status["state"] == "ready":
        if _is_search_ready(status, media_id):
            st.success(status.get("message", "The video index is ready."))
            _render_summary(status.get("summary"))
        else:
            st.info("The selected video has not been indexed yet.")
    elif status["state"] == "failed":
        st.error(status.get("message", "Video indexing failed."))
        if status.get("error"):
            with st.expander("Error details"):
                st.code(status["error"])
    elif status["state"] == "indexing":
        st.warning(
            "The previous run stopped while "
            f"{status.get('stage', 'indexing').replace('_', ' ')}. "
            "Restart indexing before searching."
        )
    elif status["state"] == "interrupted":
        st.warning("Indexing was cancelled. Restart it before searching.")


def _request_indexing():
    st.session_state[INDEX_REQUESTED_KEY] = True
    st.session_state.pop(INDEX_ERROR_KEY, None)
    st.session_state.pop(SEARCH_RESULT_KEY, None)


def _request_cancellation():
    job_id = st.session_state.get(INDEX_JOB_ID_KEY)
    if job_id is not None:
        _configured_jobs().cancel(job_id)
        st.session_state[CANCEL_REQUESTED_KEY] = True
        st.session_state.pop(INDEX_ERROR_KEY, None)
    else:
        st.session_state[INDEX_ERROR_KEY] = (
            "This indexing process cannot be cancelled from the current UI."
        )


def _available_index_modalities() -> tuple[str, ...]:
    service = _configured_service()
    return tuple(
        name
        for name in service.registry.index_names()
        if service.check_dependencies(
            DependencyCheckCommand(modalities=(name,))
        ).ok
    )


def _run_indexing(uploaded_video, status, modalities):
    service = _configured_service()
    temporary_path = None
    try:
        if uploaded_video is not None:
            suffix = Path(uploaded_video.name).suffix
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                suffix=suffix,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                uploaded_video.seek(0)
                shutil.copyfileobj(uploaded_video, temporary)
            asset = service.import_media(
                ImportMediaCommand(
                    path=temporary_path,
                    original_filename=Path(uploaded_video.name).name,
                    declared_mime_type=getattr(uploaded_video, "type", None),
                )
            )
            media_id = asset.media_id
            st.session_state[MEDIA_ID_KEY] = media_id
            st.session_state[UPLOAD_TOKEN_KEY] = _upload_token(uploaded_video)
        else:
            media_id = st.session_state.get(MEDIA_ID_KEY)
            if media_id is None and status:
                media_ids = (status.get("summary") or {}).get("media_ids") or ()
                media_id = media_ids[0] if len(media_ids) == 1 else None
            if media_id is None:
                raise ValueError("Select or import media before indexing.")
        job = _configured_jobs().submit_index(
            CreateIndexCommand(
                media_id=media_id,
                modalities=modalities,
            )
        )
        st.session_state[INDEX_JOB_ID_KEY] = job.job_id
    except ApplicationError as exc:
        st.session_state[INDEX_ERROR_KEY] = str(exc)
    except Exception:
        LOGGER.exception("Unexpected indexing request failure")
        st.session_state[INDEX_ERROR_KEY] = (
            "Indexing could not be started. Check the application logs."
        )
    else:
        st.session_state.pop(INDEX_ERROR_KEY, None)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        st.session_state[INDEX_REQUESTED_KEY] = False
    st.rerun()


def _run_search(search_type, query):
    service = _configured_service()
    try:
        status = service.index_status().model_dump(mode="json")
        if status.get("state") != "ready":
            raise IndexNotReadyError("The video index is not ready.")
        if search_type == "actor":
            clusters = service.actor_clusters(page_size=100).clusters
            cluster = next(
                (
                    item
                    for item in clusters
                    if item.cluster_id == query
                ),
                None,
            )
            if cluster is None:
                return {"error": "No matching actor cluster was found."}
            job = _configured_jobs().submit_actor_overlay(
                CreateActorOverlayCommand(
                    cluster_id=query,
                    media_id=cluster.media_id,
                    generation_id=cluster.generation_id,
                )
            )
            artifact = _configured_jobs().wait(job.job_id).result
            if artifact is None:
                return {"error": "The actor artifact result is unavailable."}
            artifact = artifact.result
            return {
                "type": search_type,
                "query": query,
                "artifact_id": artifact.artifact_id,
            }

        result = service.search(
            SearchCommand(
                modality=search_type,
                query=query,
                top_k=1,
            )
        )
        if not result.hits:
            return {"error": f"No {search_type} match was found."}
        hit = result.hits[0]
        return {
            "type": search_type,
            "query": query,
            "timestamp": hit.start,
            "hit": hit.to_dict(),
            "media_id": hit.media_id,
        }
    except IndexNotReadyError as exc:
        return {"error": str(exc)}
    except ApplicationError as exc:
        return {"error": str(exc)}
    except Exception:
        LOGGER.exception("Unexpected search failure")
        return {"error": "Search failed. Check the application logs."}


def _render_search_result(result):
    if not result:
        return
    if error := result.get("error"):
        st.error(error)
        return

    service = _configured_service()
    try:
        resource = (
            service.open_artifact_content(result["artifact_id"])
            if result["type"] == "actor"
            else service.open_media_content(result["media_id"])
        )
    except ApplicationError:
        st.error("The search result video is no longer available.")
        return

    search_type = result["type"]
    if search_type == "actor":
        st.success(f"Actor cluster {result['query']}")
        st.video(
            str(resource.path),
            format=resource.mime_type,
            width="stretch",
        )
        return

    timestamp = result["timestamp"]
    st.success(f"Best {search_type} match: {timestamp:.3f} seconds")
    st.video(
        str(resource.path),
        start_time=timestamp,
        width="stretch",
    )


def _select_video(busy, media_id):
    service = _configured_service()
    st.subheader("Video")
    upload_slot = st.empty()
    has_session_upload = st.session_state.get("video_upload") is not None
    if busy and not has_session_upload and media_id is not None:
        uploaded_video = None
        st.caption("Indexing the registered video.")
    else:
        with upload_slot:
            uploaded_video = st.file_uploader(
                "Upload an MP4, MOV, or AVI video",
                type=["mp4", "mov", "avi"],
                disabled=busy,
                key="video_upload",
            )

    if uploaded_video is not None:
        st.video(uploaded_video, width=560)
    elif media_id is not None:
        try:
            resource = service.open_media_content(media_id)
        except ApplicationError:
            st.warning("The registered video is no longer available.")
        else:
            if not busy:
                st.caption("Using the registered video.")
            st.video(str(resource.path), width=560)
    return uploaded_video


def _search_controls(ready, uploaded_video, available_modalities):
    st.subheader("Search")
    if not ready:
        message = (
            "Index this uploaded video before searching it."
            if uploaded_video is not None
            else "Search becomes available after indexing completes."
        )
        st.caption(message)

    type_column, query_column = st.columns(
        [0.35, 0.65],
        gap="small",
        vertical_alignment="bottom",
    )
    with type_column:
        search_type = st.selectbox(
            "Search type",
            list(available_modalities),
            disabled=not ready,
        )
    with query_column:
        query = st.text_input(
            "Actor cluster ID" if search_type == "actor" else "Search query",
            placeholder=(
                "For example: 1"
                if search_type == "actor"
                else "For example: Chef makes pizza and cuts it up."
            ),
            disabled=not ready,
        )
    clicked = st.button("Search", disabled=not ready or not query.strip())
    return clicked, search_type, query


def run():
    service = _configured_service()
    st.set_page_config(page_title="VidXP", page_icon="🎬", layout="wide")
    st.title("VidXP")
    st.caption("Index and search video by dialogue, scene, and actor.")
    st.caption(f"Index repository: {service.layout.root}")

    jobs = _configured_jobs()
    job_id = st.session_state.get(INDEX_JOB_ID_KEY)
    current_job = _get_job(jobs, job_id)
    if job_id is not None and current_job is None:
        st.session_state.pop(INDEX_JOB_ID_KEY, None)
        job_id = None
    active = (
        current_job is not None
        and current_job.state in {JobState.queued, JobState.running}
    )
    if not active:
        st.session_state.pop(CANCEL_REQUESTED_KEY, None)
    requested = st.session_state.get(INDEX_REQUESTED_KEY, False)
    busy = active or requested
    status = service.index_status().model_dump(mode="json")
    media_id = st.session_state.get(MEDIA_ID_KEY)
    if media_id is None:
        indexed_media = (status.get("summary") or {}).get("media_ids") or ()
        if len(indexed_media) == 1:
            media_id = indexed_media[0]
            st.session_state[MEDIA_ID_KEY] = media_id
    installed_modalities = _available_index_modalities()
    video_column, workflow_column = st.columns(
        [0.95, 1.05],
        gap="large",
        vertical_alignment="top",
    )

    with video_column:
        uploaded_video = _select_video(busy, media_id)
    selected_media_id = (
        media_id
        if uploaded_video is None
        or st.session_state.get(UPLOAD_TOKEN_KEY)
        == _upload_token(uploaded_video)
        else None
    )

    with workflow_column:
        st.subheader("Build index")
        selected_modalities = tuple(
            st.multiselect(
                "Capabilities",
                installed_modalities,
                default=installed_modalities,
                disabled=busy,
                help="Install another capability extra to make it available here.",
            )
        )
        if not installed_modalities:
            st.warning(
                "No indexing capabilities are installed. "
                'Install one, for example: pip install "vidxp[scene]"'
            )
        st.button(
            "Index video",
            type="primary",
            disabled=busy
            or not selected_modalities
            or (uploaded_video is None and media_id is None),
            help=(
                "Indexing is already running."
                if busy
                else "Build or replace the index. First use may download model weights."
            ),
            on_click=_request_indexing,
        )
        if active:
            st.button(
                "Cancel indexing",
                on_click=_request_cancellation,
                disabled=st.session_state.get(CANCEL_REQUESTED_KEY, False),
            )
            if st.session_state.get(CANCEL_REQUESTED_KEY, False):
                st.caption(
                    "Cancellation requested. The current batch will finish "
                    "before indexing stops."
                )

        if requested:
            st.markdown("⏳ Starting indexing...")
        elif active:

            @st.fragment(run_every="1s")
            def poll_index_status():
                latest_job = _get_job(jobs, job_id)
                if latest_job is None:
                    st.session_state.pop(INDEX_JOB_ID_KEY, None)
                    st.error("The background indexing job is unavailable.")
                    st.rerun()
                    return
                latest_active = latest_job.state in {
                    JobState.queued,
                    JobState.running,
                }
                if not latest_active:
                    if latest_job.state == JobState.succeeded:
                        st.session_state.pop(INDEX_ERROR_KEY, None)
                    elif latest_job.error is not None:
                        st.session_state[INDEX_ERROR_KEY] = (
                            latest_job.error.message
                        )
                    elif latest_job.state == JobState.cancelled:
                        st.session_state[INDEX_ERROR_KEY] = (
                            "Indexing was cancelled."
                        )
                    else:
                        st.session_state[INDEX_ERROR_KEY] = (
                            "Indexing did not complete successfully."
                        )
                    st.session_state.pop(INDEX_JOB_ID_KEY, None)
                    st.rerun()
                    return
                latest_status = (
                    latest_job.progress.model_dump(mode="json")
                    if latest_job.progress is not None
                    else {
                        "state": "indexing",
                        "stage": "queued",
                        "message": "Indexing is queued.",
                    }
                )
                _render_index_status(
                    latest_status,
                    True,
                    selected_media_id,
                    st.session_state.get(INDEX_ERROR_KEY),
                )

            poll_index_status()
        else:
            _render_index_status(
                status,
                False,
                selected_media_id,
                st.session_state.get(INDEX_ERROR_KEY),
            )

        ready = not busy and _is_search_ready(status, selected_media_id)
        configured_modalities = (
            (status.get("summary") or {}).get(
                "modalities",
                ("scene", "dialogue", "actor"),
            )
            if ready
            else ("scene", "dialogue", "actor")
        )
        available_modalities = tuple(
            modality
            for modality in ("scene", "dialogue", "actor")
            if modality in configured_modalities
        )
        search_clicked, search_type, query = _search_controls(
            ready,
            uploaded_video,
            available_modalities,
        )
        if search_clicked:
            st.session_state[SEARCH_RESULT_KEY] = _run_search(search_type, query)
        _render_search_result(st.session_state.get(SEARCH_RESULT_KEY))

    if requested:
        _run_indexing(uploaded_video, status, selected_modalities)


def main(
    arguments: Sequence[str] = (),
    settings: VidXPSettings | None = None,
):
    from streamlit.web import cli as streamlit_cli

    application_arguments = []
    if settings is not None:
        application_arguments = [
            "--",
            "--vidxp-settings-json",
            LocalExecutionSettings.from_settings(settings).model_dump_json(),
        ]
    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        *arguments,
        *application_arguments,
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    run()
