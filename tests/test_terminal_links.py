"""URL activation regressions, using terminal doubles without a GUI."""

import re
import unittest
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
            cls.pane_type = nt.build_native_classes(g).TerminalPane

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


if __name__ == "__main__":
    unittest.main()
