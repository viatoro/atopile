import threading
import uuid
from pathlib import PurePosixPath
from unittest.mock import MagicMock, patch

import pytest

from atopile.telemetry import telemetry
from atopile.telemetry.config import TelemetryConfig
from atopile.telemetry.properties import PropertyLoaders, _normalize_git_remote_url


@pytest.mark.parametrize(
    ("git_remote",),
    [
        ("https://github.com/atopile/atopile.git",),
        ("git@github.com:atopile/atopile.git",),
    ],
)
def test_normalize_git_remote_url(git_remote):
    assert _normalize_git_remote_url(git_remote) == "github.com/atopile/atopile"


def test_capture_event():
    @telemetry.capture(
        "test_start",
        "test_end",
        {"test_property": "test_value"},
    )
    def test_capture_event():
        pass

    test_capture_event()


def test_capture_exception():
    try:
        raise Exception("test_exception")
    except Exception as e:
        telemetry.capture_exception(e, {"test_property": "test_value"})


def test_telemetry_worker_starts_lazily(monkeypatch):
    monkeypatch.setattr(telemetry.atexit, "register", lambda _: None)
    monkeypatch.setattr(
        telemetry.TelemetryConfig,
        "load",
        lambda: TelemetryConfig(telemetry=True, id=uuid.uuid4()),
    )
    monkeypatch.setattr(
        telemetry,
        "TelemetryProperties",
        lambda thin: type("FakeTelemetryProperties", (), {"dump": lambda self: {}})(),
    )
    processed = threading.Event()

    class FakeClient:
        def capture(self, *args, **kwargs):
            processed.set()

        def capture_exception(self, *args, **kwargs):
            processed.set()

        def close(self):
            pass

    monkeypatch.setattr(telemetry, "TelemetryClient", FakeClient)

    t = telemetry.Telemetry()
    assert t._worker is None

    t.capture("test_event", telemetry.ThinProperties(extra={"x": 1}))

    assert processed.wait(timeout=1.0)
    assert t._worker is not None
    t.flush()


def test_flush_without_started_worker_is_noop(monkeypatch):
    monkeypatch.setattr(telemetry.atexit, "register", lambda _: None)
    monkeypatch.setattr(
        telemetry.TelemetryConfig,
        "load",
        lambda: TelemetryConfig(telemetry=True, id=uuid.uuid4()),
    )

    t = telemetry.Telemetry()

    assert t._worker is None
    t.flush()
    assert t._shutdown.is_set()


def test_capture_degrades_when_worker_start_fails(monkeypatch):
    monkeypatch.setattr(telemetry.atexit, "register", lambda _: None)
    monkeypatch.setattr(
        telemetry.TelemetryConfig,
        "load",
        lambda: TelemetryConfig(telemetry=True, id=uuid.uuid4()),
    )

    t = telemetry.Telemetry()
    monkeypatch.setattr(
        t,
        "_ensure_worker_started",
        lambda: (_ for _ in ()).throw(RuntimeError("thread start failed")),
    )

    t.capture("test_event", telemetry.ThinProperties(extra={"x": 1}))

    assert t._worker is None


# ── install_method detection tests ────────────────────


def _clear_install_method_cache():
    PropertyLoaders.install_method.cache.clear()


def _mock_dist(path_str, direct_url_json=None):
    """Create a mock distribution with a given path and optional direct_url.json."""
    dist = MagicMock()
    dist._path = PurePosixPath(path_str)
    dist.read_text = lambda name: direct_url_json if name == "direct_url.json" else None
    return dist


def test_install_method_playground(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.setenv("ATO_PLAYGROUND", "1")
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    assert PropertyLoaders.install_method() == "playground"


def test_install_method_docker(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    monkeypatch.setenv("ATO_VIA_DOCKER", "1")
    assert PropertyLoaders.install_method() == "docker"


def test_install_method_dev(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/home/user/code/atopile/.venv/lib/python3.14/site-packages/atopile-0.14.dist-info",
        '{"url":"file:///home/user/code/atopile","dir_info":{"editable":true}}',
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "dev"


def test_install_method_vsce_managed(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/home/user/.vscode-server/data/User/globalStorage/atopile.atopile/uv/cache/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "vsce-managed"


def test_install_method_uv_tool(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/home/user/.local/share/uv/tools/atopile/lib/python3.14/site-packages/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "uv-tool"


def test_install_method_brew(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/opt/homebrew/Cellar/atopile/0.14/lib/python3.14/site-packages/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "brew"


def test_install_method_pipx(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/home/user/.local/pipx/venvs/atopile/lib/python3.14/site-packages/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "pipx"


def test_install_method_pip_fallback(monkeypatch):
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "/home/user/myproject/.venv/lib/python3.14/site-packages/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "pip"


def test_install_method_vsce_managed_appdata(monkeypatch):
    """Test VSCE detection with a Windows-style AppData path (forward slashes)."""
    _clear_install_method_cache()
    monkeypatch.delenv("ATO_VIA_DOCKER", raising=False)
    monkeypatch.delenv("ATO_PLAYGROUND", raising=False)
    dist = _mock_dist(
        "C:/Users/user/AppData/Roaming/Code/User/globalStorage/atopile.atopile/uv/cache/atopile-0.14.dist-info",
    )
    with patch("importlib.metadata.distribution", return_value=dist):
        assert PropertyLoaders.install_method() == "vsce-managed"
