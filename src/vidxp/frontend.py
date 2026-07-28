import argparse
import hashlib
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import streamlit as st

from vidxp.application import VidXPApplication
from vidxp.application_models import (
    ApplicationError,
    DependencyCheckCommand,
    SearchCommand,
)
from vidxp.composition import create_application
from vidxp.index_state import IndexNotReadyError
from vidxp.index_worker import (
    cancel_indexing,
    indexing_in_progress,
    start_indexing,
)
from vidxp.settings import VidXPSettings


LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _configured_service(
    settings: VidXPSettings | None = None,
) -> VidXPApplication:
    return create_application(settings or _settings_from_arguments())


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
    return VidXPSettings.model_validate_json(parsed.vidxp_settings_json)


INDEX_REQUESTED_KEY = "_vidxp_index_requested"
INDEX_ERROR_KEY = "_vidxp_index_error"
SEARCH_RESULT_KEY = "_vidxp_search_result"
CANCEL_REQUESTED_KEY = "_vidxp_cancel_requested"


def _video_hash(uploaded_video) -> str | None:
    if uploaded_video is None:
        return None
    return hashlib.sha256(uploaded_video.getvalue()).hexdigest()


def _is_search_ready(status, uploaded_video) -> bool:
    saved_video_path = _configured_service().layout.media / "source-video.mp4"
    if not status or status.get("state") != "ready":
        return False
    if uploaded_video is None:
        return saved_video_path.is_file()
    return status.get("video", {}).get("sha256") == _video_hash(uploaded_video)


def _render_summary(summary):
    if not summary:
        return
    st.caption(
        " · ".join(
            (
                f"Language: {summary.get('language', '—')}",
                f"Dialogue phrases: {summary.get('dialogue_phrases', 0):,}",
                f"Scene frames: {summary.get('scene_frames', 0):,}",
                f"Actor clusters: {summary.get('actor_clusters', 0):,}",
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


def _render_index_status(status, active, uploaded_video, request_error=None):
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
        if _is_search_ready(status, uploaded_video):
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
    if cancel_indexing():
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
    saved_video_path = service.layout.media / "source-video.mp4"
    try:
        if uploaded_video is not None:
            saved_video_path.parent.mkdir(parents=True, exist_ok=True)
            saved_video_path.write_bytes(uploaded_video.getvalue())
            source_name = uploaded_video.name
        else:
            source_name = (
                status.get("video", {}).get("source_name", saved_video_path.name)
                if status
                else saved_video_path.name
            )
        start_indexing(
            str(saved_video_path),
            source_name,
            service,
            modalities=modalities,
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
        st.session_state[INDEX_REQUESTED_KEY] = False
    st.rerun()


def _run_search(search_type, query):
    service = _configured_service()
    saved_video_path = service.layout.media / "source-video.mp4"
    actor_output_path = service.layout.artifacts / "actor-result.mp4"
    try:
        status = service.index_status().model_dump(mode="json")
        if status.get("state") != "ready":
            raise IndexNotReadyError("The video index is not ready.")
        if search_type == "actor":
            actor_output_path.unlink(missing_ok=True)
            service.render_actor(
                query,
                saved_video_path,
                actor_output_path,
            )
            if (
                not actor_output_path.is_file()
                or actor_output_path.stat().st_size == 0
            ):
                return {"error": "Actor result video could not be generated."}
            return {
                "type": search_type,
                "query": query,
                "video_path": str(actor_output_path),
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
            "video_path": str(saved_video_path),
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

    video_path = Path(result["video_path"])
    if not video_path.is_file():
        st.error("The search result video is no longer available.")
        return

    search_type = result["type"]
    if search_type == "actor":
        st.success(f"Actor cluster {result['query']}")
        st.video(str(video_path), format="video/mp4", width="stretch")
        return

    timestamp = result["timestamp"]
    st.success(f"Best {search_type} match: {timestamp:.3f} seconds")
    st.video(
        str(video_path),
        start_time=timestamp,
        width="stretch",
    )


def _select_video(busy):
    saved_video_path = _configured_service().layout.media / "source-video.mp4"
    st.subheader("Video")
    upload_slot = st.empty()
    has_session_upload = st.session_state.get("video_upload") is not None
    if busy and not has_session_upload and saved_video_path.is_file():
        uploaded_video = None
        st.caption("Indexing the saved video.")
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
    elif saved_video_path.is_file():
        if not busy:
            st.caption("Using the saved video.")
        st.video(str(saved_video_path), width=560)
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
    saved_video_path = service.layout.media / "source-video.mp4"
    st.set_page_config(page_title="VidXP", page_icon="🎬", layout="wide")
    st.title("VidXP")
    st.caption("Index and search video by dialogue, scene, and actor.")
    st.caption(f"Index repository: {service.layout.root}")

    active = indexing_in_progress(service)
    if not active:
        st.session_state.pop(CANCEL_REQUESTED_KEY, None)
    requested = st.session_state.get(INDEX_REQUESTED_KEY, False)
    busy = active or requested
    status = service.index_status().model_dump(mode="json")
    installed_modalities = _available_index_modalities()
    video_column, workflow_column = st.columns(
        [0.95, 1.05],
        gap="large",
        vertical_alignment="top",
    )

    with video_column:
        uploaded_video = _select_video(busy)

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
            or (uploaded_video is None and not saved_video_path.is_file()),
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
                latest_active = indexing_in_progress(service)
                _render_index_status(
                    service.index_status().model_dump(mode="json"),
                    latest_active,
                    uploaded_video,
                    st.session_state.get(INDEX_ERROR_KEY),
                )
                if not latest_active:
                    st.rerun()

            poll_index_status()
        else:
            _render_index_status(
                status,
                False,
                uploaded_video,
                st.session_state.get(INDEX_ERROR_KEY),
            )

        ready = not busy and _is_search_ready(status, uploaded_video)
        configured_modalities = (
            (status.get("summary", {}).get("configuration") or {}).get(
                "enabled_modalities",
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
            settings.model_dump_json(),
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
