"""Offline gates must prevent actual I/O and reject hidden attempted calls."""

import importlib
import os
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def guard_module():
    assert importlib.util.find_spec("scripts.offline_guard"), "offline gate is not implemented"
    return importlib.import_module("scripts.offline_guard")


@pytest.mark.parametrize("operation", ["connect", "connect_ex", "dns"])
def test_network_is_blocked_before_io_and_counted(operation):
    module = guard_module()
    guard = module.NetworkGuard()
    guard.install()
    try:
        with pytest.raises(RuntimeError, match="offline_network_forbidden"):
            if operation == "dns":
                socket.getaddrinfo("must-not-be-resolved.invalid", 443)
            else:
                with socket.socket() as client:
                    getattr(client, operation)(("127.0.0.1", 9))
        assert guard.violations == 1
    finally:
        guard.restore()


def test_in_memory_http_transport_is_allowed():
    module = guard_module()
    guard = module.NetworkGuard()
    guard.install()
    try:
        with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"fixture": True})
        )) as client:
            assert client.get("https://example.invalid").json() == {"fixture": True}
        assert guard.violations == 0
    finally:
        guard.restore()


@pytest.mark.parametrize(("operation", "arguments"), [
    ("gethostbyname", ("fixture.invalid",)),
    ("gethostbyname_ex", ("fixture.invalid",)),
    ("gethostbyaddr", ("192.0.2.1",)),
    ("getnameinfo", (("192.0.2.1", 443), 0)),
])
def test_every_public_dns_resolver_is_blocked_before_native_io(monkeypatch, operation, arguments):
    module = guard_module()
    # A fake native entry makes the failing regression safe: no DNS is ever sent.
    monkeypatch.setattr(socket, operation, lambda *a, **k: "native-resolver-entered")
    guard = module.NetworkGuard()
    guard.install()
    try:
        with pytest.raises(RuntimeError, match="offline_network_forbidden"):
            getattr(socket, operation)(*arguments)
        assert guard.violations == 1
    finally:
        guard.restore()


def test_restore_does_not_destroy_outer_guard():
    module = guard_module()
    outer, inner = module.NetworkGuard(), module.NetworkGuard()
    outer.install()
    inner.install()
    inner.restore()
    try:
        with pytest.raises(RuntimeError):
            socket.getaddrinfo("fixture.invalid", 443)
        assert outer.violations == 1 and inner.violations == 0
    finally:
        outer.restore()


def test_stdlib_socketpair_ipc_is_allowed_but_ordinary_loopback_is_blocked():
    module = guard_module()
    guard = module.NetworkGuard()
    guard.install()
    try:
        # Python's Windows event loop uses this stdlib TCP fallback as local IPC.
        first, second = socket._fallback_socketpair()
        with first, second:
            first.sendall(b"local-ipc")
            assert second.recv(9) == b"local-ipc"
        assert guard.violations == 0
        with socket.socket() as client, pytest.raises(RuntimeError):
            client.connect(("127.0.0.1", 9))
        assert guard.violations == 1
    finally:
        guard.restore()


def test_asgi_testclient_remains_offline_with_guard_installed():
    module = guard_module()
    app = FastAPI()

    @app.get("/fixture")
    def fixture():
        return {"offline": True}

    guard = module.NetworkGuard()
    guard.install()
    try:
        with TestClient(app) as client:
            assert client.get("/fixture").json() == {"offline": True}
        assert guard.violations == 0
    finally:
        guard.restore()


def test_caught_network_exception_still_fails_pytest_session(tmp_path):
    guard_module()
    file = tmp_path / "test_swallowed.py"
    file.write_text("import socket\ndef test_swallowed():\n"
                    "    try: socket.getaddrinfo('fixture.invalid', 443)\n"
                    "    except RuntimeError: pass\n", encoding="utf-8")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", str(file), "-q",
                             "-p", "scripts.offline_guard", "--rootdir", str(tmp_path),
                             "--confcutdir", str(tmp_path)],
                            capture_output=True, text=True, env=env, timeout=30, check=False,
                            cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "offline_network_attempts=1" in result.stdout
    assert "fixture.invalid" not in result.stdout


def test_offline_success_explicitly_reports_zero_network_attempts(tmp_path):
    guard_module()
    file = tmp_path / "test_offline.py"
    file.write_text("def test_local():\n    assert 2 + 2 == 4\n", encoding="utf-8")
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONUTF8": "1"}
    result = subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", str(file), "-q",
                             "-p", "scripts.offline_guard", "--rootdir", str(tmp_path),
                             "--confcutdir", str(tmp_path)],
                            capture_output=True, text=True, env=env, timeout=30, check=False,
                            cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "offline_network_attempts=0" in result.stdout.splitlines()


def runner_module():
    assert importlib.util.find_spec("scripts.verify_offline"), "offline runner is not implemented"
    return importlib.import_module("scripts.verify_offline")


def test_child_environment_cannot_inherit_live_opt_ins_or_credentials():
    module = runner_module()
    fixture_value = "private-" + "fixture"
    result = module.sanitized_env({"PATH": "local-path", "DOCUCHECK_RUN_MYSQL_TESTS": "1",
        "DOCUCHECK_RUN_MINERU_MYSQL": "1", "DOCUCHECK_MODEL_API_KEY": fixture_value,
        "OPENAI_API_KEY": fixture_value, "LANGSMITH_API_KEY": fixture_value,
        "DOCUCHECK_CJK_FONT": "fixture.ttf", "LANGSMITH_TRACING": "true"})
    assert result["PATH"] == "local-path"
    assert result["DOCUCHECK_CJK_FONT"] == "fixture.ttf"
    assert not any(key.startswith("DOCUCHECK_RUN_") for key in result)
    assert not any(key.endswith("API_KEY") for key in result)
    assert result["LANGSMITH_TRACING"] == "false"
    assert result["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"


def test_offline_children_do_not_inherit_project_identity_storage_or_parser_config():
    module = runner_module()
    private = {name: "private-fixture" for name in (
        "DOCUCHECK_EVAL_TOKEN", "DOCUCHECK_OIDC_ADMIN_PASSWORD",
        "DOCUCHECK_OIDC_DEMO_PASSWORD", "DOCUCHECK_AUTH_MODE",
        "DOCUCHECK_OIDC_ISSUER", "DOCUCHECK_MINERU_EXECUTABLE",
        "DOCUCHECK_DATA_DIR", "DOCUCHECK_FUTURE_PRIVATE_SETTING",
    )}
    result = module.sanitized_env({**private, "PATH": "fixture-path",
                                   "DOCUCHECK_CJK_FONT": "fixture.ttf"})
    assert not private.keys() & result.keys()
    assert result["PATH"] == "fixture-path"
    assert result["DOCUCHECK_CJK_FONT"] == "fixture.ttf"


def test_missing_font_fails_before_any_test_process(tmp_path, monkeypatch, capsys):
    module = runner_module()
    monkeypatch.setenv("DOCUCHECK_CJK_FONT", str(tmp_path / "missing.ttf"))
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("process_started"))
    assert module.main(["--report", str(tmp_path / "result.json")]) == 2
    assert "cjk_font_missing" in capsys.readouterr().out


def test_report_is_not_overwritten(tmp_path, monkeypatch, capsys):
    module = runner_module()
    target = tmp_path / "result.json"
    target.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: pytest.fail("process_started"))
    assert module.main(["--report", str(target)]) == 2
    assert target.read_text(encoding="utf-8") == "preserve"
    assert "report_exists" in capsys.readouterr().out
