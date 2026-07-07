"""Unit-level contracts for the shared TUI core (entry model, scanning)."""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from agent_terminal import tui_core


class EntryModelTests(unittest.TestCase):
    def test_three_arg_construction_still_works(self):
        entry = tui_core.PickerEntry("a", "/a", False)
        self.assertIsNone(entry.size)
        self.assertIsNone(entry.mtime)
        self.assertIsNone(entry.mode)


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        (self.base / "sub").mkdir()
        (self.base / "file.txt").write_text("hello")
        (self.base / ".hidden").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_names_verbatim_no_parent_no_slash(self):
        names = {e.name for e in tui_core.scan_directory(self.base)}
        self.assertEqual(names, {"sub", "file.txt"})

    def test_show_hidden(self):
        names = {e.name for e in tui_core.scan_directory(self.base,
                                                         show_hidden=True)}
        self.assertIn(".hidden", names)

    def test_is_dir_flag(self):
        by_name = {e.name: e for e in tui_core.scan_directory(self.base)}
        self.assertTrue(by_name["sub"].is_dir)
        self.assertFalse(by_name["file.txt"].is_dir)

    def test_without_stat_fields_are_none(self):
        for entry in tui_core.scan_directory(self.base):
            self.assertIsNone(entry.size)
            self.assertIsNone(entry.mtime)
            self.assertIsNone(entry.mode)

    def test_with_stat_fields_are_populated(self):
        by_name = {e.name: e
                   for e in tui_core.scan_directory(self.base,
                                                    with_stat=True)}
        entry = by_name["file.txt"]
        self.assertEqual(entry.size, 5)
        self.assertIsNotNone(entry.mtime)
        self.assertTrue(stat.S_ISREG(entry.mode))
        self.assertTrue(stat.S_ISDIR(by_name["sub"].mode))

    def test_broken_symlink_falls_back_to_lstat(self):
        os.symlink(self.base / "missing", self.base / "dangling")
        by_name = {e.name: e
                   for e in tui_core.scan_directory(self.base,
                                                    with_stat=True)}
        entry = by_name["dangling"]
        self.assertFalse(entry.is_dir)
        self.assertIsNotNone(entry.mtime)
        self.assertTrue(stat.S_ISLNK(entry.mode))

    def test_unreadable_directory_yields_empty(self):
        self.assertEqual(
            tui_core.scan_directory(self.base / "does-not-exist"), [])


if __name__ == "__main__":
    unittest.main()
