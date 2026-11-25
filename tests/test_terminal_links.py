"""URL activation regressions, using terminal doubles without a GUI."""

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent_terminal import native_terminal as nt


class TerminalLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g = Mock()
        g.Gtk = SimpleNamespace(
            Widget=object, Window=object, ApplicationWindow=object,
            Application=object,
            EventSequenceState=SimpleNamespace(CLAIMED="claimed"))
        g.Gdk = SimpleNamespace(
            BUTTON_PRIMARY=1,
            ModifierType=SimpleNamespace(CONTROL_MASK=4))
        with patch.object(nt, "_NATIVE_CLASSES", None):
            classes = nt.build_native_classes(g)
            cls.pane_type = classes.TerminalPane
            cls.window_type = classes.NativeTerminalWindow

    def setUp(self):
        # Exercise the real lookup/click handlers without spawning a shell.
        self.pane = object.__new__(self.pane_type)
        self.pane.terminal = Mock()
        self.pane.terminal.check_hyperlink_at.return_value = None
        self.pane._open_uri = Mock()

    def test_ctrl_click_omits_surrounding_prose_punctuation(self):
        cases = (
            ("See https://example.com/docs.", "https://example.com/docs"),
            ("(https://example.com/docs).", "https://example.com/docs"),
            ("[docs](https://example.com/docs)", "https://example.com/docs"),
            ("[https://example.com/docs].", "https://example.com/docs"),
            ("'https://example.com/docs'", "https://example.com/docs"),
            ("https://example.com/docs,", "https://example.com/docs"),
            ("https://example.com/docs;", "https://example.com/docs"),
            ("https://example.com/docs:!?", "https://example.com/docs"),
            ("(https://example.com/docs...).", "https://example.com/docs"),
            ("(https://en.wikipedia.org/wiki/Function_(mathematics)).",
             "https://en.wikipedia.org/wiki/Function_(mathematics)"),
            ("(https://example.com/a_(b_(c))).",
             "https://example.com/a_(b_(c))"),
            ("[https://[::1]].", "https://[::1]"),
            ("https://example.com/?q=(a)&tags[]=b).",
             "https://example.com/?q=(a)&tags[]=b"),
        )
        gesture = Mock()
        gesture.get_current_button.return_value = 1
        gesture.get_current_event_state.return_value = 4
        for text, expected in cases:
            with self.subTest(text=text):
                self.pane._open_uri.reset_mock()
                match = re.search(nt.URL_REGEX_PATTERN, text).group()
                self.pane.terminal.check_match_at.return_value = (match, 0)
                self.pane._on_link_pressed(gesture, 1, 10, 20)
                self.pane._open_uri.assert_called_once_with(expected)

    def test_preserves_url_contents_and_balanced_delimiters(self):
        for uri in (
            "http://localhost:8000/a.b?q=one,two;three!four#section",
            "https://example.com/O'Reilly",
            "https://example.com/wiki/Function_(mathematics)",
            "https://example.com/a_(b_(c))",
            "https://[2001:db8::1]",
            "https://[::1]:8080/?filters[]=a&next=",
            "https://example.com/?q=[value]",
            "https://example.com/a%29%2E",
        ):
            with self.subTest(uri=uri):
                match = re.search(nt.URL_REGEX_PATTERN, uri).group()
                self.pane.terminal.check_match_at.return_value = (match, 0)
                self.assertEqual(self.pane._link_at(10, 20), uri)

    def test_explicit_hyperlink_target_is_unchanged(self):
        for uri in ("https://example.com/end.", "https://example.com/end)",
                    "https://example.com/?q=why?!", "file:///tmp/report."):
            with self.subTest(uri=uri):
                self.pane.terminal.check_hyperlink_at.return_value = uri
                self.assertEqual(self.pane._link_at(10, 20), uri)
                self.pane.terminal.check_match_at.assert_not_called()

    def test_absent_link_is_not_activated(self):
        self.pane.terminal.check_match_at.return_value = (None, -1)
        self.assertIsNone(self.pane._link_at(10, 20))

    def test_falls_back_when_hyperlinks_are_unavailable(self):
        self.pane.terminal.check_hyperlink_at.side_effect = AttributeError
        self.pane.terminal.check_match_at.return_value = (
            "https://example.com/docs).", 0)
        self.assertEqual(self.pane._link_at(10, 20),
                         "https://example.com/docs")

    def test_spawn_advertises_hyperlink_support_to_pi(self):
        self.pane.persistence = None
        self.pane.assistant = None
        for override, expected in ((None, "1"), ("0", "0")):
            with self.subTest(override=override):
                self.pane.terminal.reset_mock()
                environ = {} if override is None else {
                    "PI_HYPERLINKS": override}
                with patch.dict(nt.os.environ, environ, clear=True), \
                        patch.object(
                            nt.copilot_shell, "wrap_argv",
                            side_effect=lambda argv, *args, **kwargs: argv):
                    self.pane._spawn(["bash"], "/tmp", None, None)
                envv = self.pane.terminal.spawn_async.call_args.args[3]
                self.assertIn(f"PI_HYPERLINKS={expected}", envv)

    def test_local_file_uri_decodes_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a b#c.txt"
            path.write_text("test", encoding="utf-8")
            self.assertEqual(nt.local_file_uri_path(path.as_uri()), str(path))
        self.assertIsNone(nt.local_file_uri_path(
            "file://server/share/a.txt"))
        self.assertIsNone(nt.local_file_uri_path(
            "https://example.com/a.txt"))
        self.assertIsNone(nt.local_file_uri_path("file:relative.txt"))

    def test_terminal_editor_resolution(self):
        self.assertEqual(
            nt.terminal_editor_argv({"EDITOR": "vim -f", "VISUAL": "nano"}),
            ["vim", "-f"])
        self.assertEqual(
            nt.terminal_editor_argv({"EDITOR": "'unterminated",
                                     "VISUAL": "nano -w"}),
            ["nano", "-w"])
        with patch.object(nt.shutil, "which", return_value="/usr/bin/editor"):
            self.assertEqual(nt.terminal_editor_argv({}), ["editor"])
        with patch.object(nt.shutil, "which", return_value=None):
            self.assertEqual(nt.terminal_editor_argv({}), ["vi"])

    def test_editor_window_uses_new_terminal_tab(self):
        window = object.__new__(self.window_type)
        window.add_terminal_tab = Mock()
        with patch.object(nt, "terminal_editor_argv",
                          return_value=["vim", "-f"]):
            result = window.open_editor_in_new_tab("/tmp/a b.txt")
        window.add_terminal_tab.assert_called_once_with(
            command=["vim", "-f", "/tmp/a b.txt"],
            working_directory="/tmp",
            title="Edit a b.txt")
        self.assertIs(result, window.add_terminal_tab.return_value)

    def test_file_uri_opens_editor_tab(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a b#c.txt"
            path.write_text("test", encoding="utf-8")
            uri = path.as_uri()
            window = Mock()
            self.pane.terminal.get_root.return_value = window
            self.pane.terminal.check_hyperlink_at.return_value = uri
            gesture = Mock()
            gesture.get_current_button.return_value = 1
            gesture.get_current_event_state.return_value = 4
            self.pane._on_link_pressed(gesture, 1, 10, 20)
            window.open_editor_in_new_tab.assert_called_once_with(str(path))
            self.pane._open_uri.assert_not_called()
            gesture.set_state.assert_called_once_with("claimed")

    def test_directory_uri_keeps_desktop_opener(self):
        with tempfile.TemporaryDirectory() as tmp:
            uri = Path(tmp).as_uri()
            window = Mock()
            self.pane.terminal.get_root.return_value = window
            self.pane.terminal.check_hyperlink_at.return_value = uri
            gesture = Mock()
            gesture.get_current_button.return_value = 1
            gesture.get_current_event_state.return_value = 4
            self.pane._on_link_pressed(gesture, 1, 10, 20)
            window.open_editor_in_new_tab.assert_not_called()
            self.pane._open_uri.assert_called_once_with(uri)


if __name__ == "__main__":
    unittest.main()
