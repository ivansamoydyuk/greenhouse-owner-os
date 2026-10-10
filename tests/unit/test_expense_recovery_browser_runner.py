"""Real socket proof; no database, subprocess, or preview is started."""
import socket

import pytest

from scripts.run_expense_recovery_e2e import require_free_preview_port, require_fixture_metadata


def test_preview_preflight_rejects_live_listener():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        server.listen(1)
        with pytest.raises(OSError):
            require_free_preview_port(server.getsockname()[1])


def test_preview_preflight_accepts_stopped_listener_with_time_wait():
    with socket.socket() as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('127.0.0.1', 0))
        port = server.getsockname()[1]
        server.listen(1)
        with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
            connection, _ = server.accept()
            # The accepted server connection closes first, leaving server-side
            # TIME_WAIT after the client acknowledges and closes its end.
            connection.close()
            assert client.recv(1) == b''
    require_free_preview_port(port)


def test_standalone_fixture_registers_all_inbox_foreign_key_targets():
    require_fixture_metadata()
