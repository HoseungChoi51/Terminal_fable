"""Unit-level contracts for the native terminal.

These tests run without a GUI session: they cover launch options, the
pure layout engine, Markdown helpers, image helpers, the action surface,
shortcuts, the control socket protocol, and source-level implementation
guardrails.
"""

import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from agent_terminal import native_terminal as nt

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = (REPO_ROOT / "agent_terminal" / "native_terminal.py").read_text(
    encoding="utf-8")


class OptionTests(unittest.TestCase):
    def test_defaults(self):
        options = nt.parse_args([])
        self.assertIsNone(options.working_directory)
        self.assertIsNone(options.command)
        self.assertFalse(options.hold_on_exit)
        self.assertEqual(options.settings.font_family, "Monospace")
        self.assertEqual(options.settings.scrollback_lines, 10_000)
        self.assertTrue(options.settings.cursor_blink)
        self.assertEqual(options.settings.cursor_style, "block")
        self.assertEqual(options.settings.palette, "agent-dark")
        self.assertEqual(options.native_config.pane_close_policy,
                         "adjacent_expand")

    def test_flags(self):
        options = nt.parse_args([
            "--working-directory", "/tmp",
            "--command", "bash -lc 'echo ok'",
            "--title", "Demo",
            "--hold-on-exit",
            "--font-family", "Fira Code",
            "--font-size", "14",
            "--scrollback-lines", "500",
            "--cursor-style", "ibeam",
            "--no-cursor-blink",
            "--palette", "solarized-dark",
            "--markdown", "README.md",
            "--image", "shot.png",
            "--control-socket", "/tmp/test.sock",
        ])
        self.assertEqual(options.working_directory, "/tmp")
        self.assertTrue(options.hold_on_exit)
        self.assertEqual(options.title, "Demo")
        self.assertEqual(options.settings.font_family, "Fira Code")
        self.assertEqual(options.settings.font_size, 14.0)
        self.assertEqual(options.settings.scrollback_lines, 500)
        self.assertEqual(options.settings.cursor_style, "ibeam")
        self.assertFalse(options.settings.cursor_blink)
        self.assertEqual(options.settings.palette, "solarized-dark")
        self.assertEqual(options.markdown_paths, ("README.md",))
        self.assertEqual(options.image_paths, ("shot.png",))
        self.assertEqual(options.control_socket_path, "/tmp/test.sock")

    def test_command_argv_explicit(self):
        options = nt.parse_args(["--command", "bash -lc 'echo ok; exec bash'"])
        self.assertEqual(nt.command_argv(options),
                         ["bash", "-lc", "echo ok; exec bash"])

    def test_command_argv_default_shell(self):
        options = nt.parse_args([])
        argv = nt.command_argv(options)
        self.assertEqual(len(argv), 1)
        self.assertEqual(argv[0], nt.default_shell())

    def test_version_flag_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            nt.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_font_size_clamping(self):
        self.assertEqual(nt.clamp_font_size(1), nt.MIN_FONT_SIZE)
        self.assertEqual(nt.clamp_font_size(500), nt.MAX_FONT_SIZE)
        self.assertEqual(nt.clamp_font_size(12.5), 12.5)
        self.assertEqual(nt.clamp_font_size("bogus"), nt.DEFAULT_FONT_SIZE)

    def test_palette_normalization(self):
        self.assertEqual(nt.normalize_palette("agent-dark"), "agent-dark")
        self.assertEqual(nt.normalize_palette("nope"), nt.DEFAULT_PALETTE)
        self.assertEqual(nt.normalize_palette(None), nt.DEFAULT_PALETTE)
        for palette in nt.PALETTES.values():
            self.assertEqual(len(palette["colors"]), 16)
            self.assertTrue(palette["foreground"].startswith("#"))
            self.assertTrue(palette["background"].startswith("#"))

    def test_dependency_hint_names_packages(self):
        for package in ("python3-gi", "gir1.2-gtk-4.0", "gir1.2-vte-3.91"):
            self.assertIn(package, nt.DEPENDENCY_HINT)

    def test_url_regex_flags_include_multiline(self):
        self.assertTrue(nt.URL_REGEX_FLAGS & nt.PCRE2_MULTILINE)
        self.assertTrue(nt.SEARCH_REGEX_FLAGS & nt.PCRE2_MULTILINE)

    def test_config_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "native.json"
            config_path.write_text(json.dumps({
                "pane_close_policy": "same_axis_reflow",
                "palette": "agent-light",
            }))
            config = nt.load_native_config(config_path)
            self.assertEqual(config.pane_close_policy, "same_axis_reflow")
            self.assertEqual(config.palette, "agent-light")

    def test_config_rejects_unknown_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "native.json"
            config_path.write_text(json.dumps({
                "pane_close_policy": "explode",
                "palette": "neon",
            }))
            config = nt.load_native_config(config_path)
            self.assertEqual(config.pane_close_policy, nt.DEFAULT_CLOSE_POLICY)
            self.assertEqual(config.palette, nt.DEFAULT_PALETTE)

    def test_config_missing_file(self):
        config = nt.load_native_config("/nonexistent/native.json")
        self.assertEqual(config, nt.NativeConfig())

    def test_config_assistant_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "native.json"
            config_path.write_text(json.dumps({
                "assistant": {"shell_integration": False,
                              "journal": {"max_commands": 12}},
            }))
            config = nt.load_native_config(config_path)
            self.assertTrue(config.assistant.enabled)
            self.assertFalse(config.assistant.shell_integration)
            self.assertEqual(config.assistant.journal.max_commands, 12)
        # defaults when the section is absent or malformed
        self.assertTrue(nt.NativeConfig().assistant.enabled)
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "native.json"
            config_path.write_text(json.dumps({"assistant": "bogus"}))
            config = nt.load_native_config(config_path)
            self.assertTrue(config.assistant.shell_integration)

    def test_control_socket_path_is_process_local(self):
        path = nt.default_control_socket_path(1234)
        self.assertIn("agent-terminal-native-1234.sock", path)


def grid_2x2():
    """h-split of (a, v-split of (b, c)) plus d on the right."""
    root = nt.layout_leaf("a")
    root = nt.layout_split_node(root, "a", nt.HORIZONTAL, "b")
    root = nt.layout_split_node(root, "b", nt.VERTICAL, "c")
    root = nt.layout_split_node(root, "a", nt.VERTICAL, "d")
    return root


class LayoutTests(unittest.TestCase):
    def test_same_axis_splits_flatten(self):
        root = nt.layout_leaf("a")
        root = nt.layout_split_node(root, "a", nt.HORIZONTAL, "b")
        root = nt.layout_split_node(root, "b", nt.HORIZONTAL, "c")
        self.assertEqual(root.kind, "split")
        self.assertEqual(root.orientation, nt.HORIZONTAL)
        self.assertEqual(len(root.children), 3)
        self.assertTrue(all(child.kind == "leaf" for child in root.children))
        self.assertEqual(nt.layout_leaf_ids(root), ("a", "b", "c"))

    def test_split_weights_normalized(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")],
                                     [3.0, 1.0])
        self.assertAlmostEqual(sum(root.weights), 1.0)
        self.assertAlmostEqual(root.weights[0], 0.75)

    def test_pane_count_and_contains(self):
        root = grid_2x2()
        self.assertEqual(nt.layout_pane_count(root), 4)
        self.assertTrue(nt.layout_contains(root, "c"))
        self.assertFalse(nt.layout_contains(root, "zz"))

    def test_remove_leaf_adjacent_expand_preserves_unrelated(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")],
            [0.5, 0.3, 0.2])
        result = nt.layout_remove_leaf(root, "b", "adjacent_expand")
        self.assertEqual(nt.layout_leaf_ids(result), ("a", "c"))
        self.assertAlmostEqual(result.weights[0], 0.8)
        self.assertAlmostEqual(result.weights[1], 0.2)

    def test_remove_leaf_same_axis_reflow(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")],
            [0.5, 0.3, 0.2])
        result = nt.layout_remove_leaf(root, "b", "same_axis_reflow")
        self.assertEqual(nt.layout_leaf_ids(result), ("a", "c"))
        self.assertAlmostEqual(result.weights[0] / result.weights[1],
                               0.5 / 0.2, places=5)

    def test_remove_last_leaf_returns_none(self):
        self.assertIsNone(nt.layout_remove_leaf(nt.layout_leaf("a"), "a"))

    def test_remove_collapses_single_child_split(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        result = nt.layout_remove_leaf(root, "a")
        self.assertEqual(result.kind, "leaf")
        self.assertEqual(result.pane_id, "b")

    def test_focus_target_geometry(self):
        root = grid_2x2()
        # Layout: left column (a over d), right column (b over c).
        self.assertEqual(nt.layout_focus_target(root, "a", "right"), "b")
        self.assertEqual(nt.layout_focus_target(root, "a", "down"), "d")
        self.assertEqual(nt.layout_focus_target(root, "c", "left"), "d")
        self.assertEqual(nt.layout_focus_target(root, "c", "up"), "b")
        self.assertIsNone(nt.layout_focus_target(root, "a", "left"))
        self.assertIsNone(nt.layout_focus_target(root, "b", "up"))

    def test_relative_rects_cover_unit_square(self):
        root = grid_2x2()
        rects = nt.layout_rects(root)
        area = sum(r.width * r.height for r in rects.values())
        self.assertAlmostEqual(area, 1.0)

    def test_pixel_rects_respect_minimums(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b")],
            [0.95, 0.05])
        rects = nt.layout_pixel_rects(root, 1000, 600)
        self.assertGreaterEqual(rects["b"].width, nt.MIN_PANE_WIDTH)
        total = rects["a"].width + rects["b"].width + nt.PANE_GAP
        self.assertEqual(total, 1000)

    def test_split_boundaries_between_children(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        boundaries = nt.layout_split_boundaries(root, 1000, 600)
        self.assertEqual(len(boundaries), 1)
        boundary = boundaries[0]
        self.assertEqual(boundary.orientation, nt.HORIZONTAL)
        self.assertEqual(boundary.width, nt.PANE_GAP)
        self.assertEqual(boundary.height, 600)
        self.assertEqual(boundary.index, 0)

    def test_boundary_hit_testing(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        boundaries = nt.layout_split_boundaries(root, 1000, 600)
        boundary = boundaries[0]
        hit = nt.layout_boundary_at(boundaries, boundary.x + 1, 50)
        self.assertEqual(hit, boundary)
        self.assertIsNone(nt.layout_boundary_at(boundaries, 5, 5))

    def test_update_split_ratio_preserves_unrelated_weights(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")],
            [0.4, 0.3, 0.3])
        result = nt.layout_update_split_ratio(root, root.split_id, 0, 0.5)
        self.assertAlmostEqual(result.weights[0], 0.5)
        self.assertAlmostEqual(result.weights[1], 0.2)
        self.assertAlmostEqual(result.weights[2], 0.3)
        self.assertAlmostEqual(sum(result.weights), 1.0)

    def test_update_split_ratio_clamps_minimum(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        result = nt.layout_update_split_ratio(root, root.split_id, 0, 0.0)
        self.assertGreaterEqual(result.weights[0], nt.MIN_SPLIT_WEIGHT - 1e-9)

    def test_boundary_drag_ratio(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        boundary = nt.layout_split_boundaries(root, 1000, 600)[0]
        result = nt.layout_update_split_boundary_ratio(root, boundary, 250)
        self.assertAlmostEqual(result.weights[0], 0.25, places=2)

    def test_resize_nearest_split(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        result, changed = nt.layout_resize_nearest_split_result(root, "a",
                                                                "right")
        self.assertTrue(changed)
        self.assertGreater(result.weights[0], root.weights[0])
        result, changed = nt.layout_resize_nearest_split_result(root, "a",
                                                                "left")
        self.assertTrue(changed)
        self.assertLess(result.weights[0], root.weights[0])

    def test_resize_no_matching_axis(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        result, changed = nt.layout_resize_nearest_split_result(root, "a",
                                                                "up")
        self.assertFalse(changed)
        self.assertEqual(result, root)

    def test_grow_from_side(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")])
        grown = nt.layout_grow(root, "b", "left")
        self.assertGreater(grown.weights[1], root.weights[1])
        self.assertLess(grown.weights[0], root.weights[0])
        self.assertAlmostEqual(grown.weights[2], root.weights[2])

    def test_resize_centered(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")])
        grown = nt.layout_resize_centered(root, "b", nt.HORIZONTAL, 0.1)
        self.assertGreater(grown.weights[1], root.weights[1])
        shrunk = nt.layout_resize_centered(root, "b", nt.HORIZONTAL, -0.1)
        self.assertLess(shrunk.weights[1], root.weights[1])

    def test_swap_panes(self):
        root = grid_2x2()
        swapped = nt.layout_swap_panes(root, "a", "c")
        rects = nt.layout_rects(root)
        swapped_rects = nt.layout_rects(swapped)
        self.assertEqual(rects["a"], swapped_rects["c"])
        self.assertEqual(rects["c"], swapped_rects["a"])
        self.assertEqual(swapped_rects["b"], rects["b"])

    def test_swap_missing_pane_is_noop(self):
        root = grid_2x2()
        self.assertEqual(nt.layout_swap_panes(root, "a", "zz"), root)

    def test_move_pane_near(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        root = nt.layout_split_node(root, "b", nt.VERTICAL, "c")
        moved = nt.layout_move_pane_near(root, "c", "left")
        self.assertEqual(set(nt.layout_leaf_ids(moved)), {"a", "b", "c"})
        # c moves past its left neighbor a and becomes the leftmost pane.
        self.assertEqual(nt.layout_focus_target(moved, "c", "right"), "a")
        self.assertIsNone(nt.layout_focus_target(moved, "c", "left"))

    def test_move_pane_without_neighbor_is_noop(self):
        root = nt.layout_split_group(nt.HORIZONTAL,
                                     [nt.layout_leaf("a"), nt.layout_leaf("b")])
        self.assertEqual(nt.layout_move_pane_near(root, "a", "left"), root)

    def test_balance_splits(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")],
            [0.7, 0.2, 0.1])
        balanced = nt.layout_balance_splits(root)
        for weight in balanced.weights:
            self.assertAlmostEqual(weight, 1.0 / 3.0)

    def test_balance_local_touches_only_parent(self):
        outer = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"),
             nt.layout_split_group(nt.VERTICAL,
                                   [nt.layout_leaf("b"), nt.layout_leaf("c")],
                                   [0.8, 0.2])],
            [0.7, 0.3])
        balanced = nt.layout_balance_local(outer, "b")
        self.assertAlmostEqual(balanced.weights[0], 0.7)
        inner = balanced.children[1]
        self.assertAlmostEqual(inner.weights[0], 0.5)

    def test_balance_axis(self):
        outer = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"),
             nt.layout_split_group(nt.VERTICAL,
                                   [nt.layout_leaf("b"), nt.layout_leaf("c")],
                                   [0.8, 0.2])],
            [0.7, 0.3])
        balanced = nt.layout_balance_axis(outer, nt.HORIZONTAL)
        self.assertAlmostEqual(balanced.weights[0], 0.5)
        inner = balanced.children[1]
        self.assertAlmostEqual(inner.weights[0], 0.8)

    def test_balance_weighted(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")])
        weighted = nt.layout_balance_weighted(root, "b", factor=2.0)
        self.assertAlmostEqual(weighted.weights[1], 0.5)
        self.assertAlmostEqual(weighted.weights[0], 0.25)

    def test_balance_tidy_equalizes_leaf_area(self):
        outer = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"),
             nt.layout_split_group(nt.VERTICAL,
                                   [nt.layout_leaf("b"), nt.layout_leaf("c")])],
            [0.5, 0.5])
        tidy = nt.layout_balance_tidy(outer)
        rects = nt.layout_rects(tidy)
        areas = {pane: r.width * r.height for pane, r in rects.items()}
        self.assertAlmostEqual(areas["a"], areas["b"])
        self.assertAlmostEqual(areas["b"], areas["c"])

    def test_fit_focused_does_not_mutate_base(self):
        root = grid_2x2()
        fitted = nt.layout_fit_focused(root, "c")
        self.assertNotEqual(fitted, root)
        rects = nt.layout_rects(fitted)
        base_rects = nt.layout_rects(root)
        self.assertGreater(rects["c"].width * rects["c"].height,
                           base_rects["c"].width * base_rects["c"].height)
        # base root unchanged (frozen dataclasses, fresh object)
        self.assertEqual(nt.layout_rects(root), base_rects)

    def test_fit_focused_share(self):
        root = nt.layout_split_group(
            nt.HORIZONTAL,
            [nt.layout_leaf("a"), nt.layout_leaf("b"), nt.layout_leaf("c")])
        fitted = nt.layout_fit_focused(root, "b", share=0.6)
        self.assertAlmostEqual(fitted.weights[1], 0.6)

    def test_layout_snapshot_equality(self):
        root = grid_2x2()
        self.assertEqual(nt.LayoutSnapshot(root, "a"),
                         nt.LayoutSnapshot(root, "a"))
        self.assertNotEqual(nt.LayoutSnapshot(root, "a"),
                            nt.LayoutSnapshot(root, "b"))


class MarkdownTests(unittest.TestCase):
    def test_block_coverage(self):
        text = "\n".join([
            "# Title",
            "",
            "A paragraph with **bold** text.",
            "",
            "> quoted",
            "",
            "```python",
            "print('hi')",
            "```",
            "",
            "---",
            "",
            "- one",
            "- two",
            "",
            "1. first",
            "2. second",
            "",
            "- [ ] todo",
            "- [x] done",
            "",
            "| h1 | h2 |",
            "| -- | -- |",
            "| a  | b  |",
        ])
        kinds = [block.kind for block in nt.parse_markdown_blocks(text)]
        self.assertEqual(kinds, ["heading", "paragraph", "blockquote", "code",
                                 "rule", "ulist", "olist", "tasklist",
                                 "table"])

    def test_heading_levels(self):
        blocks = nt.parse_markdown_blocks("### Third")
        self.assertEqual(blocks[0].level, 3)
        self.assertEqual(blocks[0].text, "Third")

    def test_fenced_code_language(self):
        blocks = nt.parse_markdown_blocks("```bash\necho hi\n```")
        self.assertEqual(blocks[0].language, "bash")
        self.assertEqual(blocks[0].text, "echo hi")

    def test_indented_code(self):
        blocks = nt.parse_markdown_blocks("    indented code")
        self.assertEqual(blocks[0].kind, "code")
        self.assertEqual(blocks[0].text, "indented code")

    def test_task_items(self):
        blocks = nt.parse_markdown_blocks("- [x] done\n- [ ] todo")
        self.assertEqual(blocks[0].items, ((True, "done"), (False, "todo")))

    def test_table_rows(self):
        blocks = nt.parse_markdown_blocks("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertEqual(blocks[0].rows, (("a", "b"), ("1", "2")))

    def test_inline_escaping(self):
        markup = nt.markdown_inline_to_pango("a < b & **c**")
        self.assertEqual(markup, "a &lt; b &amp; <b>c</b>")

    def test_inline_code_escaped(self):
        markup = nt.markdown_inline_to_pango("run `x < y` now")
        self.assertIn("<tt>x &lt; y</tt>", markup)

    def test_inline_link(self):
        markup = nt.markdown_inline_to_pango("see [docs](http://example.com)")
        self.assertIn('<a href="http://example.com">docs</a>', markup)

    def test_inline_italic(self):
        markup = nt.markdown_inline_to_pango("an *italic* word")
        self.assertIn("<i>italic</i>", markup)

    def test_highlight_wraps_matches(self):
        markup = nt.markdown_inline_to_pango("find the needle here",
                                             highlight="needle")
        self.assertIn(nt.HIGHLIGHT_SPAN + "needle</span>", markup)

    def test_heading_point_sizes_descend(self):
        sizes = [nt.heading_point_size(level) for level in range(1, 7)]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertGreater(nt.heading_point_size(1, zoom=2.0),
                           nt.heading_point_size(1))

    def test_list_item_prefix(self):
        self.assertEqual(nt.list_item_prefix(3, True), "3. ")
        self.assertEqual(nt.list_item_prefix(1, False), "• ")

    def test_resolve_local_link_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "doc.md"
            base.write_text("# base")
            other = Path(tmp) / "other.md"
            other.write_text("# other")
            image = Path(tmp) / "shot.png"
            image.write_bytes(b"\x89PNG")
            script = Path(tmp) / "run.sh"
            script.write_text("#!/bin/sh")
            self.assertEqual(nt.resolve_local_link(base, "other.md"),
                             str(other.resolve()))
            self.assertEqual(nt.resolve_local_link(base, "shot.png"),
                             str(image.resolve()))
            self.assertIsNone(nt.resolve_local_link(base, "run.sh"))
            self.assertIsNone(nt.resolve_local_link(base, "missing.md"))
            self.assertIsNone(nt.resolve_local_link(base,
                                                    "http://example.com/x.md"))
            self.assertIsNone(nt.resolve_local_link(base, "#anchor"))
            self.assertIsNone(nt.resolve_local_link(base, "mailto:a@b.c"))

    def test_safe_external_uri(self):
        self.assertTrue(nt.is_safe_external_uri("https://example.com"))
        self.assertTrue(nt.is_safe_external_uri("mailto:a@b.c"))
        self.assertFalse(nt.is_safe_external_uri("javascript:alert(1)"))
        self.assertFalse(nt.is_safe_external_uri("plain text"))

    def test_markdown_path_recognition(self):
        self.assertTrue(nt.is_markdown_path("README.md"))
        self.assertTrue(nt.is_markdown_path("notes.MARKDOWN"))
        self.assertFalse(nt.is_markdown_path("script.py"))


class ImageTests(unittest.TestCase):
    def test_extension_recognition(self):
        for name in ("a.png", "b.JPG", "c.jpeg", "d.gif", "e.webp"):
            self.assertTrue(nt.is_image_path(name))
        self.assertFalse(nt.is_image_path("vector.svg"))
        self.assertFalse(nt.is_image_path("doc.md"))

    def test_fit_scale_bounds(self):
        self.assertAlmostEqual(nt.fit_image_scale(200, 100, 100, 100), 0.5)
        self.assertAlmostEqual(nt.fit_image_scale(10, 10, 100, 50), 5.0)
        self.assertEqual(nt.fit_image_scale(0, 100, 100, 100), 1.0)
        self.assertLessEqual(nt.fit_image_scale(1, 1, 10**6, 10**6),
                             nt.MAX_IMAGE_SCALE)

    def test_scale_clamping(self):
        self.assertEqual(nt.clamp_image_scale(0.0001), nt.MIN_IMAGE_SCALE)
        self.assertEqual(nt.clamp_image_scale(1000), nt.MAX_IMAGE_SCALE)
        self.assertEqual(nt.clamp_image_scale("bad"), 1.0)


class ActionTests(unittest.TestCase):
    REQUIRED_ACTIONS = (
        "new-window", "new-tab", "open-file", "open-markdown", "open-image",
        "close-active", "close-pane", "next-tab", "previous-tab",
        "split-horizontal", "split-vertical",
        "focus-left", "focus-right", "focus-up", "focus-down",
        "fit-focused", "zoom-pane", "pane-leader",
        "move-border-left", "move-border-right", "move-border-up",
        "move-border-down",
        "grow-left", "grow-right", "grow-up", "grow-down",
        "increase-width", "decrease-width", "increase-height",
        "decrease-height",
        "undo-layout", "redo-layout",
        "copy", "paste", "select-all", "find", "find-next", "find-previous",
        "reset", "reload-pane", "clear-scrollback",
        "zoom-in", "zoom-out", "zoom-reset",
        "copilot-menu", "copilot-ask", "copilot-pause", "copilot-sessions",
        "copilot-debug",
        "shortcuts", "preferences", "quit",
    )

    def test_action_names_contain_public_actions(self):
        for name in self.REQUIRED_ACTIONS:
            self.assertIn(name, nt.ACTION_NAMES)

    def test_accelerator_actions_are_known(self):
        known = set(nt.ACTION_NAMES) | set(nt.TAB_ACTION_NAMES)
        for action in nt.ACCELERATORS:
            self.assertIn(action, known)

    def test_required_shortcuts(self):
        self.assertIn("<Ctrl><Shift>t", nt.ACCELERATORS["new-tab"])
        self.assertIn("<Ctrl><Shift>o", nt.ACCELERATORS["open-file"])
        self.assertIn("<Ctrl><Shift>w", nt.ACCELERATORS["close-active"])
        self.assertIn("<Alt><Shift>h", nt.ACCELERATORS["split-horizontal"])
        self.assertIn("<Alt><Shift>v", nt.ACCELERATORS["split-vertical"])
        self.assertIn("<Alt><Shift>Left", nt.ACCELERATORS["focus-left"])
        self.assertIn("<Alt><Shift>f", nt.ACCELERATORS["fit-focused"])
        self.assertIn("<Alt><Shift>space", nt.ACCELERATORS["pane-leader"])
        self.assertIn("<Ctrl><Shift>c", nt.ACCELERATORS["copy"])
        self.assertIn("<Ctrl><Shift>v", nt.ACCELERATORS["paste"])
        self.assertIn("<Ctrl><Shift>f", nt.ACCELERATORS["find"])
        self.assertIn("F5", nt.ACCELERATORS["reload-pane"])
        self.assertIn("<Ctrl><Shift>s", nt.ACCELERATORS["copilot-sessions"])
        self.assertIn("<Ctrl><Shift>space", nt.ACCELERATORS["copilot-menu"])
        self.assertIn("<Ctrl>question", nt.ACCELERATORS["copilot-ask"])
        self.assertIn("<Ctrl><Shift>slash", nt.ACCELERATORS["copilot-ask"])
        self.assertIn("<Alt><Shift>a", nt.ACCELERATORS["copilot-pause"])

    def test_copilot_accelerators_not_reserved(self):
        for action in ("copilot-menu", "copilot-pause", "copilot-sessions"):
            for accel in nt.ACCELERATORS[action]:
                self.assertNotIn(accel, nt.RESERVED_PLAIN_ACCELERATORS)

    def test_copilot_session_shortcut_is_unique(self):
        seen = {}
        for action, accels in nt.ACCELERATORS.items():
            for accel in accels:
                self.assertNotIn(accel, seen,
                                 f"{accel} on {action} and {seen.get(accel)}")
                seen[accel] = action
        self.assertIn("<Ctrl><Shift>h", nt.ACCELERATORS["shortcuts"])
        self.assertIn("F1", nt.ACCELERATORS["shortcuts"])

    def test_shortcuts_do_not_steal_terminal_keys(self):
        all_accels = {accel for accels in nt.ACCELERATORS.values()
                      for accel in accels}
        for reserved in nt.RESERVED_PLAIN_ACCELERATORS:
            self.assertNotIn(reserved, all_accels)
        # No plain single-modifier letter bindings that shells need, and no
        # GNOME workspace Super bindings.
        for accel in all_accels:
            self.assertNotIn("<Super>", accel)
            self.assertIsNone(
                re.fullmatch(r"<(Ctrl|Primary|Control)>[a-z]", accel),
                f"{accel} would steal a plain terminal key")

    def test_pane_behavior_surface(self):
        for method in ("focus", "copy", "paste", "select_all", "reset",
                       "clear_scrollback", "reload", "set_search",
                       "find_next", "find_previous", "zoom_in", "zoom_out",
                       "zoom_reset"):
            self.assertIn(method, nt.PANE_BEHAVIOR_METHODS)
            self.assertIn(f"def {method}", SOURCE)


class ControlSocketTests(unittest.TestCase):
    def test_parse_valid_message(self):
        message = nt.parse_control_message(b'{"action":"open-file","path":"/x"}')
        self.assertEqual(message, {"action": "open-file", "path": "/x"})

    def test_parse_rejects_invalid(self):
        self.assertIsNone(nt.parse_control_message(b"not json"))
        self.assertIsNone(nt.parse_control_message(b"[]"))
        self.assertIsNone(nt.parse_control_message(b'{"path":"/x"}'))
        self.assertIsNone(nt.parse_control_message(b'{"action":1}'))
        self.assertIsNone(nt.parse_control_message(b'{"action":"x","path":1}'))
        self.assertIsNone(nt.parse_control_message(b""))

    def test_encode_round_trip(self):
        message = {"action": "open-file", "path": "/tmp/a.md"}
        encoded = nt.encode_control_message(message)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(nt.parse_control_message(encoded), message)

    def test_server_receives_message(self):
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            socket_path = os.path.join(tmp, "control.sock")
            received = []
            done = threading.Event()

            def dispatch(message):
                received.append(message)
                done.set()

            server = nt.ControlSocketServer(socket_path, dispatch)
            try:
                from agent_terminal import tui_navigation as tui
                ok = tui.send_control_message(
                    socket_path, {"action": "open-file", "path": "/tmp/x.md"})
                self.assertTrue(ok)
                self.assertTrue(done.wait(timeout=5.0))
                self.assertEqual(received,
                                 [{"action": "open-file", "path": "/tmp/x.md"}])
            finally:
                server.close()
            self.assertFalse(os.path.exists(socket_path))


class SourceGuardrailTests(unittest.TestCase):
    """Implementation guardrails enforced on the module source."""

    def test_no_builtin_paned_or_fixed_containers(self):
        forbidden_paned = "Gtk." + "Paned"
        forbidden_fixed = "Gtk." + "Fixed"
        self.assertNotIn(forbidden_paned, SOURCE)
        self.assertNotIn(forbidden_fixed, SOURCE)

    def test_custom_widget_allocation(self):
        self.assertIn("def do_size_allocate(self, width, height, baseline)",
                      SOURCE)
        self.assertIn("def do_measure(self, orientation, for_size)", SOURCE)
        self.assertIn("child.allocate(", SOURCE)
        self.assertIn("Gsk.Transform", SOURCE)
        self.assertIn("Graphene.Point", SOURCE)

    def test_terminal_uses_vte(self):
        self.assertIn("Vte.Terminal()", SOURCE)
        self.assertIn("spawn_async", SOURCE)
        # The control-socket env var now lives in tui_core; the terminal still
        # exports it to child processes (re-exported name used in the spawn).
        self.assertEqual(nt.CONTROL_SOCKET_ENV,
                         "AGENT_TERMINAL_NATIVE_CONTROL_SOCKET")
        self.assertIn("env[CONTROL_SOCKET_ENV] = control_socket_path", SOURCE)

    def test_picker_window_is_transient(self):
        self.assertIn("set_transient_for(", SOURCE)
        self.assertIn("tui_navigation", SOURCE)

    def test_image_pane_uses_native_stack(self):
        self.assertIn("GdkPixbuf.Pixbuf.new_from_file", SOURCE)
        self.assertIn("Gtk.Picture", SOURCE)

    def test_markdown_pane_is_passive(self):
        self.assertIn("set_selectable(False)", SOURCE)
        self.assertIn("set_focusable(False)", SOURCE)

    def test_shortcut_guide_has_dismiss_control(self):
        self.assertIn('Gtk.Button(label="Close")', SOURCE)

    def test_only_one_guarded_submit_byte(self):
        # Ask mode is the sole place a submit byte (CR) is fed to a shell,
        # and only under `if run:` (auto-pilot cleared it). Nothing else in
        # the app may press Enter for the user.
        cr = 'insert_text("\\r")'
        self.assertEqual(SOURCE.count(cr), 1,
                         "exactly one carriage-return feed expected")
        self.assertIn('if run:\n                pane.insert_text("\\r")',
                      SOURCE)
        self.assertNotIn('insert_text("\\n")', SOURCE)

    def test_ask_take_goes_through_can_take(self):
        # The take path must consult the single-line/risk gate before it
        # ever inserts a command.
        self.assertIn("copilot_ask.can_take(", SOURCE)
        self.assertIn("def _ask_commit(self, pane, command, run)", SOURCE)

    def test_answer_focus_yields_to_typeahead(self):
        # Focus moves to the answer card only when the entry is not focused
        # (matching hotkeys_armed), so a follow-up being typed is never
        # yanked into the Y/N/T hotkeys and click-away keystrokes stay
        # captured rather than reaching the shell.
        self.assertIn('state["card"] is not None and not entry.has_focus()',
                      SOURCE)

    def test_ask_bar_is_non_modal_revealer(self):
        # Ask mode is an in-window revealer, never a modal popover that could
        # grab the window or trap keystrokes (the reported bug).
        self.assertIn("self._ask_revealer.set_reveal_child(True)", SOURCE)
        self.assertIn("def close_ask()", SOURCE)
        # parking the shell line still only when an endpoint is eligible
        self.assertIn("if eligible and ask_cfg.carry_draft", SOURCE)
        # the ask surface no longer creates a modal popover
        self.assertNotIn("_ask_popover", SOURCE)
        # a tab switch dismisses the window-level bar (no stale-pane ops)
        self.assertIn("def _on_switch_page(self, notebook, page_widget",
                      SOURCE)
        switch = SOURCE.split("def _on_switch_page(", 1)[1][:500]
        self.assertIn("self._ask_close()", switch)

    def test_status_bar_and_model_picker_wired(self):
        self.assertIn("copilot-model", nt.ACTION_NAMES)
        self.assertIn("<Ctrl><Shift>m", nt.ACCELERATORS["copilot-model"])
        self.assertIn("def _build_status_bar(self)", SOURCE)
        self.assertIn("def _copilot_status_text(self", SOURCE)
        self.assertIn("def show_model_picker(self)", SOURCE)
        self.assertIn('"win.copilot-model"', SOURCE)

    def test_persistent_pane_spawn_wired(self):
        # Persistence routes the shell through the ptyd attach client (opt-in,
        # fails open); reattach skips --create.
        self.assertIn("from agent_terminal import ptyd", SOURCE)
        self.assertIn("class PersistenceConfig", SOURCE)
        self.assertIn("def ptyd_attach_argv(session_id, shell_argv, cwd",
                      SOURCE)
        self.assertIn("argv = ptyd_attach_argv(", SOURCE)
        self.assertIn("create=not self.reattach", SOURCE)
        # only the default shell is persisted, and any failure falls back to a
        # direct spawn
        spawn = SOURCE.split("def _spawn(self", 1)[1].split(
            "spawn_async", 1)[0]
        self.assertIn("command is None and persistence is not None", spawn)
        self.assertIn("except Exception:", spawn)      # fail open

    def test_reattach_and_detach_wired(self):
        # Detachable sessions surface for reattach/kill; detach closes the
        # frontend but leaves the process; a startup nudge points at them.
        self.assertIn("def _orphaned_sessions(self)", SOURCE)
        self.assertIn("def _reattach_session(self, info)", SOURCE)
        self.assertIn("def detach_active_pane(self)", SOURCE)
        self.assertIn("def _maybe_prompt_reattach(self)", SOURCE)
        self.assertIn("ptyd.list_sessions()", SOURCE)
        self.assertIn("ptyd.kill_session(info.id)", SOURCE)
        self.assertIn("pane-detach", nt.ACTION_NAMES)
        self.assertIn("add_terminal_tab(working_directory=cwd, "
                      "session_id=info.id", SOURCE)
        # a reattach must still route through ptyd even if the default is off
        self.assertIn("persistence.enabled or self.reattach", SOURCE)

    def test_naming_wired(self):
        # Manual rename + LLM name suggestions; a name overrides the inferred
        # title, and the naming context is the redacted digest (not raw).
        self.assertIn("def rename(self, name):", SOURCE)              # pane
        self.assertIn("def rename_window(self, name):", SOURCE)
        self.assertIn("def rename_active_pane(self)", SOURCE)
        self.assertIn("def show_name_workspace(self)", SOURCE)
        self.assertIn("copilot_llm.suggest_names(", SOURCE)
        self.assertIn("pane-rename", nt.ACTION_NAMES)
        self.assertIn("window-rename", nt.ACTION_NAMES)
        self.assertIn("workspace-name", nt.ACTION_NAMES)
        self.assertIn("F2", nt.ACCELERATORS["pane-rename"])
        # the manual/LLM name wins over the inferred title in every setter
        # (the copilot-inferred path, the VTE-title path, and the flush path)
        self.assertGreaterEqual(
            SOURCE.count("if self._name_override is not None:"), 2)
        self.assertIn("and self._name_override is None", SOURCE)
        # naming feeds the model the redacted digest, not raw output
        self.assertIn('output_mode="digest", budget_chars=800', SOURCE)
        # suggestions are applied only from the Apply button (never silently)
        self.assertIn("def _show_name_dialog(self, panes, result)", SOURCE)

    def test_live_pane_move_wired(self):
        # Moving a pane between windows must reparent it alive: detach without
        # disposing (that would kill the process), then adopt with rebound
        # window callbacks.
        self.assertIn("def detach_pane(self, pane_id)", SOURCE)
        self.assertIn("def adopt_pane(self, pane, orientation=HORIZONTAL)",
                      SOURCE)
        self.assertIn("def eject_active_pane(self)", SOURCE)
        self.assertIn("def send_active_pane_to(self, target)", SOURCE)
        self.assertIn("pane-eject", nt.ACTION_NAMES)
        self.assertIn("pane-send", nt.ACTION_NAMES)
        self.assertIn("<Alt><Shift>e", nt.ACCELERATORS["pane-eject"])
        # detach unparents the widget but never disposes the pane
        detach = SOURCE.split("def detach_pane(self, pane_id):", 1)[1] \
            .split("def adopt_pane", 1)[0]
        self.assertIn("self.container.remove_pane(pane_id)", detach)
        self.assertNotIn("pane.dispose()", detach)
        # adopt rebinds the window-scoped exit callback to the new window
        self.assertIn("pane.on_exited = self.window._on_pane_exited", SOURCE)
        # the tab-click controller is tracked so a move can swap it out
        self.assertIn("pane._tab_click = click", SOURCE)

    def test_workspace_job_restore_wired(self):
        # Detected jobs restore as one bounded split-pane window (packing),
        # with an act-then-revert bar (auto-revert timer + Keep/Revert).
        self.assertIn("def _restore_job(self, job", SOURCE)
        self.assertIn("def _load_workspace(self, plan)", SOURCE)
        self.assertIn("def _show_confirm_revert(self, message, on_revert",
                      SOURCE)
        self.assertIn("copilot_jobs.cluster(", SOURCE)
        self.assertIn("copilot_jobs.pack(", SOURCE)
        self.assertIn("def new_window(self)", SOURCE)
        # act-then-revert: an auto-revert countdown + revert closes what opened
        self.assertIn("GLib.timeout_add_seconds(1, tick)", SOURCE)
        self.assertIn("on_revert=lambda: [w.close() for w in created]", SOURCE)
        # packing is fed the real monitor + cell metrics (the legibility floor)
        self.assertIn("def _workspace_avail_px(self)", SOURCE)
        self.assertIn("def _workspace_cell_px(self)", SOURCE)
        self.assertIn("get_char_width()", SOURCE)

    def test_episode_resume_wired(self):
        # Restoring a session resumes an *episode*: a pane at the episode cwd
        # with a history seed file, passed via extra_env to the spawn.
        self.assertIn("def _restore_episode(self, episode, summary, index",
                      SOURCE)
        self.assertIn("def _write_seed_file(self, episode, tag)", SOURCE)
        self.assertIn("copilot_resume.episodes_of(", SOURCE)
        self.assertIn("copilot_resume.seed_file_content(", SOURCE)
        self.assertIn('"AGENT_TERMINAL_SEED_HISTFILE": seed', SOURCE)
        # the seed reaches the child through the pane spawn env
        self.assertIn("def create_terminal_pane(self, command=None, "
                      "working_directory=None,", SOURCE)
        self.assertIn("extra_env=extra_env", SOURCE)
        # "Restore" resumes the most recent episode; the browser lists the rest
        self.assertIn("self._restore_episode(episodes[-1], summary, "
                      "len(episodes) - 1)", SOURCE)

    def test_digest_mode_ab_toggle_wired(self):
        # The heuristic<->LLM context A/B switch: an action + accelerator, a
        # session override read at submit time, and an injected summarizer
        # that runs on the worker thread (never blocking the UI) and falls
        # back to the heuristic digest.
        self.assertIn("copilot-digest-mode", nt.ACTION_NAMES)
        self.assertIn("<Ctrl><Shift>d", nt.ACCELERATORS["copilot-digest-mode"])
        self.assertIn("def toggle_copilot_digest_mode(self)", SOURCE)
        self.assertIn("def _effective_digest_mode(self, llm_cfg)", SOURCE)
        self.assertIn("self._digest_mode_override", SOURCE)
        # the summarizer is built inside the ask-mode work() (worker thread),
        # not on the GTK thread, because it makes a network call
        submit = SOURCE.split("def submit_question(", 1)[1].split(
            "threading.Thread", 1)[0]
        self.assertIn("def work():", submit)
        self.assertIn("_llm_summarizer(cfg, ch, query)", submit)
        self.assertIn('mode == "llm"', submit)
        # the summarizer itself must degrade to the heuristic digest
        self.assertIn("def _llm_summarizer(cfg, chain, question)", SOURCE)

    def test_ask_mode_sends_digested_episode_activity(self):
        # submit_question must derive the current episode, build the
        # distilled+redacted activity block from it, and pass it to the LLM
        # as `activity=` — never dump raw output. (Guards the wiring the
        # GTK e2e exercises end-to-end.)
        self.assertIn("def _current_episode(self)", SOURCE)
        self.assertIn("copilot_askcontext.build_ask_context(", SOURCE)
        self.assertIn("output_mode=cfg.send_output", SOURCE)
        self.assertIn("activity=activity", SOURCE)
        # the ask bar surfaces the inferred task so a bad segmentation shows
        self.assertIn("episode_now = self._current_episode()", SOURCE)
        self.assertIn('"ask-task"', SOURCE)


class BuildInfoTests(unittest.TestCase):
    def test_reads_current_repo_revision(self):
        info = nt.resolve_build_info(REPO_ROOT)
        self.assertNotEqual(info.branch, "unknown")
        self.assertRegex(info.commit, r"^[0-9a-f]{7,}$")

    def test_non_git_dir_falls_back_to_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = nt.resolve_build_info(tmp)
            self.assertEqual(info.branch, "unknown")
            self.assertEqual(info.commit, "unknown")
            self.assertFalse(info.dirty)

    def test_describe(self):
        self.assertEqual(nt.BuildInfo("main", "abc1234").describe(),
                         "main @ abc1234")
        self.assertIn("+dirty",
                      nt.BuildInfo("x", "y", dirty=True).describe())
        self.assertIn("unknown", nt.BuildInfo().describe())

    def test_about_action_wired(self):
        self.assertIn("about", nt.ACTION_NAMES)
        self.assertIn('"win.about"', SOURCE)
        self.assertIn("def show_about(self)", SOURCE)


class PackagingTests(unittest.TestCase):
    def test_launcher_exists_and_is_executable(self):
        launcher = REPO_ROOT / "bin" / "agent-terminal-native"
        self.assertTrue(launcher.is_file())
        self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
        text = launcher.read_text()
        self.assertIn("python3 -m agent_terminal.native_terminal", text)
        self.assertIn("AGENT_TERMINAL_NATIVE_USE_UV", text)

    def test_install_script(self):
        script = REPO_ROOT / "packaging" / "install.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        text = script.read_text()
        self.assertIn("set -euo pipefail", text)
        self.assertIn(".local/bin", text)

    def test_desktop_entry(self):
        desktop = (REPO_ROOT / "packaging"
                   / "agent-terminal-native.desktop").read_text()
        self.assertIn("Type=Application", desktop)
        self.assertIn("Name=Agent Terminal", desktop)
        self.assertIn("Exec=", desktop)
        self.assertIn("Terminal=false", desktop)


class ShortcutHintDetectorTests(unittest.TestCase):
    def test_below_threshold_stays_quiet(self):
        detector = nt.ShortcutHintDetector(threshold=3, max_gap=3.0)
        self.assertFalse(detector.record(1.0))
        self.assertFalse(detector.record(1.5))

    def test_quick_run_triggers(self):
        detector = nt.ShortcutHintDetector(threshold=3, max_gap=3.0)
        detector.record(1.0)
        detector.record(1.5)
        self.assertTrue(detector.record(2.0))

    def test_pause_resets_the_count(self):
        detector = nt.ShortcutHintDetector(threshold=3, max_gap=3.0)
        detector.record(1.0)
        detector.record(1.5)
        self.assertFalse(detector.record(10.0))
        self.assertFalse(detector.record(10.5))
        self.assertTrue(detector.record(11.0))

    def test_keeps_firing_while_fumbling_continues(self):
        detector = nt.ShortcutHintDetector(threshold=3, max_gap=3.0)
        detector.record(1.0)
        detector.record(1.5)
        self.assertTrue(detector.record(2.0))
        self.assertTrue(detector.record(2.5))

    def test_reset(self):
        detector = nt.ShortcutHintDetector(threshold=3, max_gap=3.0)
        detector.record(1.0)
        detector.record(1.5)
        detector.reset()
        self.assertFalse(detector.record(2.0))

    def test_modifier_keyvals_cover_plain_modifiers(self):
        for name in ("Shift_L", "Control_R", "Alt_L", "Super_L",
                     "Caps_Lock"):
            self.assertIn(name, nt.MODIFIER_KEYVAL_NAMES)
        self.assertNotIn("a", nt.MODIFIER_KEYVAL_NAMES)
        self.assertNotIn("Escape", nt.MODIFIER_KEYVAL_NAMES)

    def test_hint_text_names_the_shortcut(self):
        self.assertIn("Ctrl+Shift+H", nt.SHORTCUT_HINT_TEXT)
        self.assertIn("Esc", nt.SHORTCUT_HINT_TEXT)


class PersistenceConfigTests(unittest.TestCase):
    def _load(self, data):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump(data, handle)
            path = handle.name
        try:
            return nt.load_native_config(path)
        finally:
            os.unlink(path)

    def test_default_off(self):
        cfg = nt.load_native_config("/nonexistent")
        self.assertFalse(cfg.persistence.enabled)
        self.assertEqual(cfg.persistence.scrollback_bytes, 512 * 1024)

    def test_enabled_and_ring(self):
        cfg = self._load({"persistence": {"enabled": True,
                                          "scrollback_bytes": 100000}})
        self.assertTrue(cfg.persistence.enabled)
        self.assertEqual(cfg.persistence.scrollback_bytes, 100000)

    def test_ring_clamped_and_malformed_ignored(self):
        self.assertEqual(self._load(
            {"persistence": {"scrollback_bytes": 10}}
        ).persistence.scrollback_bytes, 4096)
        self.assertEqual(self._load(
            {"persistence": {"scrollback_bytes": "x"}}
        ).persistence.scrollback_bytes, 512 * 1024)
        self.assertFalse(self._load(
            {"persistence": "nope"}).persistence.enabled)

    def test_attach_argv_shape(self):
        argv = nt.ptyd_attach_argv("s-abc", ["/bin/bash", "-i"], "/tmp", 4096)
        self.assertIn("-m", argv)
        self.assertIn("agent_terminal.ptyd", argv)
        self.assertEqual(argv[argv.index("--session") + 1], "s-abc")
        self.assertIn("--create", argv)
        self.assertEqual(argv[-2:], ["/bin/bash", "-i"])
        # reattach form drops --create (and the shell argv)
        re = nt.ptyd_attach_argv("s-abc", ["/bin/bash"], "/tmp", 4096,
                                 create=False)
        self.assertNotIn("--create", re)


if __name__ == "__main__":
    unittest.main()
