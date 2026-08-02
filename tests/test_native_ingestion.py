from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import socket
import subprocess
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
import uvicorn

from vidxp.api import create_app
from vidxp.app_paths import default_model_directory
from vidxp.application_models import Principal
from vidxp.composition import (
    create_control_plane_application,
    create_http_application,
)
from vidxp.mcp import create_mcp_server
from vidxp.settings import VidXPSettings


def _video(path: Path, color: str) -> None:
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("FFmpeg is required for native ingestion integration tests.")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=64x64:d=0.8:r=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            "-y",
            str(path),
        ],
        check=True,
    )


def test_stdio_ingests_multiple_paths_without_mcp_media_bytes(
    tmp_path: Path,
) -> None:
    asyncio.run(_stdio_ingestion_scenario(tmp_path))


async def _stdio_ingestion_scenario(tmp_path: Path) -> None:
    first = tmp_path / "first" / "same.mp4"
    second = tmp_path / "second" / "same.mp4"
    missing = tmp_path / "missing.mp4"
    _video(first, "red")
    _video(second, "blue")
    settings = VidXPSettings(
        data_dir=tmp_path / "data",
        repository_root=tmp_path / "repository",
        runtime_backend="cpu",
        model_cache=default_model_directory(),
        trusted_local_import_roots=(tmp_path,),
        workflow_poll_interval_seconds=0.05,
    )
    context = create_control_plane_application(settings)
    try:
        server = create_mcp_server(
            context,
            default_principal=Principal(
                subject="local",
                client_id="stdio",
                scopes=frozenset({"*"}),
            ),
            filesystem_accessible=True,
        )
        async with Client(server) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert "ingest_local_media" in tools
            assert "get_media_ingestion" in tools
            assert "create_media_upload" not in tools
            schema = str(tools["ingest_local_media"].input_schema).lower()
            assert "base64" not in schema
            assert "chunk" not in schema
            arguments = {
                "command": {
                    "paths": [str(first), str(second), str(missing)],
                    "index_after_import": False,
                },
                "idempotency_key": "local-three-files-0001",
            }
            submitted = await client.call_tool("ingest_local_media", arguments)
            assert not submitted.is_error
            assert "get_media_ingestion" in submitted.structured_content["next_action"]
            assert "get_media_upload" not in submitted.structured_content["next_action"]
            ingestion_id = submitted.structured_content["session_id"]
            for _attempt in range(300):
                current = await client.call_tool(
                    "get_media_ingestion",
                    {"ingestion_id": ingestion_id},
                )
                assert not current.is_error
                items = current.structured_content["items"]
                phases = [item["phase"] for item in items]
                if all(phase in {"registered", "failed"} for phase in phases):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Local ingestion did not reach terminal states.")

            assert phases.count("registered") == 2
            assert phases.count("failed") == 1
            failed = next(item for item in items if item["phase"] == "failed")
            assert failed["original_filename"] == missing.name
            assert failed["client_file_key"] == "local-03"
            successful = [item for item in items if item["phase"] == "registered"]
            assert len({item["media_id"] for item in successful}) == 2
            assert all(item["import_job_id"] for item in successful)
            assert all(item["index_job_id"] is None for item in successful)
            assert all("path" not in item for item in items)
            replay = await client.call_tool("ingest_local_media", arguments)
            assert replay.structured_content["session_id"] == ingestion_id
            reordered = await client.call_tool(
                "ingest_local_media",
                {
                    "command": {
                        "paths": [str(second), str(first), str(missing)],
                        "index_after_import": False,
                    },
                    "idempotency_key": "local-three-files-0001",
                },
            )
            assert reordered.is_error
            assert '"code":"idempotency_key_reused"' in reordered.content[0].text
            media = await client.call_tool("list_media", {})
            assert media.structured_content["total"] == 2
    finally:
        context.close()

    restarted = create_control_plane_application(settings)
    try:
        server = create_mcp_server(
            restarted,
            default_principal=Principal(
                subject="local",
                client_id="stdio",
                scopes=frozenset({"*"}),
            ),
            filesystem_accessible=True,
        )
        async with Client(server) as client:
            recovered = await client.call_tool(
                "get_media_ingestion",
                {"ingestion_id": ingestion_id},
            )
            assert not recovered.is_error
            recovered_phases = [
                item["phase"] for item in recovered.structured_content["items"]
            ]
            assert recovered_phases.count("registered") == 2
            assert recovered_phases.count("failed") == 1
            media = await client.call_tool("list_media", {})
            assert media.structured_content["total"] == 2
    finally:
        restarted.close()


def test_native_http_browser_session_uses_bounded_multipart(
    tmp_path: Path,
) -> None:
    first = tmp_path / "red.mp4"
    second = tmp_path / "blue.mp4"
    _video(first, "red")
    _video(second, "blue")
    settings = VidXPSettings(
        data_dir=tmp_path / "data",
        repository_root=tmp_path / "repository",
        runtime_backend="cpu",
        upload_handoff_public_url="https://testserver/upload-handoff",
        upload_handoff_secret="h" * 32,
        http_auth_mode="static",
        http_static_bearer_token="s" * 32,
        http_trusted_hosts=("testserver",),
        http_max_json_body_bytes=1024,
        workflow_poll_interval_seconds=0.05,
    )
    context = create_http_application(settings)
    try:
        assert context.uploads is not None
        link = context.uploads.create_upload_session(
            principal=Principal(subject="native-http", scopes=frozenset({"*"})),
            request_key="a" * 64,
            index_after_import=False,
        )
        session_id = link.status.session_id
        headers = {
            "Origin": "https://testserver",
            "Sec-Fetch-Site": "same-origin",
        }
        with TestClient(
            create_app(context=context),
            base_url="https://testserver",
        ) as client:
            assert client.get("/api/v1/media").status_code == 401
            page = client.get(f"/upload-handoff/{session_id}")
            assert page.status_code == 200
            assert "script-src 'self'" in page.headers["content-security-policy"]
            with patch(
                "vidxp.upload_page.copy_upload",
                side_effect=AssertionError("unauthenticated body was staged"),
            ):
                unauthenticated = client.post(
                        (
                            f"/upload-handoff/{session_id}/files/"
                            "123456781234423481234567890abcde/content"
                        ),
                    files={"upload": (first.name, first.read_bytes(), "video/mp4")},
                    headers=headers,
                )
            assert unauthenticated.status_code == 401
            bootstrap = client.post(
                f"/upload-handoff/{session_id}/bootstrap",
                json={"capability": link.capability},
                headers=headers,
            )
            assert bootstrap.status_code == 200
            contract = bootstrap.json()["status"]
            assert contract["transfer_backend"] == "multipart"
            assert contract["resumable"] is False
            assert contract["maximum_file_bytes"] == min(
                settings.max_local_import_bytes,
                settings.http_max_small_upload_bytes,
            )

            for position, source in enumerate((first, second), start=1):
                payload = source.read_bytes()
                authorized = client.post(
                    f"/upload-handoff/{session_id}/files",
                    json={
                        "client_file_key": f"browser-{position}",
                        "original_filename": source.name,
                        "byte_size": len(payload),
                        "declared_mime_type": "video/mp4",
                    },
                    headers=headers,
                )
                assert authorized.status_code == 200
                assert authorized.json()["grant"] is None
                intent_id = authorized.json()["status"]["intent_id"]
                uploaded = client.post(
                    (f"/upload-handoff/{session_id}/files/{intent_id}/content"),
                    files={"upload": (source.name, payload, "video/mp4")},
                    headers=headers,
                )
                assert uploaded.status_code == 200, uploaded.text

            for _attempt in range(300):
                status = client.get(f"/upload-handoff/{session_id}/status").json()[
                    "status"
                ]
                if all(
                    item["phase"] in {"registered", "failed"}
                    for item in status["items"]
                ):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Native multipart imports did not finish.")
            assert [item["phase"] for item in status["items"]] == [
                "registered",
                "registered",
            ]
            assert all(item["import_job_id"] for item in status["items"])
            assert all(item["index_job_id"] is None for item in status["items"])
    finally:
        context.close()


def test_local_ingestion_automatically_indexes_and_becomes_searchable(
    tmp_path: Path,
) -> None:
    asyncio.run(_automatic_index_scenario(tmp_path))


async def _automatic_index_scenario(tmp_path: Path) -> None:
    source = tmp_path / "searchable.mp4"
    _video(source, "green")
    settings = VidXPSettings(
        data_dir=tmp_path / "data",
        repository_root=tmp_path / "repository",
        runtime_backend="cpu",
        model_cache=default_model_directory(),
        trusted_local_import_roots=(tmp_path,),
        workflow_poll_interval_seconds=0.05,
    )
    context = create_control_plane_application(settings)
    try:
        server = create_mcp_server(
            context,
            default_principal=Principal(
                subject="local",
                client_id="stdio",
                scopes=frozenset({"*"}),
            ),
            filesystem_accessible=True,
        )
        async with Client(server) as client:
            submitted = await client.call_tool(
                "ingest_local_media",
                {
                    "command": {
                        "paths": [str(source)],
                        "modalities": ["scene"],
                    },
                    "idempotency_key": "local-auto-index-0001",
                },
            )
            assert not submitted.is_error
            ingestion_id = submitted.structured_content["session_id"]
            intent_id = submitted.structured_content["items"][0]["intent_id"]
            for _attempt in range(900):
                stored = context.catalog.get_upload_intent(intent_id)
                if stored is not None and stored.state.value in {"indexed", "failed"}:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError(
                    "Autonomous indexing did not finish without status polling."
                )
            status = await client.call_tool(
                "get_media_ingestion",
                {"ingestion_id": ingestion_id},
            )
            assert not status.is_error
            item = status.structured_content["items"][0]
            assert item["phase"] == "indexed", item.get("error")
            assert item["import_job_id"]
            assert item["index_job_id"]
            assert item["media_id"]
            assert item["generation_id"]
            assert item["snapshot_id"]
            assert item["searchable"] is True

            search = await client.call_tool(
                "search_moments",
                {
                    "command": {
                        "query": "green frame",
                        "modalities": ["scene"],
                        "media_id": item["media_id"],
                        "top_k": 1,
                    },
                    "idempotency_key": "search-after-ingestion-0001",
                },
            )
            assert not search.is_error
            job_id = search.structured_content["job_id"]
            for _attempt in range(900):
                completed = await client.call_tool(
                    "get_job",
                    {"job_id": job_id},
                )
                assert not completed.is_error
                if completed.structured_content["state"] in {
                    "succeeded",
                    "failed",
                    "recovery_exhausted",
                }:
                    break
                await asyncio.sleep(0.1)
            else:
                raise AssertionError("Search after ingestion did not finish.")
            assert completed.structured_content["state"] == "succeeded"
            moments = completed.structured_content["result"]["result"]["moments"]
            assert moments
            assert moments[0]["media_id"] == item["media_id"]
    finally:
        context.close()


def test_streamable_http_browser_upload_indexes_in_one_session(
    tmp_path: Path,
) -> None:
    asyncio.run(_streamable_http_ingestion_scenario(tmp_path))


async def _streamable_http_ingestion_scenario(tmp_path: Path) -> None:
    sources = (
        tmp_path / "http-green.mp4",
        tmp_path / "http-blue.mp4",
    )
    _video(sources[0], "green")
    _video(sources[1], "blue")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    settings = VidXPSettings(
        data_dir=tmp_path / "data",
        repository_root=tmp_path / "repository",
        runtime_backend="cpu",
        model_cache=default_model_directory(),
        http_bind_host="127.0.0.1",
        http_port=port,
        workflow_poll_interval_seconds=0.05,
    )
    context = create_http_application(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(context=context),
            host="127.0.0.1",
            port=port,
            log_level="critical",
        )
    )
    serving = asyncio.create_task(server.serve())
    try:
        for _attempt in range(300):
            if server.started:
                break
            if serving.done():
                await serving
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Native vidxp-api did not start.")

        origin = f"http://127.0.0.1:{port}"
        async with httpx2.AsyncClient() as http_client:
            transport = streamable_http_client(
                f"{origin}/mcp",
                http_client=http_client,
            )
            async with Client(transport) as client:
                created = await client.call_tool(
                    "create_media_upload",
                    {
                        "idempotency_key": "http-auto-index-0001",
                        "modalities": ["scene"],
                    },
                )
                assert not created.is_error
                session_id = created.structured_content["session_id"]
                link = created.structured_content["upload_session_url"]
                parsed = urlsplit(link)
                capability = parse_qs(parsed.fragment)["capability"][0]
                assert parsed.netloc == f"127.0.0.1:{port}"

                page = await http_client.get(f"{origin}/upload-handoff/{session_id}")
                assert page.status_code == 200
                assert "script-src 'self'" in page.headers["content-security-policy"]
                browser_headers = {
                    "Origin": origin,
                    "Sec-Fetch-Site": "same-origin",
                }
                bootstrap = await http_client.post(
                    f"{origin}/upload-handoff/{session_id}/bootstrap",
                    json={"capability": capability},
                    headers=browser_headers,
                )
                assert bootstrap.status_code == 200
                cookie = bootstrap.headers["set-cookie"].split(";", 1)[0]
                contract = bootstrap.json()["status"]
                assert contract["transfer_backend"] == "multipart"
                assert contract["resumable"] is False

                request_headers = {**browser_headers, "Cookie": cookie}
                for position, source in enumerate(sources, start=1):
                    payload = source.read_bytes()
                    authorized = await http_client.post(
                        f"{origin}/upload-handoff/{session_id}/files",
                        json={
                            "client_file_key": f"http-browser-file-{position}",
                            "original_filename": source.name,
                            "byte_size": len(payload),
                            "declared_mime_type": "video/mp4",
                        },
                        headers=request_headers,
                    )
                    assert authorized.status_code == 200, authorized.text
                    intent_id = authorized.json()["status"]["intent_id"]
                    uploaded = await http_client.post(
                        (
                            f"{origin}/upload-handoff/{session_id}/files/"
                            f"{intent_id}/content"
                        ),
                        files={"upload": (source.name, payload, "video/mp4")},
                        headers=request_headers,
                    )
                    assert uploaded.status_code == 200, uploaded.text

                items = []
                for _attempt in range(900):
                    status = await client.call_tool(
                        "get_media_upload",
                        {"upload_session_id": session_id},
                    )
                    assert not status.is_error
                    items = status.structured_content["items"]
                    if len(items) == 2 and all(
                        item["phase"] in {"indexed", "failed"} for item in items
                    ):
                        break
                    await asyncio.sleep(0.1)
                else:
                    raise AssertionError("HTTP ingestion did not finish indexing.")
                assert [item["phase"] for item in items] == ["indexed", "indexed"]
                assert all(item["searchable"] is True for item in items)
                assert all(
                    item["import_job_id"] and item["index_job_id"] for item in items
                )
                assert all(
                    item["generation_id"] and item["snapshot_id"] for item in items
                )
                assert len({item["media_id"] for item in items}) == 2

                index = await client.call_tool("get_index_status", {})
                assert not index.is_error
                summary = index.structured_content["summary"]
                assert summary["media_count"] == 2
                assert set(summary["media_ids"]) == {item["media_id"] for item in items}
    finally:
        server.should_exit = True
        await serving
        context.close()
