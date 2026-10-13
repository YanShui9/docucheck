"""Slow synchronous authorization must leave the shared ASGI loop responsive."""

import asyncio
from threading import Event, Thread

import httpx
import pytest
from sqlalchemy import create_engine

from backend.app.api.auth import Principal
from backend.app.api.main import create_app
from backend.app.db.models import metadata
from backend.tests.test_api_workspace_authorization import DemoAuthenticator


@pytest.mark.parametrize("slow_stage", ["authenticate", "require_case"])
def test_slow_authorization_does_not_block_health_or_fast_request(
    tmp_path, monkeypatch, slow_stage,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'authorization.db'}")
    metadata.create_all(engine)
    app = create_app(engine, authenticator=DemoAuthenticator())
    workspace = app.state.workspace
    principal = Principal("http://localhost:8080/realms/demo", "alice")
    project = workspace.create_project(principal, "并发采购")
    case = workspace.create_case(project["id"], principal)
    entered, release, fast_completed = Event(), Event(), Event()

    def pause():
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("slow authorization was not released")

    if slow_stage == "authenticate":
        original = app.state.authenticator.authenticate

        def slow_authenticate(authorization):
            if authorization == "Bearer slow":
                pause()
                authorization = "Bearer alice"
            return original(authorization)

        monkeypatch.setattr(app.state.authenticator, "authenticate", slow_authenticate)
        slow_headers = {"Authorization": "Bearer slow"}
    else:
        original = workspace.require_case

        def slow_require_case(case_id, actor, action):
            if case_id == case["id"]:
                pause()
            return original(case_id, actor, action)

        monkeypatch.setattr(workspace, "require_case", slow_require_case)
        slow_headers = {"Authorization": "Bearer alice"}

    def release_on_stall():
        # A separate thread releases an event-loop-blocking regression, so
        # the assertion fails without hanging the test or leaving a worker.
        try:
            if entered.wait(timeout=5):
                fast_completed.wait(timeout=3)
        finally:
            release.set()

    watchdog = Thread(target=release_on_stall, daemon=True)
    watchdog.start()

    async def requests():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            slow = asyncio.create_task(client.get(
                f"/api/v1/cases/{case['id']}", headers=slow_headers,
            ))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                health, fast = await asyncio.gather(
                    client.get("/health/live"),
                    client.get("/api/v1/me", headers={"Authorization": "Bearer alice"}),
                )
                assert health.status_code == fast.status_code == 200
                assert fast.json()["subject"] == "alice"
                assert not release.is_set(), "fast requests waited for slow authorization"
                fast_completed.set()
            finally:
                fast_completed.set()
                release.set()
                response = await slow
                assert response.status_code == 200

    try:
        asyncio.run(requests())
    finally:
        fast_completed.set()
        release.set()
        watchdog.join(timeout=5)
        engine.dispose()
