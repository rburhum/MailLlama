from __future__ import annotations

import socket
import threading

from mailllama.config import Settings
from mailllama.ssh_tunnel import maybe_ssh_tunnel


def test_disabled_tunnel_is_noop():
    s = Settings(ssh_tunnel_enabled=False)
    with maybe_ssh_tunnel(s) as t:
        assert t.spawned is False
        assert t.pid is None


def test_reuses_existing_local_listener():
    # Bind a real listener on an ephemeral port, then ask the tunnel to "use"
    # that port — it should see the port is open and skip spawning ssh.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    stop = threading.Event()

    def accept_loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                return

    t = threading.Thread(target=accept_loop, daemon=True)
    t.start()
    try:
        s = Settings(
            ssh_tunnel_enabled=True,
            ssh_tunnel_host="nonexistent-host",
            ssh_tunnel_local_port=port,
        )
        with maybe_ssh_tunnel(s) as state:
            assert state.spawned is False  # reused, didn't spawn
            assert state.local_port == port
            assert state.pid is None
    finally:
        stop.set()
        srv.close()
        t.join(timeout=1)


def test_missing_host_raises_when_enabled():
    s = Settings(ssh_tunnel_enabled=True, ssh_tunnel_host=None)
    try:
        with maybe_ssh_tunnel(s):
            pass
    except RuntimeError as e:
        assert "SSH_TUNNEL_HOST" in str(e)
    else:
        raise AssertionError("expected RuntimeError")
