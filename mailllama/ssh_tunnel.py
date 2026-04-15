"""Optional SSH tunnel to a remote LLM server.

When ``SSH_TUNNEL_ENABLED=true``, ``maybe_ssh_tunnel()`` spawns

    ssh -N -T -o ExitOnForwardFailure=yes \\
        -L <local_port>:<remote_host>:<remote_port> <ssh_host>

waits until the local port is reachable, then tears the tunnel down on exit.

If something is already listening on ``local_port`` (e.g. you have an
``ssh -fN`` running yourself), the context manager reuses it and does NOT
spawn a second tunnel.

Your SSH client must be able to connect to ``ssh_host`` non-interactively
(keys / agent / ``~/.ssh/config`` alias). No password prompting happens.
"""

from __future__ import annotations

import logging
import shlex
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass

from .config import Settings, get_settings

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.25


@dataclass
class TunnelState:
    spawned: bool
    local_port: int
    remote: str
    pid: int | None


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_open(host, port):
            return True
        time.sleep(POLL_INTERVAL_S)
    return False


def _build_cmd(settings: Settings) -> list[str]:
    assert settings.ssh_tunnel_host
    forward = (
        f"{settings.ssh_tunnel_local_port}:"
        f"{settings.ssh_tunnel_remote_host}:"
        f"{settings.ssh_tunnel_remote_port}"
    )
    cmd = [
        "ssh",
        "-N",
        "-T",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ServerAliveInterval=30",
        "-L",
        forward,
    ]
    if settings.ssh_tunnel_extra_args.strip():
        cmd.extend(shlex.split(settings.ssh_tunnel_extra_args))
    cmd.append(settings.ssh_tunnel_host)
    return cmd


@contextmanager
def maybe_ssh_tunnel(settings: Settings | None = None):
    """Yield a TunnelState if tunneling is configured, else a no-op state."""
    settings = settings or get_settings()
    if not settings.ssh_tunnel_enabled:
        yield TunnelState(spawned=False, local_port=0, remote="", pid=None)
        return
    if not settings.ssh_tunnel_host:
        raise RuntimeError(
            "SSH_TUNNEL_ENABLED is true but SSH_TUNNEL_HOST is empty. "
            "Run `mailllama setup` or edit your .env."
        )

    local_port = settings.ssh_tunnel_local_port
    remote = (
        f"{settings.ssh_tunnel_remote_host}:{settings.ssh_tunnel_remote_port} "
        f"via {settings.ssh_tunnel_host}"
    )

    # Reuse an already-open port.
    if _port_open("127.0.0.1", local_port):
        log.info("SSH tunnel port %d already open — reusing existing tunnel.", local_port)
        yield TunnelState(spawned=False, local_port=local_port, remote=remote, pid=None)
        return

    cmd = _build_cmd(settings)
    log.info("Starting SSH tunnel: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    try:
        if not _wait_for_port("127.0.0.1", local_port, CONNECT_TIMEOUT_S):
            # The ssh process probably exited — surface its error.
            try:
                proc.terminate()
                _, err = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, err = proc.communicate()
            raise RuntimeError(
                f"SSH tunnel to {remote} did not come up within "
                f"{CONNECT_TIMEOUT_S:.0f}s.\nssh stderr: "
                + err.decode("utf-8", errors="replace").strip()
            )
        log.info("SSH tunnel ready on 127.0.0.1:%d (%s)", local_port, remote)
        yield TunnelState(spawned=True, local_port=local_port, remote=remote, pid=proc.pid)
    finally:
        if proc.poll() is None:
            log.info("Stopping SSH tunnel (pid=%d).", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
