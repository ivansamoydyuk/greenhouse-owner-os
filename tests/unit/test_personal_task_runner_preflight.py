"""Real ephemeral loopback sockets; no preview, database or browser is started."""
from contextlib import ExitStack
import errno
import socket

import pytest

from scripts.run_personal_tasks_e2e import assert_preview_port_available


def test_personal_runner_free_port_probe_releases_its_socket():
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        address = reservation.getsockname()
    assert_preview_port_available(address[1])
    with socket.socket() as next_listener:
        next_listener.bind(address)
        next_listener.listen(1)


@pytest.mark.parametrize('reuse', [False, True])
def test_personal_runner_rejects_live_listener_even_when_it_allows_reuse(reuse):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, int(reuse))
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with pytest.raises(OSError) as failure:
            assert_preview_port_available(listener.getsockname()[1])
        assert failure.value.errno == errno.EADDRINUSE


def test_personal_runner_accepts_closed_predecessor_time_wait_not_false_busy():
    with ExitStack() as sockets:
        listener = sockets.enter_context(socket.socket())
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(2)
        listener.bind(('127.0.0.1', 0))
        address = listener.getsockname()
        listener.listen(1)
        client = sockets.enter_context(socket.socket())
        client.settimeout(2)
        client.connect(address)
        accepted, _ = listener.accept()
        sockets.enter_context(accepted)
        # Server closes first: its local endpoint remains in TIME_WAIT even
        # after the client and listening socket have closed normally.
        accepted.shutdown(socket.SHUT_WR)
        assert client.recv(1) == b''
        client.close()
        accepted.close()
        listener.close()
        with socket.socket() as old_bare_probe:
            with pytest.raises(OSError) as failure:
                old_bare_probe.bind(address)
            assert failure.value.errno == errno.EADDRINUSE
        assert_preview_port_available(address[1])
