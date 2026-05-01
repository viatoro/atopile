import socket

from test.runner.main import get_free_port


def test_get_free_port_returns_bindable_ephemeral_port():
    port = get_free_port(bind_host="127.0.0.1")

    assert port > 0

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))


def test_get_free_port_falls_back_when_preferred_port_is_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        preferred_port = busy.getsockname()[1]

        port = get_free_port(start_port=preferred_port, bind_host="127.0.0.1")

    assert port != preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", port))
