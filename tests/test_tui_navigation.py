"""Unit-level contracts for the TUI picker and control socket payloads."""

import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from agent_terminal import tui_navigation as tui


class ExtensionTests(unittest.TestCase):
    def test_parse_extensions(self):
        self.assertEqual(tui.parse_extensions("md,markdown"),
                         (".md", ".markdown"))
        self.assertEqual(tui.parse_extensions(" .PNG , jpg "),
                         (".png", ".jpg"))
        self.assertEqual(tui.parse_extensions(""), ())
        self.assertEqual(tui.parse_extensions(None), ())


class ListingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        (self.base / "zeta").mkdir()
        (self.base / "alpha").mkdir()
        (self.base / ".hidden_dir").mkdir()
        (self.base / "notes.md").write_text("x")
        (self.base / "image.png").write_bytes(b"x")
        (self.base / "script.py").write_text("x")
        (self.base / ".hidden.md").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_ordering_parent_dirs_then_files(self):
        entries = tui.list_directory(self.base)
        names = [entry.name for entry in entries]
        self.assertEqual(names, ["..", "alpha/", "zeta/", "image.png",
                                 "notes.md", "script.py"])

    def test_parent_entry_points_to_parent(self):
        entries = tui.list_directory(self.base)
        self.assertEqual(entries[0].path, str(self.base.resolve().parent))
        self.assertTrue(entries[0].is_dir)

    def test_extension_filter_applies_to_files_only(self):
        entries = tui.list_directory(self.base, extensions=(".md",))
        names = [entry.name for entry in entries]
        self.assertIn("alpha/", names)
        self.assertIn("notes.md", names)
        self.assertNotIn("script.py", names)
        self.assertNotIn("image.png", names)

    def test_query_filter(self):
        entries = tui.list_directory(self.base, query="not")
        names = [entry.name for entry in entries if entry.name != ".."]
        self.assertEqual(names, ["notes.md"])

    def test_query_is_case_insensitive(self):
        entries = tui.list_directory(self.base, query="NOTES")
        names = [entry.name for entry in entries if entry.name != ".."]
        self.assertEqual(names, ["notes.md"])

    def test_hidden_toggle(self):
        names = [entry.name for entry in tui.list_directory(self.base)]
        self.assertNotIn(".hidden.md", names)
        self.assertNotIn(".hidden_dir/", names)
        names = [entry.name
                 for entry in tui.list_directory(self.base, show_hidden=True)]
        self.assertIn(".hidden.md", names)
        self.assertIn(".hidden_dir/", names)


class PickerStateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        (self.base / "sub").mkdir()
        (self.base / "a.md").write_text("x")
        (self.base / "b.md").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_navigation(self):
        state = tui.PickerState(self.base)
        self.assertEqual(state.directory, str(self.base.resolve()))
        state.move(1)
        self.assertEqual(state.cursor, 1)
        state.move(-10)
        self.assertEqual(state.cursor, 0)

    def test_enter_and_parent(self):
        state = tui.PickerState(self.base)
        state.enter_directory(self.base / "sub")
        self.assertEqual(state.directory, str((self.base / "sub").resolve()))
        state.go_parent()
        self.assertEqual(state.directory, str(self.base.resolve()))

    def test_query_backspace_falls_back_to_parent(self):
        state = tui.PickerState(self.base / "sub")
        state.append_query("x")
        state.backspace()
        self.assertEqual(state.directory, str((self.base / "sub").resolve()))
        state.backspace()
        self.assertEqual(state.directory, str(self.base.resolve()))


class ControlMessageTests(unittest.TestCase):
    def test_message_shape(self):
        message = tui.control_message("open-file", "relative.md")
        self.assertEqual(set(message), {"action", "path"})
        self.assertEqual(message["action"], "open-file")
        self.assertTrue(os.path.isabs(message["path"]))

    def test_encode_is_json_line(self):
        encoded = tui.encode_message({"action": "open-file", "path": "/x"})
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(json.loads(encoded),
                         {"action": "open-file", "path": "/x"})

    def test_send_protocol_over_unix_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            socket_path = os.path.join(tmp, "test.sock")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(socket_path)
            server.listen(1)
            received = []

            def accept_once():
                connection, _ = server.accept()
                with connection:
                    received.append(connection.recv(4096))

            thread = threading.Thread(target=accept_once)
            thread.start()
            ok = tui.send_control_message(
                socket_path, tui.control_message("open-file", "/tmp/x.md"))
            thread.join(timeout=5.0)
            server.close()
            self.assertTrue(ok)
            self.assertEqual(json.loads(received[0]),
                             {"action": "open-file", "path": "/tmp/x.md"})

    def test_send_fails_gracefully_without_socket(self):
        self.assertFalse(tui.send_control_message(
            "/nonexistent/path.sock",
            {"action": "open-file", "path": "/x"}))


if __name__ == "__main__":
    unittest.main()
