"""Contracts for the detachable PTY daemon (agent_terminal/ptyd.py).

Protocol tests are pure; the lifecycle test drives real PTYs + a real daemon
(process survival, reattach/replay, winsize, teardown) in an isolated
XDG_RUNTIME_DIR.
"""

import fcntl
import os
import pty
import select
import struct
import sys
import tempfile
import termios
import time
import unittest

from agent_terminal import ptyd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ProtocolTests(unittest.TestCase):
    def test_roundtrip_split_and_multiple(self):
        blob = (ptyd.encode(ptyd.DATA, b"hello")
                + ptyd.encode(ptyd.RESIZE, struct.pack("!HH", 40, 100)))
        decoder = ptyd.FrameDecoder()
        frames = []
        for i in range(0, len(blob), 3):          # feed in odd chunks
            frames += decoder.feed(blob[i:i + 3])
        self.assertEqual(frames, [(ptyd.DATA, b"hello"),
                                  (ptyd.RESIZE, struct.pack("!HH", 40, 100))])

    def test_partial_frame_waits(self):
        decoder = ptyd.FrameDecoder()
        self.assertEqual(decoder.feed(ptyd.encode(ptyd.DATA, b"abc")[:4]), [])
        self.assertEqual(decoder.feed(ptyd.encode(ptyd.DATA, b"abc")[4:]),
                         [(ptyd.DATA, b"abc")])

    def test_oversized_frame_rejected(self):
        bad = struct.pack("!BI", ptyd.DATA, ptyd._MAX_FRAME + 1)
        with self.assertRaises(ValueError):
            ptyd.FrameDecoder().feed(bad)


class SessionPathTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self._dir

    def tearDown(self):
        if self._old is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._old

    def test_paths_and_missing_session(self):
        self.assertTrue(ptyd.socket_path("x").endswith("/pty/x.sock"))
        self.assertFalse(ptyd.session_alive("nope"))
        self.assertEqual(ptyd.list_sessions(), [])

    def test_attach_nonexistent_without_create_fails(self):
        self.assertEqual(ptyd.attach("nope", create_argv=None), 1)


class _Client:
    """A `ptyd attach` process driven over a real PTY."""

    def __init__(self, session_id, *, create=None, cwd=None):
        argv = [sys.executable, "-m", "agent_terminal.ptyd", "attach",
                "--session", session_id]
        if create:
            argv += ["--create", "--cwd", cwd or os.getcwd(), "--"] + create
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ["TERM"] = "xterm-256color"
            os.chdir(REPO_ROOT)
            os.execvp(argv[0], argv)
            os._exit(127)

    def drain(self, seconds=0.6):
        out, end = b"", time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.fd, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                out += chunk
        return out

    def send(self, text):
        os.write(self.fd, text)

    def set_winsize(self, rows, cols):
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except OSError:
            pass


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.mkdtemp()
        self._old = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self._dir
        self.sid = f"ptydtest-{os.getpid()}"

    def tearDown(self):
        ptyd.kill_session(self.sid)
        ptyd._cleanup_files(self.sid)
        if self._old is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._old

    def test_survive_reattach_resize_teardown(self):
        bash = ["/bin/bash", "--norc", "-i"]
        # create + run something that leaves state and prints a marker
        first = _Client(self.sid, create=bash)
        first.drain(1.0)
        first.send(b"MYVAR=persist_alpha\n")
        first.drain(0.3)
        first.send(b"echo START_$MYVAR\n")
        self.assertIn(b"START_persist_alpha", first.drain(0.6))
        child_pid = (ptyd._read_info(self.sid) or {}).get("child_pid")
        self.assertIsNotNone(child_pid)

        # frontend dies (close the PTY): daemon + child must survive
        first.close()
        time.sleep(0.3)
        self.assertTrue(ptyd.session_alive(self.sid))
        os.kill(child_pid, 0)                        # raises if dead
        self.assertIn(self.sid, [s.id for s in ptyd.list_sessions()])

        # reattach: replay reloads context, and it's the SAME shell
        second = _Client(self.sid)
        self.assertIn(b"START_persist_alpha", second.drain(0.8))   # replayed
        second.send(b"echo REATTACH_$MYVAR\n")
        self.assertIn(b"REATTACH_persist_alpha", second.drain(0.6))

        # winsize propagates through to the child
        second.set_winsize(40, 100)
        time.sleep(0.2)
        second.send(b"stty size\n")
        self.assertIn(b"40 100", second.drain(0.6))

        # child exit tears the daemon down and removes the socket
        second.send(b"exit\n")
        second.drain(0.8)
        second.close()
        time.sleep(0.3)
        self.assertFalse(ptyd.session_alive(self.sid))
        self.assertFalse(os.path.exists(ptyd.socket_path(self.sid)))

    def test_kill_session_reaps_orphan(self):
        first = _Client(self.sid, create=["/bin/bash", "--norc", "-i"])
        first.drain(0.8)
        first.close()                                # orphan the daemon
        time.sleep(0.3)
        self.assertTrue(ptyd.session_alive(self.sid))
        self.assertTrue(ptyd.kill_session(self.sid))
        time.sleep(0.3)
        self.assertFalse(ptyd.session_alive(self.sid))


if __name__ == "__main__":
    unittest.main()
