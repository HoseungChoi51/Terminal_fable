"""Detachable PTY sessions: a per-pane daemon that outlives the frontend.

Terminal Fable normally lets VTE own the PTY inside the GUI process, so the
child dies when the window closes. This module decouples them: a small daemon
owns the PTY + child shell and keeps running after the frontend goes away; a
thin *attach* client (spawned by VTE in place of the shell) bridges VTE's PTY
to the daemon over a Unix socket and replays recent output so reattaching
"reloads the context".

GTK-free, stdlib-only (ADR 0003/0005). The bytes flow through untouched, so
OSC-666 shell-integration and the journal keep working (unlike a filtering
multiplexer). One daemon per session, discoverable for reattach like `tmux ls`.

    frontend (VTE)  --spawn-->  `ptyd attach --create -- <shell>`
                                      |  unix socket
                                      v
                                  daemon (owns PTY master + child shell)

Wire protocol: length-framed messages, `!BI` header (type, length) + payload.
  DATA   — raw bytes (child output daemon→client; keystrokes client→daemon)
  RESIZE — `!HH` (rows, cols), client→daemon
On connect the daemon replays its bounded output ring as DATA frames.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import select
import signal
import socket
import struct
import sys
import tempfile
import termios
import time
import tty
from dataclasses import dataclass

# -- protocol -----------------------------------------------------------

DATA = 1
RESIZE = 2
_HEADER = struct.Struct("!BI")          # message type, payload length
_MAX_FRAME = 8 << 20                     # 8 MiB guard against a desync

DEFAULT_RING_BYTES = 512 * 1024         # replayed on reattach to reload context


def encode(mtype: int, payload: bytes) -> bytes:
    return _HEADER.pack(mtype, len(payload)) + payload


class FrameDecoder:
    """Accumulate bytes; yield complete (type, payload) frames."""

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes):
        self._buf += data
        out = []
        while len(self._buf) >= _HEADER.size:
            mtype, length = _HEADER.unpack_from(self._buf, 0)
            if length > _MAX_FRAME:
                raise ValueError(f"frame too large: {length}")
            if len(self._buf) < _HEADER.size + length:
                break
            start = _HEADER.size
            payload = bytes(self._buf[start:start + length])
            del self._buf[:start + length]
            out.append((mtype, payload))
        return out


# -- session paths / discovery ------------------------------------------

@dataclass(frozen=True)
class SessionInfo:
    id: str
    pid: int | None = None
    child_pid: int | None = None
    cwd: str | None = None
    argv: tuple = ()
    created_at: float | None = None
    title: str | None = None


def session_dir(env=None) -> str:
    env = os.environ if env is None else env
    base = env.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, "agent-terminal", "pty")


def _ensure_dir() -> str:
    directory = session_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    return directory


def socket_path(session_id: str) -> str:
    return os.path.join(session_dir(), f"{session_id}.sock")


def _info_path(session_id: str) -> str:
    return os.path.join(session_dir(), f"{session_id}.json")


def _write_info(session_id: str, info: dict) -> None:
    try:
        with open(_info_path(session_id), "w", encoding="utf-8") as handle:
            json.dump(info, handle)
    except OSError:
        pass


def _read_info(session_id: str) -> dict | None:
    try:
        with open(_info_path(session_id), encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _cleanup_files(session_id: str) -> None:
    for path in (socket_path(session_id), _info_path(session_id)):
        try:
            os.unlink(path)
        except OSError:
            pass


def session_alive(session_id: str) -> bool:
    """A session is live if its info file names a daemon pid that exists and
    its socket is present."""
    info = _read_info(session_id)
    if not info or not info.get("pid"):
        return False
    if not os.path.exists(socket_path(session_id)):
        return False
    try:
        os.kill(int(info["pid"]), 0)
        return True
    except (OSError, ValueError):
        return False


def list_sessions() -> list:
    """Live sessions (newest first); reap stale socket/info files in passing."""
    out = []
    try:
        names = os.listdir(session_dir())
    except OSError:
        return out
    for name in names:
        if not name.endswith(".sock"):
            continue
        session_id = name[:-len(".sock")]
        if session_alive(session_id):
            info = _read_info(session_id) or {}
            out.append(SessionInfo(
                id=session_id, pid=info.get("pid"),
                child_pid=info.get("child_pid"), cwd=info.get("cwd"),
                argv=tuple(info.get("argv") or ()),
                created_at=info.get("created_at"), title=info.get("title")))
        else:
            _cleanup_files(session_id)
    out.sort(key=lambda s: s.created_at or 0.0, reverse=True)
    return out


def kill_session(session_id: str) -> bool:
    """Terminate a session's daemon (and its child); returns whether a live
    daemon was signalled."""
    info = _read_info(session_id)
    signalled = False
    if info and info.get("pid"):
        try:
            os.kill(int(info["pid"]), signal.SIGTERM)
            signalled = True
        except (OSError, ValueError):
            pass
    if not signalled:
        _cleanup_files(session_id)
    return signalled


# -- low-level io helpers -----------------------------------------------

def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        try:
            written = os.write(fd, view)
        except BlockingIOError:
            select.select([], [fd], [], 1.0)
            continue
        view = view[written:]


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass


def _get_winsize(fd: int) -> tuple[int, int]:
    try:
        packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, _, _ = struct.unpack("HHHH", packed)
        return rows, cols
    except (OSError, struct.error):
        return 24, 80


# -- daemon -------------------------------------------------------------

def _daemonize() -> None:
    """Detach from the frontend: new session, second fork, ignore SIGHUP,
    replace stdio with /dev/null. After this the process outlives its
    spawner."""
    os.setsid()
    if os.fork() > 0:
        os._exit(0)                     # first child exits; grandchild lives on
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    if devnull > 2:
        os.close(devnull)


def serve(session_id: str, argv, cwd=None, env=None, *,
          ring_bytes: int = DEFAULT_RING_BYTES) -> None:
    """Become the daemon for `session_id`: own a PTY + child running `argv`,
    accept attach clients, replay recent output, and outlive the frontend.
    Does not return (calls os._exit)."""
    _daemonize()
    _ensure_dir()
    argv = list(argv)
    env = dict(env) if env is not None else dict(os.environ)

    master_fd, slave_fd = os.openpty()
    child_pid = os.fork()
    if child_pid == 0:                  # -- child: the shell --
        try:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            for fd in (0, 1, 2):
                os.dup2(slave_fd, fd)
            if slave_fd > 2:
                os.close(slave_fd)
            os.close(master_fd)
            if cwd and os.path.isdir(cwd):
                os.chdir(cwd)
            os.execvpe(argv[0], argv, env)
        except BaseException:
            os._exit(127)
    os.close(slave_fd)

    path = socket_path(session_id)
    try:
        os.unlink(path)
    except OSError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(8)
    server.setblocking(False)
    os.set_blocking(master_fd, False)
    _write_info(session_id, {
        "pid": os.getpid(), "child_pid": child_pid,
        "cwd": cwd, "argv": argv, "created_at": time.time()})

    ring = bytearray()
    clients: dict[int, socket.socket] = {}
    decoders: dict[int, FrameDecoder] = {}

    def drop(fd: int) -> None:
        decoders.pop(fd, None)
        client = clients.pop(fd, None)
        if client is not None:
            try:
                client.close()
            except OSError:
                pass

    def shutdown(*_a) -> None:
        # SIGTERM (kill_session): HUP the child's group; the master EOF then
        # unwinds the loop and cleans up.
        try:
            os.killpg(child_pid, signal.SIGHUP)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, shutdown)

    running = True
    while running:
        watch = [master_fd, server.fileno(), *clients.keys()]
        try:
            readable, _, _ = select.select(watch, [], [])
        except InterruptedError:
            continue
        for fd in readable:
            if fd == master_fd:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    data = b""
                if not data:                      # child exited
                    running = False
                    break
                ring += data
                if len(ring) > ring_bytes:
                    del ring[:len(ring) - ring_bytes]
                for cfd, client in list(clients.items()):
                    try:
                        client.sendall(encode(DATA, data))
                    except OSError:
                        drop(cfd)
            elif fd == server.fileno():
                try:
                    conn, _ = server.accept()
                except OSError:
                    continue
                conn.setblocking(True)
                try:
                    conn.sendall(encode(DATA, bytes(ring)))   # replay context
                except OSError:
                    conn.close()
                    continue
                clients[conn.fileno()] = conn
                decoders[conn.fileno()] = FrameDecoder()
            else:
                client = clients.get(fd)
                if client is None:
                    continue
                try:
                    chunk = client.recv(65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    drop(fd)
                    continue
                try:
                    frames = decoders[fd].feed(chunk)
                except (ValueError, KeyError):
                    drop(fd)
                    continue
                for mtype, payload in frames:
                    if mtype == DATA:
                        try:
                            _write_all(master_fd, payload)
                        except OSError:
                            pass
                    elif mtype == RESIZE and len(payload) >= 4:
                        rows, cols = struct.unpack("!HH", payload[:4])
                        _set_winsize(master_fd, rows, cols)

    for cfd in list(clients):
        drop(cfd)
    try:
        os.close(master_fd)
    except OSError:
        pass
    try:
        server.close()
    except OSError:
        pass
    _cleanup_files(session_id)
    try:
        os.waitpid(child_pid, 0)
    except OSError:
        pass
    os._exit(0)


# -- attach client ------------------------------------------------------

def _connect(session_id: str, timeout: float = 5.0):
    """Connect to a session's daemon, waiting up to `timeout` for it to come
    up (used right after a create). Returns the socket or None."""
    path = socket_path(session_id)
    deadline = time.monotonic() + timeout
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(path)
            return sock
        except OSError as exc:
            sock.close()
            if exc.errno not in (errno.ENOENT, errno.ECONNREFUSED):
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)


def _ensure_daemon(session_id: str, argv, cwd, env, ring_bytes) -> None:
    """Fork+daemonize a `serve` for this session if it isn't already live."""
    if session_alive(session_id):
        return
    _ensure_dir()
    pid = os.fork()
    if pid == 0:
        try:
            serve(session_id, argv, cwd=cwd, env=env, ring_bytes=ring_bytes)
        except BaseException:
            pass
        os._exit(0)
    # Reap the short-lived intermediate (serve double-forks the real daemon).
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass


def attach(session_id: str, *, create_argv=None, cwd=None, env=None,
           ring_bytes: int = DEFAULT_RING_BYTES) -> int:
    """Bridge this process's controlling TTY (fds 0/1) to the session daemon,
    creating it from `create_argv` if missing. Blocks until the TTY closes
    (detach) or the daemon/child ends. Returns an exit code."""
    if not session_alive(session_id):
        if create_argv is None:
            sys.stderr.write(f"ptyd: no such session: {session_id}\n")
            return 1
        _ensure_daemon(session_id, create_argv, cwd, env, ring_bytes)
    sock = _connect(session_id)
    if sock is None:
        sys.stderr.write(f"ptyd: could not attach: {session_id}\n")
        return 1

    old_termios = None
    try:
        old_termios = termios.tcgetattr(0)
        tty.setraw(0)
    except (termios.error, ValueError, OSError):
        pass

    def send_size(*_a) -> None:
        rows, cols = _get_winsize(0)
        try:
            sock.sendall(encode(RESIZE, struct.pack("!HH", rows, cols)))
        except OSError:
            pass

    try:
        signal.signal(signal.SIGWINCH, send_size)
    except (OSError, ValueError):
        pass
    send_size()                         # tell the daemon our initial size

    decoder = FrameDecoder()
    try:
        while True:
            try:
                readable, _, _ = select.select([0, sock.fileno()], [], [])
            except InterruptedError:
                continue
            if 0 in readable:
                try:
                    data = os.read(0, 65536)
                except OSError:
                    data = b""
                if not data:            # frontend/TTY closed -> detach
                    break
                try:
                    sock.sendall(encode(DATA, data))
                except OSError:
                    break
            if sock.fileno() in readable:
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    chunk = b""
                if not chunk:           # daemon gone / child exited
                    break
                try:
                    frames = decoder.feed(chunk)
                except ValueError:
                    break
                for mtype, payload in frames:
                    if mtype == DATA:
                        _write_all(1, payload)
    finally:
        if old_termios is not None:
            try:
                termios.tcsetattr(0, termios.TCSADRAIN, old_termios)
            except (termios.error, OSError):
                pass
        sock.close()
    return 0


# -- cli ----------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ptyd", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_attach = sub.add_parser("attach", help="attach to (or create) a session")
    p_attach.add_argument("--session", required=True)
    p_attach.add_argument("--create", action="store_true",
                          help="create the session from COMMAND if absent")
    p_attach.add_argument("--cwd", default=None)
    p_attach.add_argument("--ring", type=int, default=DEFAULT_RING_BYTES)
    p_attach.add_argument("command", nargs=argparse.REMAINDER,
                          help="after --, the shell/argv to run on create")

    p_list = sub.add_parser("list", help="list live sessions")
    p_kill = sub.add_parser("kill", help="terminate a session")
    p_kill.add_argument("--session", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "attach":
        command = args.command
        if command and command[0] == "--":
            command = command[1:]
        create_argv = command if (args.create and command) else None
        return attach(args.session, create_argv=create_argv, cwd=args.cwd,
                      ring_bytes=args.ring)
    if args.cmd == "list":
        for info in list_sessions():
            print(f"{info.id}\t{info.cwd or ''}\t{' '.join(info.argv)}")
        return 0
    if args.cmd == "kill":
        return 0 if kill_session(args.session) else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
