"""Unit-level contracts for the smart-ls pure core."""

import tempfile
import time
import unittest
from pathlib import Path

from agent_terminal import smart_ls
from agent_terminal.tui_core import PickerEntry


class TargetWidthTests(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertIsNone(smart_ls.compute_target_width([]))
        self.assertIsNone(smart_ls.compute_target_width([30]))

    def test_all_same_length_never_truncates(self):
        self.assertIsNone(smart_ls.compute_target_width([10] * 5))

    def test_clear_outlier_truncated_to_mode_plus_slack(self):
        self.assertEqual(smart_ls.compute_target_width([8, 8, 8, 8, 50]),
                         8 + smart_ls.SLACK)

    def test_tie_picks_largest_mode(self):
        self.assertEqual(smart_ls.compute_target_width([4, 4, 12, 12, 40]),
                         12 + smart_ls.SLACK)

    def test_all_unique_lengths_use_median(self):
        self.assertEqual(
            smart_ls.compute_target_width([3, 5, 7, 9, 11, 13, 60]),
            9 + smart_ls.SLACK)

    def test_min_name_width_floor(self):
        self.assertEqual(smart_ls.compute_target_width([2, 2, 2, 2, 30]),
                         smart_ls.MIN_NAME_WIDTH)

    def test_small_saving_not_worth_truncating(self):
        # longest is within MIN_GAIN of the would-be target
        self.assertIsNone(smart_ls.compute_target_width([10, 10, 10, 15]))

    def test_uniformly_long_names_untouched(self):
        self.assertIsNone(smart_ls.compute_target_width([45, 45, 45, 45]))


class TruncateTests(unittest.TestCase):
    def test_short_name_unchanged(self):
        self.assertEqual(smart_ls.truncate_name("abc", 10), "abc")

    def test_end(self):
        self.assertEqual(smart_ls.truncate_name("abcdefghijkl", 8, "end"),
                         "abcdefg…")

    def test_start(self):
        self.assertEqual(smart_ls.truncate_name("abcdefghijkl", 8, "start"),
                         "…fghijkl")

    def test_middle_preserves_extension(self):
        result = smart_ls.truncate_name("very_long_name_here.txt", 12,
                                        "middle")
        self.assertEqual(result, "very_lo….txt")
        self.assertEqual(len(result), 12)

    def test_middle_drops_overlong_extension(self):
        result = smart_ls.truncate_name("name.superlongextension", 12,
                                        "middle")
        self.assertEqual(len(result), 12)
        self.assertNotIn(".superlongextension", result)

    def test_middle_dotfile_leading_dot_is_not_extension(self):
        result = smart_ls.truncate_name(".bashrc_backup_very_long", 10,
                                        "middle")
        self.assertEqual(len(result), 10)
        self.assertTrue(result.startswith(".bash"))

    def test_narrow_width_degrades_to_end(self):
        self.assertEqual(smart_ls.truncate_name("abcdefgh", 4, "middle"),
                         "abc…")


class GridTests(unittest.TestCase):
    def test_basic_geometry(self):
        self.assertEqual(smart_ls.grid_geometry(10, 80, 18), (4, 3))

    def test_rebalance_removes_empty_trailing_column(self):
        # 4 columns of 2 rows would leave the 4th column empty
        self.assertEqual(smart_ls.grid_geometry(5, 100, 23), (3, 2))

    def test_narrow_terminal_single_column(self):
        self.assertEqual(smart_ls.grid_geometry(10, 5, 30), (1, 10))

    def test_empty(self):
        self.assertEqual(smart_ls.grid_geometry(0, 80, 10), (1, 0))

    def test_position_index_round_trip(self):
        rows = 3
        for index in range(10):
            row, column = smart_ls.grid_position(index, rows)
            self.assertEqual(smart_ls.grid_index(row, column, rows), index)
        self.assertEqual(smart_ls.grid_position(0, rows), (0, 0))
        self.assertEqual(smart_ls.grid_position(4, rows), (1, 1))


class FormatTests(unittest.TestCase):
    def test_size_plain(self):
        self.assertEqual(smart_ls.format_size(0), "0")
        self.assertEqual(smart_ls.format_size(12345), "12345")

    def test_size_human(self):
        self.assertEqual(smart_ls.format_size(999, human=True), "999")
        self.assertEqual(smart_ls.format_size(1023, human=True), "1023")
        self.assertEqual(smart_ls.format_size(1024, human=True), "1.0K")
        self.assertEqual(smart_ls.format_size(1536, human=True), "1.5K")
        self.assertEqual(smart_ls.format_size(10 * 1024, human=True), "10K")
        self.assertEqual(smart_ls.format_size(5 * 1024 * 1024, human=True),
                         "5.0M")

    def test_size_unknown(self):
        self.assertEqual(smart_ls.format_size(None), "?")
        self.assertEqual(smart_ls.format_size(None, human=True), "?")

    def test_mtime(self):
        self.assertEqual(smart_ls.format_mtime(0, localtime=time.gmtime),
                         "1970-01-01 00:00")
        self.assertEqual(smart_ls.format_mtime(None), "?")

    def test_mode(self):
        self.assertEqual(smart_ls.format_mode(0o100644), "-rw-r--r--")
        self.assertEqual(smart_ls.format_mode(0o40755), "drwxr-xr-x")
        self.assertEqual(smart_ls.format_mode(None), "??????????")


def _dir(name, mtime=None):
    return PickerEntry(name, "/" + name, True, mtime=mtime)


def _file(name, size=None, mtime=None):
    return PickerEntry(name, "/" + name, False, size=size, mtime=mtime)


class SortTests(unittest.TestCase):
    def setUp(self):
        self.entries = [
            _file("old.txt", size=100, mtime=10),
            _dir("beta", mtime=5),
            _file("nostat.txt"),
            _file("new.txt", size=50, mtime=200),
            _dir("Alpha", mtime=1),
        ]

    def names(self, **kwargs):
        return [e.name for e in smart_ls.sort_entries(self.entries,
                                                      **kwargs)]

    def test_name_sort_dirs_first_casefold(self):
        self.assertEqual(self.names(),
                         ["Alpha", "beta", "new.txt", "nostat.txt",
                          "old.txt"])

    def test_mtime_sort_newest_first(self):
        self.assertEqual(self.names(key="mtime"),
                         ["beta", "Alpha", "new.txt", "old.txt",
                          "nostat.txt"])

    def test_mtime_reverse_keeps_missing_last(self):
        self.assertEqual(self.names(key="mtime", reverse=True),
                         ["Alpha", "beta", "old.txt", "new.txt",
                          "nostat.txt"])

    def test_size_sort_largest_first(self):
        names = self.names(key="size")
        self.assertEqual(names[2:], ["old.txt", "new.txt", "nostat.txt"])

    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            smart_ls.sort_entries(self.entries, key="bogus")


class OpenPlanTests(unittest.TestCase):
    def test_markdown_with_socket_uses_control(self):
        kind, message = smart_ls.open_plan("/tmp/notes.md", "/tmp/x.sock")
        self.assertEqual(kind, "control")
        self.assertEqual(message["action"], "open-file")
        self.assertEqual(message["path"], "/tmp/notes.md")

    def test_image_with_socket_uses_control(self):
        kind, _ = smart_ls.open_plan("/tmp/pic.png", "/tmp/x.sock")
        self.assertEqual(kind, "control")

    def test_other_type_spawns_even_with_socket(self):
        kind, argv = smart_ls.open_plan("/tmp/notes.txt", "/tmp/x.sock")
        self.assertEqual(kind, "spawn")
        self.assertEqual(argv, ["xdg-open", "/tmp/notes.txt"])

    def test_markdown_without_socket_spawns(self):
        kind, argv = smart_ls.open_plan("/tmp/notes.md", None)
        self.assertEqual(kind, "spawn")
        self.assertEqual(argv[0], "xdg-open")


class CwdFileTests(unittest.TestCase):
    def test_writes_resolved_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "cwd"
            smart_ls.write_cwd_file(target, tmp)
            self.assertEqual(target.read_text().strip(),
                             str(Path(tmp).resolve()))


class StateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        (self.base / "sub").mkdir()
        (self.base / "a.md").write_text("x")
        (self.base / "b.txt").write_text("xx")
        (self.base / ".hidden").write_text("x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_initial_listing_sorted_no_parent_entry(self):
        state = smart_ls.SmartLsState(self.base)
        self.assertEqual([e.name for e in state.entries],
                         ["sub", "a.md", "b.txt"])

    def test_move_clamps(self):
        state = smart_ls.SmartLsState(self.base)
        state.move(10)
        self.assertEqual(state.cursor, 2)
        state.move(-10)
        self.assertEqual(state.cursor, 0)

    def test_refresh_clamps_cursor_after_shrink(self):
        state = smart_ls.SmartLsState(self.base)
        state.last()
        (self.base / "a.md").unlink()
        (self.base / "b.txt").unlink()
        state.refresh()
        self.assertEqual(state.cursor, 0)

    def test_enter_resets_cursor(self):
        state = smart_ls.SmartLsState(self.base)
        state.last()
        state.enter_directory(self.base / "sub")
        self.assertEqual(state.directory,
                         str((self.base / "sub").resolve()))
        self.assertEqual(state.cursor, 0)

    def test_go_parent_lands_on_departed_child(self):
        state = smart_ls.SmartLsState(self.base / "sub")
        state.go_parent()
        self.assertEqual(state.directory, str(self.base.resolve()))
        self.assertEqual(state.selected().name, "sub")

    def test_toggle_hidden_refreshes(self):
        state = smart_ls.SmartLsState(self.base)
        state.toggle_hidden()
        self.assertIn(".hidden", [e.name for e in state.entries])

    def test_set_sort_toggles_back_to_name(self):
        state = smart_ls.SmartLsState(self.base)
        state.set_sort("mtime")
        self.assertEqual(state.sort_key, "mtime")
        state.set_sort("mtime")
        self.assertEqual(state.sort_key, "name")
        state.set_sort("mtime")
        state.set_sort("size")
        self.assertEqual(state.sort_key, "size")

    def test_cycle_truncation_order(self):
        state = smart_ls.SmartLsState(self.base)
        seen = [state.trunc_mode]
        for _ in range(3):
            state.cycle_truncation()
            seen.append(state.trunc_mode)
        self.assertEqual(seen, ["end", "middle", "start", "end"])


if __name__ == "__main__":
    unittest.main()
