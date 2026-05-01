import resource
import sys
from types import SimpleNamespace

from atopile.lsp.forkserver_worker import _get_current_rss_mb


def test_get_current_rss_mb_returns_zero_on_windows(monkeypatch):
    monkeypatch.setattr("atopile.lsp.forkserver_worker.sys.platform", "win32")

    assert _get_current_rss_mb() == 0.0


def test_get_current_rss_mb_returns_zero_when_resource_missing(monkeypatch):
    monkeypatch.setattr("atopile.lsp.forkserver_worker.sys.platform", "linux")
    monkeypatch.setitem(sys.modules, "resource", None)

    assert _get_current_rss_mb() == 0.0


def test_get_current_rss_mb_uses_linux_kib_fallback(monkeypatch):
    monkeypatch.setattr(
        "atopile.lsp.forkserver_worker.Path.read_text",
        lambda self: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        resource,
        "getrusage",
        lambda _who: SimpleNamespace(ru_maxrss=2048),
    )
    monkeypatch.setattr("atopile.lsp.forkserver_worker.sys.platform", "linux")

    assert _get_current_rss_mb() == 2.0


def test_get_current_rss_mb_uses_darwin_byte_fallback(monkeypatch):
    monkeypatch.setattr(
        "atopile.lsp.forkserver_worker.Path.read_text",
        lambda self: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(
        resource,
        "getrusage",
        lambda _who: SimpleNamespace(ru_maxrss=2 * 1024 * 1024),
    )
    monkeypatch.setattr("atopile.lsp.forkserver_worker.sys.platform", "darwin")

    assert _get_current_rss_mb() == 2.0
