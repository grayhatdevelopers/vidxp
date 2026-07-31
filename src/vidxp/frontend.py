import argparse
import logging
import shutil
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import streamlit as st

from vidxp.app_paths import available_storage_bytes
from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ApplicationError,
    CreateActorOverlayCommand,
    CreateIndexCommand,
    DependencyCheckCommand,
    ErrorCategory,
    ImportMediaCommand,
    JobState,
    ListMediaCommand,
    PrepareModelsCommand,
    QueryVideoCommand,
    SearchCommand,
)
from vidxp.branding import PROJECT_URL, icon_path
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
    active_settings = settings or _settings_from_arguments()
    return create_job_service(
        active_settings,
        index_preflight=_configured_service(active_settings).preflight_index,
    )


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
PREPARE_JOB_ID_KEY = "_vidxp_prepare_job_id"
PREPARE_CANCEL_REQUESTED_KEY = "_vidxp_prepare_cancel_requested"
SEARCH_RESULT_KEY = "_vidxp_search_result"
CANCEL_REQUESTED_KEY = "_vidxp_cancel_requested"
MEDIA_ID_KEY = "_vidxp_media_id"
UPLOAD_TOKEN_KEY = "_vidxp_upload_token"
MEDIA_NOTICE_KEY = "_vidxp_media_notice"
INDEX_JOB_QUERY_PARAM = "index_job"
PREPARE_JOB_QUERY_PARAM = "prepare_job"
SEARCH_JOB_QUERY_PARAM = "search_job"
SEARCH_TYPE_QUERY_PARAM = "search_type"
SCENE_SAMPLE_FPS_DEFAULT = 1.0
SCENE_DETAIL_PRESETS = (0.5, SCENE_SAMPLE_FPS_DEFAULT, 2.0)
SCENE_DETAIL_LABELS = {
    0.5: "Faster — every 2 seconds",
    1.0: "Balanced — every second",
    2.0: "Detailed — twice per second",
}


def _format_bytes(size: int) -> str:
    gib = 1024**3
    mib = 1024**2
    if size >= gib:
        return f"{size / gib:.2f} GiB"
    return f"{size / mib:.1f} MiB"


def _remember_job(
    *,
    job_id: str,
    query_param: str,
    search_type: str | None = None,
) -> None:
    st.query_params[query_param] = job_id
    if search_type is not None:
        st.query_params[SEARCH_TYPE_QUERY_PARAM] = search_type


def _forget_job(query_param: str) -> None:
    st.query_params.pop(query_param, None)
    if query_param == SEARCH_JOB_QUERY_PARAM:
        st.query_params.pop(SEARCH_TYPE_QUERY_PARAM, None)


def _restore_durable_jobs() -> None:
    if INDEX_JOB_ID_KEY not in st.session_state:
        if job_id := st.query_params.get(INDEX_JOB_QUERY_PARAM):
            st.session_state[INDEX_JOB_ID_KEY] = str(job_id)
    if PREPARE_JOB_ID_KEY not in st.session_state:
        if job_id := st.query_params.get(PREPARE_JOB_QUERY_PARAM):
            st.session_state[PREPARE_JOB_ID_KEY] = str(job_id)
    if SEARCH_RESULT_KEY not in st.session_state:
        if job_id := st.query_params.get(SEARCH_JOB_QUERY_PARAM):
            st.session_state[SEARCH_RESULT_KEY] = {
                "type": str(
                    st.query_params.get(SEARCH_TYPE_QUERY_PARAM) or "search"
                ),
                "query": "",
                "job_id": str(job_id),
            }


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
        if event.get("stage") == "downloading_model":
            text = f"{_format_bytes(current)} of {_format_bytes(total)}"
        else:
            text = f"{current:,} of {total:,}"
        st.progress(
            min(current / total, 1.0),
            text=text,
        )


def _get_job(jobs: JobService, job_id: str | None):
    if job_id is None:
        return None
    try:
        return jobs.get(job_id)
    except ApplicationError as exc:
        if exc.category == ErrorCategory.not_found:
            LOGGER.info("Background job %s no longer exists.", job_id)
            return None
        LOGGER.warning(
            "Could not query background job %s: %s",
            job_id,
            exc.code,
        )
        raise


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
        st.caption("Prepare the selected models, then start indexing.")
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


def _request_prepare_cancellation():
    job_id = st.session_state.get(PREPARE_JOB_ID_KEY)
    if job_id is not None:
        _configured_jobs().cancel(job_id)
        st.session_state[PREPARE_CANCEL_REQUESTED_KEY] = True


def _available_index_modalities() -> tuple[str, ...]:
    service = _configured_service()
    return tuple(
        capability.name
        for capability in service.list_capabilities()
        if capability.supports_indexing
        if service.check_dependencies(
            DependencyCheckCommand(
                modalities=(capability.name,),
                include_runtime_checks=False,
            )
        ).ok
    )


def _available_query_modalities(
    configured: tuple[str, ...],
) -> tuple[str, ...]:
    service = _configured_service()

    def supports_query(capability_name: str) -> bool:
        capability = service.get_capability(capability_name)
        operations = {
            operation.name for operation in capability.operations
        }
        return (
            "search" in operations
            or {"clusters", "detections"}.issubset(operations)
        )

    return tuple(
        capability.name
        for capability in service.list_capabilities()
        if capability.name in configured
        if supports_query(capability.name)
    )


def _finish_index_job(job) -> None:
    _forget_job(INDEX_JOB_QUERY_PARAM)
    if job.state == JobState.succeeded:
        st.session_state.pop(INDEX_ERROR_KEY, None)
    elif job.error is not None:
        st.session_state[INDEX_ERROR_KEY] = job.error.message
    elif job.state == JobState.cancelled:
        st.session_state[INDEX_ERROR_KEY] = "Indexing was cancelled."
    else:
        st.session_state[INDEX_ERROR_KEY] = (
            "Indexing did not complete successfully."
        )
    st.session_state.pop(INDEX_JOB_ID_KEY, None)


def _run_indexing(
    uploaded_video,
    status,
    modalities,
    *,
    scene_sample_fps: float | None = None,
):
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
                scene_sample_fps=scene_sample_fps,
            )
        )
        st.session_state[INDEX_JOB_ID_KEY] = job.job_id
        _remember_job(
            job_id=job.job_id,
            query_param=INDEX_JOB_QUERY_PARAM,
        )
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


def _scene_sample_fps_control(
    modalities: tuple[str, ...],
    *,
    disabled: bool,
) -> float | None:
    if "scene" not in modalities:
        return None
    return float(
        st.selectbox(
            "Scene detail",
            SCENE_DETAIL_PRESETS,
            index=1,
            format_func=SCENE_DETAIL_LABELS.__getitem__,
            disabled=disabled,
            help=(
                "More scene detail can improve coverage but takes longer "
                "to index and uses more storage."
            ),
        )
    )


def _run_search(search_type, query, media_id=None):
    service = _configured_service()
    try:
        status = service.index_status().model_dump(mode="json")
        if status.get("state") != "ready":
            raise IndexNotReadyError("The video index is not ready.")
        if search_type == "actor":
            job = _configured_jobs().submit_actor_overlay(
                CreateActorOverlayCommand(cluster_id=query)
            )
            _remember_job(
                job_id=job.job_id,
                query_param=SEARCH_JOB_QUERY_PARAM,
                search_type=search_type,
            )
            return {
                "type": search_type,
                "query": query,
                "job_id": job.job_id,
            }

        if search_type == "natural-language":
            job = _configured_jobs().submit_query(
                QueryVideoCommand(
                    question=query,
                    media_id=media_id,
                    top_k=10,
                )
            )
        else:
            job = _configured_jobs().submit_search(
                SearchCommand(
                    modalities=(search_type,),
                    query=query,
                    media_id=media_id,
                    top_k=1,
                )
            )
        _remember_job(
            job_id=job.job_id,
            query_param=SEARCH_JOB_QUERY_PARAM,
            search_type=search_type,
        )
        return {
            "type": search_type,
            "query": query,
            "job_id": job.job_id,
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

    if "job_id" in result:

        @st.fragment(run_every="1s")
        def poll_search_job():
            label = (
                "actor overlay"
                if result["type"] == "actor"
                else "natural-language query"
                if result["type"] == "natural-language"
                else f"{result['type']} search"
            )
            try:
                job = _get_job(_configured_jobs(), result["job_id"])
            except ApplicationError as exc:
                message = (
                    f"The {label} status is temporarily unavailable. Retrying."
                    if exc.retryable
                    else f"The {label} status could not be read: {exc}"
                )
                st.warning(message)
                return
            if job is None:
                _forget_job(SEARCH_JOB_QUERY_PARAM)
                st.session_state[SEARCH_RESULT_KEY] = {
                    "type": result["type"],
                    "query": result["query"],
                    "error": f"The {label} job is unavailable.",
                }
                st.rerun()
                return
            if job.state in {JobState.queued, JobState.running}:
                if job.progress is not None:
                    _render_progress(job.progress.model_dump(mode="json"))
                else:
                    action = (
                        "Starting"
                        if job.state == JobState.queued
                        else "Running"
                    )
                    st.markdown(f"⏳ {action} {label}...")
                return
            if job.state != JobState.succeeded or job.result is None:
                _forget_job(SEARCH_JOB_QUERY_PARAM)
                message = {
                    JobState.cancelled: f"The {label} was cancelled.",
                }.get(
                    job.state,
                    (
                        job.error.message
                        if job.error is not None
                        else f"The {label} did not complete successfully."
                    ),
                )
                st.session_state[SEARCH_RESULT_KEY] = {
                    "type": result["type"],
                    "query": result["query"],
                    "error": message,
                }
                st.rerun()
                return
            completed = job.result.result
            _forget_job(SEARCH_JOB_QUERY_PARAM)
            if result["type"] == "actor":
                resolved = {
                    "type": "actor",
                    "query": result["query"],
                    "artifact_id": completed.artifact_id,
                }
            elif result["type"] == "natural-language":
                first_moment = (
                    completed.moments[0] if completed.moments else None
                )
                first_evidence = (
                    completed.evidence[0] if completed.evidence else None
                )
                resolved = {
                    "type": result["type"],
                    "query": completed.question,
                    "answer": completed.model_dump(mode="json"),
                    "media_id": (
                        first_moment.media_id
                        if first_moment is not None
                        else getattr(first_evidence, "media_id", None)
                    ),
                    "timestamp": (
                        first_moment.start
                        if first_moment is not None
                        else getattr(first_evidence, "start", 0)
                    ),
                }
            elif not completed.moments:
                resolved = {
                    "type": result["type"],
                    "query": completed.query,
                    "error": f"No {result['type']} match was found.",
                }
            else:
                moment = completed.moments[0]
                resolved = {
                    "type": result["type"],
                    "query": completed.query,
                    "timestamp": moment.start,
                    "moment": moment.model_dump(mode="json"),
                    "media_id": moment.media_id,
                }
            st.session_state[SEARCH_RESULT_KEY] = resolved
            st.rerun()

        poll_search_job()
        return

    if result["type"] == "natural-language":
        answer = result["answer"]
        if answer["claims"]:
            for claim in answer["claims"]:
                st.markdown(f"- {claim['text']}")
                st.caption(
                    "Evidence: " + ", ".join(claim["evidence_ids"])
                )
        elif answer["evidence"]:
            st.info(
                "The query returned evidence without generating unsupported "
                "claims."
            )
        else:
            st.info("No supporting evidence was found.")
        if answer.get("fallback_reason"):
            st.caption(f"Fallback: {answer['fallback_reason']}")
        if result.get("media_id") is None:
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
        st.success(
            (
                f"Actor cluster {result['query']}"
                if result.get("query")
                else "Actor overlay"
            )
        )
        st.video(
            str(resource.path),
            format=resource.mime_type,
            width="stretch",
        )
        return

    timestamp = result["timestamp"]
    result_label = (
        "Closest sampled scene"
        if search_type == "scene"
        else "Closest supporting evidence"
        if search_type == "natural-language"
        else f"Closest {search_type} match"
    )
    st.success(f"{result_label}: {timestamp:.1f} seconds")
    if search_type == "scene":
        st.caption(
            "Scene search ranks sampled frames by visual similarity. "
            "It does not identify the first occurrence and is not reliable "
            "for counting people."
        )
    st.video(
        str(resource.path),
        start_time=timestamp,
        width="stretch",
    )


def _default_media_id(media_id, assets):
    available_ids = {asset.media_id for asset in assets}
    if media_id in available_ids:
        return media_id
    if len(assets) == 1:
        return assets[0].media_id
    return None


def _import_local_video(service, raw_path):
    return service.import_media(
        ImportMediaCommand(path=Path(raw_path.strip()).expanduser())
    )


def _select_video(busy, media_id, media_page):
    service = _configured_service()
    st.subheader("Video")
    assets = tuple(media_page.items) if media_page is not None else ()
    media_id = _default_media_id(media_id, assets)
    if media_id is not None:
        st.session_state[MEDIA_ID_KEY] = media_id
    else:
        st.session_state.pop(MEDIA_ID_KEY, None)

    if assets:
        asset_by_id = {asset.media_id: asset for asset in assets}
        selected_media_id = st.selectbox(
            "Registered video",
            tuple(asset_by_id),
            index=(
                tuple(asset_by_id).index(media_id)
                if media_id is not None
                else 0
            ),
            format_func=lambda value: (
                f"{asset_by_id[value].original_filename} "
                f"({asset_by_id[value].duration_seconds:.1f}s)"
            ),
            disabled=busy,
        )
        if selected_media_id != media_id:
            st.session_state.pop(SEARCH_RESULT_KEY, None)
        media_id = selected_media_id
        st.session_state[MEDIA_ID_KEY] = media_id
        if media_page.next_cursor is not None:
            st.caption(
                "Showing the first 100 registered videos. "
                "Use the CLI or API to inspect the full catalog."
            )

    with st.expander("Import a large local video"):
        st.caption(
            "Use a local path to avoid sending large files through the "
            "browser uploader."
        )
        local_path = st.text_input(
            "Local video path",
            disabled=busy,
            placeholder="C:\\Videos\\example.mp4 or /Users/me/Videos/example.mp4",
        )
        if st.button(
            "Register local video",
            disabled=busy or not local_path.strip(),
        ):
            try:
                imported = _import_local_video(service, local_path)
            except ApplicationError as exc:
                st.error(str(exc))
            else:
                st.session_state[MEDIA_ID_KEY] = imported.media_id
                st.session_state.pop(SEARCH_RESULT_KEY, None)
                st.session_state[MEDIA_NOTICE_KEY] = (
                    f"Registered {imported.original_filename}."
                )
                st.rerun()

    uploaded_video = st.file_uploader(
        "Upload an MP4, MOV, or AVI video",
        type=["mp4", "mov", "avi"],
        disabled=busy,
        key="video_upload",
    )
    if busy and uploaded_video is None and media_id is not None:
        st.caption("Indexing the registered video.")

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
    return uploaded_video, media_id


def _search_controls(ready, uploaded_video, available_modalities):
    st.subheader("Search")
    if not ready:
        message = (
            "Index this uploaded video before searching it."
            if uploaded_video is not None
            else "Search becomes available after indexing completes."
        )
        st.caption(message)

    with st.form(
        "video_search",
        clear_on_submit=False,
        enter_to_submit=True,
        border=False,
    ):
        type_column, query_column = st.columns(
            [0.35, 0.65],
            gap="small",
            vertical_alignment="bottom",
        )
        with type_column:
            search_type = st.selectbox(
                "Search type",
                ["natural-language", *available_modalities],
                disabled=not ready,
            )
        with query_column:
            query = st.text_input(
                (
                    "Actor cluster ID"
                    if search_type == "actor"
                    else "Question"
                    if search_type == "natural-language"
                    else "Search query"
                ),
                placeholder=(
                    "For example: 1"
                    if search_type == "actor"
                    else "For example: What happens after the taxi arrives?"
                    if search_type == "natural-language"
                    else "For example: Chef makes pizza and cuts it up."
                ),
                disabled=not ready,
                key="video_search_query",
            )
        clicked = st.form_submit_button(
            "Search",
            disabled=not ready,
        )
    if clicked and not query.strip():
        st.warning("Enter a search query.")
        clicked = False
    return clicked, search_type, query


def run():
    application_icon = icon_path()
    st.set_page_config(
        page_title="VidXP",
        page_icon=application_icon,
        layout="wide",
    )
    st.logo(application_icon, size="large", link=PROJECT_URL)
    service = _configured_service()
    st.title("VidXP")
    st.caption("Index and search video by dialogue, scene, and actor.")
    st.caption(f"Index repository: {service.layout.root}")
    if notice := st.session_state.pop(MEDIA_NOTICE_KEY, None):
        st.success(notice)

    jobs = _configured_jobs()
    _restore_durable_jobs()
    job_id = st.session_state.get(INDEX_JOB_ID_KEY)
    job_lookup_error = None
    try:
        current_job = _get_job(jobs, job_id)
    except ApplicationError as exc:
        current_job = None
        job_lookup_error = exc
    if job_id is not None and current_job is None:
        if job_lookup_error is None:
            st.session_state.pop(INDEX_JOB_ID_KEY, None)
            _forget_job(INDEX_JOB_QUERY_PARAM)
            job_id = None
    elif (
        current_job is not None
        and current_job.state not in {JobState.queued, JobState.running}
    ):
        _finish_index_job(current_job)
        job_id = None
    active = (
        job_id is not None
        and (
            job_lookup_error is not None
            or (
                current_job is not None
                and current_job.state in {JobState.queued, JobState.running}
            )
        )
    )
    prepare_job_id = st.session_state.get(PREPARE_JOB_ID_KEY)
    prepare_error = None
    try:
        prepare_job = _get_job(jobs, prepare_job_id)
    except ApplicationError as exc:
        prepare_job = None
        prepare_error = exc
    if prepare_job_id is not None and prepare_job is None:
        if prepare_error is None:
            st.session_state.pop(PREPARE_JOB_ID_KEY, None)
            _forget_job(PREPARE_JOB_QUERY_PARAM)
            prepare_job_id = None
    elif (
        prepare_job is not None
        and prepare_job.state not in {JobState.queued, JobState.running}
    ):
        st.session_state.pop(PREPARE_JOB_ID_KEY, None)
        _forget_job(PREPARE_JOB_QUERY_PARAM)
        prepare_job_id = None
        if prepare_job.state == JobState.succeeded:
            st.session_state[MEDIA_NOTICE_KEY] = (
                "Selected model artifacts are prepared."
            )
        elif prepare_job.error is not None:
            prepare_error = ApplicationError(
                prepare_job.error.code,
                prepare_job.error.category,
                prepare_job.error.message,
                details=prepare_job.error.details,
                retryable=prepare_job.error.retryable,
            )
    preparing = (
        prepare_job_id is not None
        and (
            prepare_error is not None
            or (
                prepare_job is not None
                and prepare_job.state in {JobState.queued, JobState.running}
            )
        )
    )
    if not preparing:
        st.session_state.pop(PREPARE_CANCEL_REQUESTED_KEY, None)
    if not active:
        st.session_state.pop(CANCEL_REQUESTED_KEY, None)
    requested = st.session_state.get(INDEX_REQUESTED_KEY, False)
    busy = active or requested or preparing
    status = service.index_status().model_dump(mode="json")
    media_id = st.session_state.get(MEDIA_ID_KEY)
    if media_id is None:
        indexed_media = (status.get("summary") or {}).get("media_ids") or ()
        if len(indexed_media) == 1:
            media_id = indexed_media[0]
            st.session_state[MEDIA_ID_KEY] = media_id
    try:
        media_page = service.list_media(ListMediaCommand(page_size=100))
    except ApplicationError:
        media_page = None
        st.warning(
            "Registered videos are temporarily unavailable. "
            "The current index remains usable."
        )
    installed_modalities = _available_index_modalities()
    video_column, workflow_column = st.columns(
        [0.95, 1.05],
        gap="large",
        vertical_alignment="top",
    )

    with video_column:
        uploaded_video, media_id = _select_video(
            busy,
            media_id,
            media_page,
        )
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
        scene_sample_fps = _scene_sample_fps_control(
            selected_modalities,
            disabled=busy,
        )
        model_readiness = (
            service.model_readiness(selected_modalities)
            if selected_modalities
            else None
        )
        missing_checks = (
            tuple(
                check
                for check in model_readiness.checks
                if not check.ok
            )
            if model_readiness is not None
            else ()
        )
        missing_models = tuple(check.name for check in missing_checks)
        required_download_bytes = sum(
            check.download_size_bytes or 0
            for check in missing_checks
        )
        model_cache_free_bytes = (
            available_storage_bytes(service.model_cache)
            if missing_checks
            else None
        )
        insufficient_model_space = (
            model_cache_free_bytes is not None
            and model_cache_free_bytes < required_download_bytes
        )
        if not installed_modalities:
            st.warning(
                "No indexing capabilities are installed. "
                'Install one, for example: pip install "vidxp[scene]"'
            )
        if missing_models and not preparing:
            st.warning(
                "The following model artifacts must be downloaded:"
            )
            for check in missing_checks:
                st.caption(
                    f"• {check.name} — "
                    f"{_format_bytes(check.download_size_bytes or 0)}"
                )
            st.caption(
                "Maximum additional download and cache space: "
                f"{_format_bytes(required_download_bytes)}"
            )
            st.caption(f"Model cache: {service.model_cache}")
            if model_cache_free_bytes is not None:
                st.caption(
                    "Free space at model cache: "
                    f"{_format_bytes(model_cache_free_bytes)}"
                )
            if insufficient_model_space:
                st.error(
                    "There is not enough free space for these model "
                    "downloads. Free space at the displayed location or "
                    "restart VidXP with a different global --data-dir."
                )
            confirmed = st.checkbox(
                "I want to download these models and use this cache space.",
                key=(
                    "_vidxp_confirm_models_"
                    + "_".join(selected_modalities)
                ),
                disabled=insufficient_model_space,
            )
            if st.button(
                "Download models",
                type="primary",
                disabled=busy
                or insufficient_model_space
                or not confirmed,
            ):
                preparation = jobs.submit_prepare_models(
                    PrepareModelsCommand(modalities=selected_modalities)
                )
                st.session_state[PREPARE_JOB_ID_KEY] = preparation.job_id
                _remember_job(
                    job_id=preparation.job_id,
                    query_param=PREPARE_JOB_QUERY_PARAM,
                )
                st.rerun()
        if preparing:
            st.button(
                "Cancel model preparation",
                on_click=_request_prepare_cancellation,
                disabled=st.session_state.get(
                    PREPARE_CANCEL_REQUESTED_KEY,
                    False,
                ),
            )
            if st.session_state.get(PREPARE_CANCEL_REQUESTED_KEY, False):
                st.caption("Cancellation requested.")
        if preparing:

            @st.fragment(run_every="1s")
            def poll_model_preparation():
                try:
                    latest = _get_job(jobs, prepare_job_id)
                except ApplicationError as exc:
                    st.warning(
                        "Model preparation status is temporarily unavailable. "
                        f"Retrying: {exc}"
                    )
                    return
                if latest is None:
                    st.error("The model preparation job is unavailable.")
                    return
                if latest.state not in {JobState.queued, JobState.running}:
                    st.rerun()
                    return
                if latest.progress is not None:
                    _render_progress(
                        latest.progress.model_dump(mode="json")
                    )
                else:
                    st.markdown("⏳ Model preparation is queued.")

            poll_model_preparation()
        elif prepare_error is not None:
            st.error(f"Model preparation failed: {prepare_error}")
        st.button(
            "Index video",
            type="primary",
            disabled=busy
            or not selected_modalities
            or bool(missing_models)
            or (uploaded_video is None and media_id is None),
            help=(
                "Indexing is already running."
                if active or requested
                else "Model preparation is running."
                if preparing
                else "Prepare the selected models before indexing."
                if missing_models
                else "Build or replace the index."
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
                try:
                    latest_job = _get_job(jobs, job_id)
                except ApplicationError as exc:
                    message = (
                        "The indexing job status is temporarily unavailable. "
                        "Retrying."
                        if exc.retryable
                        else f"The indexing job status could not be read: {exc}"
                    )
                    st.warning(message)
                    return
                if latest_job is None:
                    st.session_state.pop(INDEX_JOB_ID_KEY, None)
                    _forget_job(INDEX_JOB_QUERY_PARAM)
                    st.error("The background indexing job is unavailable.")
                    st.rerun()
                    return
                latest_active = latest_job.state in {
                    JobState.queued,
                    JobState.running,
                }
                if not latest_active:
                    _finish_index_job(latest_job)
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
                (),
            )
            if ready
            else ()
        )
        available_modalities = _available_query_modalities(
            tuple(configured_modalities)
        )
        search_clicked, search_type, query = _search_controls(
            ready,
            uploaded_video,
            available_modalities,
        )
        if search_clicked:
            st.session_state[SEARCH_RESULT_KEY] = _run_search(
                search_type,
                query,
                selected_media_id,
            )
        _render_search_result(st.session_state.get(SEARCH_RESULT_KEY))

    if requested:
        _run_indexing(
            uploaded_video,
            status,
            selected_modalities,
            scene_sample_fps=scene_sample_fps,
        )


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
