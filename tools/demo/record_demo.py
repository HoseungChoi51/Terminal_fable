#!/usr/bin/env python3
"""Record the Terminal Fable demo animation (GIF + animated WebP).

This machine has no input-injection tooling (no xdotool/ydotool/wtype, and
/dev/uinput is root-only), so the demo cannot be "recorded off the screen while
someone drives it".  Instead the app is booted *in process* and driven through
its own public surface: the ``win.`` ``Gio.SimpleAction``s that the keyboard
shortcuts activate, and ``TerminalPane.insert_text()`` -- the same
``feed_child`` path the ask bar's Take button uses.  What you see in the
recording is the real app taking the real actions.

Capture does not scrape the screen either.  ``ffmpeg -f x11grab`` of a screen
region returns black under GNOME's rootless Xwayland (toplevels are
composite-redirected away from the root window).  So each frame is rendered
straight out of the window's own GSK render tree::

    Gtk.WidgetPaintable -> Gtk.Snapshot.to_node()
      -> Gsk.Renderer.render_texture() -> Gdk.TextureDownloader.download_bytes()
      -> raw RGBA on ffmpeg's stdin

That is pixel-exact, costs ~9 ms/frame at 2560x1600, is immune to occlusion
(nothing else on the desktop can leak into a frame), and lets us pick the
output size independently of the monitor's scale factor.  Frames are rendered
at 2x the logical window size purely as supersampling for the downscale.

The one thing it cannot see is anything on its own GdkSurface -- popovers,
dialogs and other windows are separate ``GtkNative``s and are *not* in the
toplevel's render tree.  The storyboard therefore only uses in-window UI.  The
ask bar qualifies: it is a ``Gtk.Revealer`` inside the window, deliberately
"never a modal popover".

Modes::

    record_demo.py --probe          # capture one frame, print timings, exit
    record_demo.py --record         # run the storyboard -> master.mkv + meta.json
    record_demo.py --encode-only    # master.mkv -> demo.gif / demo.webp
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent_terminal import native_terminal as nt   # noqa: E402

# -- capture geometry -------------------------------------------------------

LOGICAL_W, LOGICAL_H = 1280, 800
SUPERSAMPLE = 2          # render at 2x purely as supersampling for the downscale
FPS = 15

# -- deliverables -----------------------------------------------------------

OUT_W = 1000            # width of the GIF/WebP
GIF_FPS = 10            # GIF delays are centiseconds; 10 fps == exactly 10 cs
WEBP_FPS = 15
GIF_COLORS = 192

DEFAULT_WORKDIR = Path(
    os.environ.get("CLAUDE_JOB_DIR", "/tmp")) / "tmp" / "demo-take"

# The answer card, the ask bar and the Take button, by the identities the app
# gives them (native_terminal.py:4436, :4281, :4482).
CARD_CSS_CLASS = "intent-card"
ASK_CSS_CLASS = "copilot-ask"
TAKE_LABEL = "Take (Y)"


# ---------------------------------------------------------------------------
# widget-tree helpers
# ---------------------------------------------------------------------------

def walk(widget):
    """Depth-first over a widget and all of its descendants."""
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def find_by_css(root, css_class):
    for w in walk(root):
        if w.has_css_class(css_class):
            return w
    return None


def find_entry(root, Gtk):
    for w in walk(root):
        if isinstance(w, Gtk.Entry):
            return w
    return None


def find_button(root, Gtk, label):
    for w in walk(root):
        if isinstance(w, Gtk.Button) and w.get_label() == label:
            return w
    return None


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------

class Recorder:
    """Pipes the window's render tree into ffmpeg, one CFR frame at a time."""

    def __init__(self, g, window, out_path, background):
        self.g = g
        self.win = window
        self.out_path = out_path
        self.background = background
        self.paintable = g.Gtk.WidgetPaintable.new(window)
        self.renderer = window.get_native().get_renderer()
        # Capture size follows the window: GTK owns the final geometry (a
        # compositor may not honour set_default_size exactly), and the
        # deliverable is scaled to a fixed width regardless.
        self.cap_w = window.get_width() * SUPERSAMPLE
        self.cap_h = window.get_height() * SUPERSAMPLE
        self.q: queue.Queue = queue.Queue(maxsize=8)
        self.dropped = 0
        self.emitted = 0
        self.suppressed = 0
        self.t0_us = None
        self.last = None
        self.tick_id = None
        self.crop = None
        self.proc = None
        self.writer = None
        self.render_ms = 0.0
        self.download_ms = 0.0

    def _spawn_ffmpeg(self):
        """Crop the CSD shadow and flatten alpha *here*, not at encode time.

        libx264rgb has no alpha channel, so anything left transparent in the
        master would arrive as black.  The overlay must be told the source is
        premultiplied, or it multiplies by alpha a second time and dirties the
        window's antialiased rounded corners.
        """
        box = self.crop
        graph = (
            f"color=c={self.background}:s={box['w']}x{box['h']}[bg];"
            f"[0:v]crop={box['w']}:{box['h']}:{box['x']}:{box['y']}[fg];"
            f"[bg][fg]overlay=alpha=premultiplied:shortest=1,format=rgb24"
        )
        self.proc = subprocess.Popen([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-f", "rawvideo", "-pixel_format", "rgba",
            "-video_size", f"{self.cap_w}x{self.cap_h}", "-framerate", str(FPS),
            "-i", "-",
            "-filter_complex", graph,
            "-an", "-c:v", "libx264rgb", "-qp", "0",
            "-preset", "ultrafast", "-g", str(FPS),
            str(self.out_path),
        ], stdin=subprocess.PIPE)
        self.writer = threading.Thread(target=self._writer, daemon=True)
        self.writer.start()

    # -- frame production ---------------------------------------------------

    def grab(self):
        """One RGBA frame of the window, or None if it is not renderable."""
        g = self.g
        t = time.perf_counter()
        snapshot = g.Gtk.Snapshot.new()
        self.paintable.snapshot(snapshot, self.cap_w, self.cap_h)
        node = snapshot.to_node()
        if node is None:
            # Unmapped window: gtk_widget_snapshot() short-circuits.
            return None
        texture = self.renderer.render_texture(node, None)
        mid = time.perf_counter()
        downloader = g.Gdk.TextureDownloader.new(texture)
        # The renderer's own format: asking for anything else costs a swizzle
        # pass (measured 12.5 ms vs 3.7 ms at 2560x1600).  Premultiplied alpha
        # is handled at encode time by overlay=alpha=premultiplied.
        downloader.set_format(g.Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
        data, stride = downloader.download_bytes()
        end = time.perf_counter()
        self.render_ms = (mid - t) * 1000.0
        self.download_ms = (end - mid) * 1000.0
        if stride != self.cap_w * 4:
            raise RuntimeError(
                f"unexpected stride {stride} (want {self.cap_w * 4})")
        return data

    def _artifact_visible(self):
        """True while VTE is painting a raw OSC fragment at the cursor.

        VTE 0.84 can flash the shell-integration marker for one frame before it
        parses it (acknowledged in copilot/shell/agent-terminal.bash).  At 15 fps
        that lands in the recording, so those frames re-emit the previous one --
        visually identical, minus the garbage.
        """
        try:
            tab = self.win.active_tab()
            pane = tab.active_pane() if tab is not None else None
            if pane is None or pane.kind != "terminal":
                return False
            row = pane.terminal.get_cursor_position()[1]
            rows = pane._read_rows(max(row - 1, 0), row + 1) or []
        except Exception:
            return False
        return any("666;" in r or "\x1b]" in r or "\x1b[?" in r for r in rows)

    def _on_tick(self, _widget, clock):
        g = self.g
        if self.proc.poll() is not None:
            return g.GLib.SOURCE_REMOVE
        now = clock.get_frame_time()
        if self.t0_us is None:
            self.t0_us = now
        want = int(round((now - self.t0_us) / 1e6 * FPS))
        if want <= self.emitted:
            return g.GLib.SOURCE_CONTINUE
        frame = self.grab() or self.last
        if frame is None:
            return g.GLib.SOURCE_CONTINUE
        if self._artifact_visible() and self.last is not None:
            frame = self.last
            self.suppressed += 1
        self.last = frame
        # Exact CFR: repeat the last frame through any hitch.  Repeats are
        # nearly free in x264rgb and keep audio-free A/V timing honest.
        for _ in range(want - self.emitted):
            try:
                self.q.put_nowait(frame)
            except queue.Full:
                self.dropped += 1
        self.emitted = want
        return g.GLib.SOURCE_CONTINUE

    def _writer(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            try:
                self.proc.stdin.write(item.get_data())
            except (BrokenPipeError, ValueError, OSError):
                break
        try:
            self.proc.stdin.close()
        except Exception:
            pass

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        self.last = self.grab()      # warms the pipeline (first call ~47 ms)
        self.crop = self.opaque_box()
        if self.crop is None:
            raise RuntimeError("could not find the opaque window rect")
        self._spawn_ffmpeg()
        self.tick_id = self.win.add_tick_callback(self._on_tick)

    def stop(self):
        if self.tick_id is not None:
            self.win.remove_tick_callback(self.tick_id)
            self.tick_id = None
        self.q.put(None)
        self.writer.join(timeout=15)
        try:
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    @property
    def elapsed(self):
        return self.emitted / FPS

    def opaque_box(self):
        """The window rect inside the CSD shadow, found from the alpha channel.

        GTK draws the drop shadow inside the surface, so the captured frame has
        a fully transparent border whose width nobody publishes.  Scanning one
        frame's alpha along the centre row and column finds it exactly, and is
        cheaper than guessing from _GTK_FRAME_EXTENTS (which is X11-only).
        """
        frame = self.last or self.grab()
        if frame is None:
            return None
        raw = frame.get_data()
        w, h = self.cap_w, self.cap_h

        def alpha(x, y):
            return raw[(y * w + x) * 4 + 3]

        mid_y, mid_x = h // 2, w // 2
        x0 = next((x for x in range(w) if alpha(x, mid_y) == 255), 0)
        x1 = next((x for x in range(w - 1, -1, -1) if alpha(x, mid_y) == 255), w - 1)
        y0 = next((y for y in range(h) if alpha(mid_x, y) == 255), 0)
        y1 = next((y for y in range(h - 1, -1, -1) if alpha(mid_x, y) == 255), h - 1)
        # Even dimensions keep yuv420p (the WebP/MP4 path) happy.
        bw, bh = (x1 - x0 + 1) & ~1, (y1 - y0 + 1) & ~1
        return {"x": x0, "y": y0, "w": bw, "h": bh}


# ---------------------------------------------------------------------------
# storyboard
# ---------------------------------------------------------------------------

class Demo:
    """Boots the app, drives the storyboard, records while it runs."""

    def __init__(self, workdir: Path, probe_only=False, keep_open=False):
        self.workdir = workdir
        self.probe_only = probe_only
        self.keep_open = keep_open
        self.g = nt.load_gtk()
        self.classes = nt.build_native_classes(self.g)
        self.rec: Recorder | None = None
        self.marks: list[dict] = []
        self.failure: str | None = None
        self.app = None
        self._last_size = (0, 0)
        self.crop = None

    # -- accessors ----------------------------------------------------------

    def window(self):
        win = self.app.get_active_window()
        if isinstance(win, self.classes.NativeTerminalWindow):
            return win
        for candidate in self.app.get_windows() or []:
            if isinstance(candidate, self.classes.NativeTerminalWindow):
                return candidate
        return None

    def pane(self):
        return self.window()._recent_terminal_pane()

    def act(self, name):
        """Activate a win. action -- exactly what the accelerator does."""
        action = self.window().lookup_action(name)
        if action is None:
            raise RuntimeError(f"no such action: {name}")
        action.activate(None)

    def type_(self, text, enter=False):
        pane = self.pane()
        if pane is None:
            raise RuntimeError("no terminal pane to type into")
        pane.insert_text(text + ("\r" if enter else ""))

    def mark(self, name):
        self.marks.append({
            "name": name,
            "t": round(self.rec.elapsed, 3) if self.rec else 0.0,
        })

    # -- readiness ----------------------------------------------------------

    def ready(self):
        """None when ready, else a short reason (used in the abort message)."""
        win = self.window()
        if win is None:
            return "no window"
        if not win.get_mapped():
            return "window not mapped"
        size = (win.get_width(), win.get_height())
        if size[0] <= 0 or size[1] != self._last_size[1] or size[0] != self._last_size[0]:
            # Require two consecutive polls at the same size, so a take never
            # starts mid-resize (which would change the capture dimensions).
            self._last_size = size
            return f"size settling at {size[0]}x{size[1]}"
        pane = self.pane()
        if pane is None:
            return "no terminal pane"
        try:
            row = pane.terminal.get_cursor_position()[1]
            rows = pane._read_rows(max(row - 1, 0), row + 1) or []
        except Exception as exc:
            return f"read_rows: {exc}"
        if not any(r.strip() for r in rows):
            return "no prompt drawn yet"
        win.set_size_request(-1, -1)     # keep the size, drop the constraint
        return None

    # -- steps --------------------------------------------------------------

    def storyboard(self):
        """(delay_ms_before, name, callable) -- the whole demo, in order."""
        g = self.g

        def sls_key(key):
            return lambda: self.type_(key)

        return [
            (600,  "hold",            lambda: None),
            # --no-decorate keeps every line inside a half-width pane; the ref
            # names on HEAD otherwise wrap the first two commits into a mess.
            (100,  "git-log",         lambda: self.type_(
                "git log --oneline --no-decorate -6", enter=True)),
            (2600, "split-right",     lambda: self.act("split-horizontal")),
            (900,  "sls",             lambda: self.type_(
                "bin/sls agent_terminal/copilot", enter=True)),
            # Only toggles with an unmistakable visual result: `e` (truncation
            # mode) is the signature trick but is a no-op unless a name
            # actually overflows its column, which reads as a dead pause.
            (2400, "sls-long",        sls_key("l")),
            (1800, "sls-human",       sls_key("h")),
            (1800, "sls-sort-size",   sls_key("S")),
            (2000, "sls-quit",        sls_key("Q")),
            (900,  "split-down",      lambda: self.act("split-vertical")),
            # Short enough to fit one line next to the real shell prompt.
            (900,  "tests",           lambda: self.type_(
                "python3 -m unittest -q tests.test_smart_ls", enter=True)),
            (2400, "tint",            lambda: self.act("cycle-pane-tint")),
            (1100, "focus-up",        lambda: self.act("focus-up")),
            (1400, "focus-left",      lambda: self.act("focus-left")),
            # fit-focused (62%) rather than zoom-pane (90%): VTE re-wraps a
            # pane's scrollback when it resizes, and at 90% the neighbours
            # collapse into a garbled ten-column strip.  Enlarging the pane
            # that holds the most text also means the reflow reads as an
            # improvement rather than damage.
            (1000, "fit-in",          lambda: self.act("fit-focused")),
            (2000, "fit-out",         lambda: self.act("fit-focused")),
            # No balance("tidy") here: with this tree it gives every pane an
            # equal third, and the narrowed first pane re-wraps its scrollback
            # into a mess.  The 50/50 split reads better on camera.
            (900,  "draft",           lambda: self.type_('find . -name "*.py"')),
            (1900, "ask-open",        lambda: self.act("copilot-ask")),
            (2200, "ask-submit",      self.submit_question),
            (0,    "ask-wait",        self.await_answer),
            (2600, "take",            self.press_take),
            (2100, "run",             lambda: self.type_("", enter=True)),
            (2600, "hold-out",        lambda: None),
        ]

    def submit_question(self):
        win = self.window()
        child = win._ask_revealer.get_child()
        if child is None:
            raise RuntimeError("ask bar did not open")
        entry = find_entry(child, self.g.Gtk)
        if entry is None:
            raise RuntimeError("ask bar has no entry")
        if not entry.get_sensitive():
            raise RuntimeError(
                "ask bar is gated -- no eligible endpoint (check auth.json)")
        # Anchored on ".py files" on purpose.  Ask mode carries the recent
        # shell commands as context as well as the parked draft, so "this" or
        # "these" is genuinely ambiguous -- the model kept answering about the
        # `git log` further up the pane and returning a page-wide
        # `git log | while read ...` pipeline.  Naming something only the draft
        # can refer to pins it to the find.  The answer also has to *print*
        # something: an empty result makes the payoff shot look like nothing
        # happened.
        entry.set_text("of these .py files, print the 5 largest with sizes")
        entry.emit("activate")

    def await_answer(self):
        """Block the storyboard (not the main loop) until the card lands."""
        return {"until": self.answer_ready, "timeout_ms": 20000,
                "on_timeout": "no answer card arrived within 20s"}

    def answer_ready(self):
        child = self.window()._ask_revealer.get_child()
        if child is None:
            return False
        return find_by_css(child, CARD_CSS_CLASS) is not None

    def press_take(self):
        child = self.window()._ask_revealer.get_child()
        button = find_button(child, self.g.Gtk, TAKE_LABEL) if child else None
        if button is None:
            raise RuntimeError("no Take button on the answer card")
        button.emit("clicked")

    # -- run ----------------------------------------------------------------

    def run(self):
        options = nt.parse_args([
            "--working-directory", str(REPO_ROOT),
            "--palette", "agent-dark",
            "--font-family", "DejaVu Sans Mono",
            "--font-size", "12",
            "--no-cursor-blink",
            "--scrollback-lines", "2000",
            "--title", "Terminal Fable",
        ])
        self.app = self.classes.NativeTerminalApplication(options)
        self.app.connect("window-added", self._on_window_added)
        self.app.connect("activate", lambda *_: self.g.GLib.idle_add(self._begin))
        self.app.run(["terminal-fable-demo"])
        return 0 if self.failure is None else 3

    def _on_window_added(self, _app, win):
        # This fires from inside GtkApplicationWindow's construction, i.e.
        # *before* NativeTerminalWindow.__init__ sets its own 1100x700 default
        # -- so the resize has to be deferred past the constructor.
        if isinstance(win, self.classes.NativeTerminalWindow):
            self.g.GLib.idle_add(self._apply_geometry, win)

    def _apply_geometry(self, win):
        # set_default_size is a no-op once the window is mapped, and present()
        # has already run by now.  A size request is the one lever that still
        # resizes a mapped toplevel; it is dropped again in ready() so it does
        # not pin a minimum size for the rest of the take.
        win.set_default_size(LOGICAL_W, LOGICAL_H)
        win.set_size_request(LOGICAL_W, LOGICAL_H)
        return self.g.GLib.SOURCE_REMOVE

    def _begin(self):
        g = self.g
        deadline = time.monotonic() + 10.0

        def poll():
            reason = self.ready()
            if reason is None:
                self._after_ready()
                return g.GLib.SOURCE_REMOVE
            if time.monotonic() > deadline:
                self._abort(f"window/shell never became ready ({reason})")
                return g.GLib.SOURCE_REMOVE
            return g.GLib.SOURCE_CONTINUE

        g.GLib.timeout_add(120, poll)
        return g.GLib.SOURCE_REMOVE

    def _after_ready(self):
        g = self.g
        # Let the resize settle: a mapped-toplevel resize goes through the
        # compositor, and the frames right after it are not representative.
        g.GLib.timeout_add(600, self._after_settle)
        return g.GLib.SOURCE_REMOVE

    def _after_settle(self):
        g = self.g
        win = self.window()
        if win is None or not win.get_mapped():
            self._abort("window disappeared while settling")
            return g.GLib.SOURCE_REMOVE
        print(f"[demo] window {win.get_width()}x{win.get_height()} "
              f"mapped={win.get_mapped()}")
        if self.probe_only:
            self._probe(win)
            return g.GLib.SOURCE_REMOVE
        # Pre-warm the endpoint so the on-camera request is not paying for
        # connection setup.  This ask is opened and closed before recording.
        self._prewarm()
        g.GLib.timeout_add(1500, self._start_recording)
        return g.GLib.SOURCE_REMOVE

    def _prewarm(self):
        try:
            win = self.window()
            win.show_ask_overlay()
            child = win._ask_revealer.get_child()
            entry = find_entry(child, self.g.Gtk) if child else None
            if entry is not None and entry.get_sensitive():
                entry.set_text("say ok")
                entry.emit("activate")
            if win._ask_close is not None:
                win._ask_close()
        except Exception as exc:                       # never fatal
            print(f"[demo] pre-warm skipped: {exc}", file=sys.stderr)

    def _start_recording(self):
        g = self.g
        win = self.window()
        try:
            self.rec = Recorder(g, win, self.workdir / "master.mkv",
                                nt.PALETTES["agent-dark"]["background"])
            self.rec.start()
            self.crop = self.rec.crop
            print(f"[demo] capture {self.rec.cap_w}x{self.rec.cap_h} "
                  f"-> crop {self.crop}")
        except Exception as exc:
            self._abort(f"recorder failed to start: {exc}")
            return g.GLib.SOURCE_REMOVE
        self._steps = iter(self.storyboard())
        g.GLib.timeout_add(200, self._next_step)
        return g.GLib.SOURCE_REMOVE

    def _next_step(self):
        g = self.g
        try:
            delay, name, fn = next(self._steps)
        except StopIteration:
            self._finish()
            return g.GLib.SOURCE_REMOVE

        def fire():
            try:
                result = fn()
            except Exception as exc:
                self._abort(f"step {name!r}: {exc}")
                return g.GLib.SOURCE_REMOVE
            self.mark(name)
            if isinstance(result, dict) and "until" in result:
                self._wait_until(result)
            else:
                g.GLib.timeout_add(1, self._next_step)
            return g.GLib.SOURCE_REMOVE

        g.GLib.timeout_add(max(delay, 1), fire)
        return g.GLib.SOURCE_REMOVE

    def _wait_until(self, spec):
        g = self.g
        deadline = time.monotonic() + spec["timeout_ms"] / 1000.0

        def poll():
            if spec["until"]():
                g.GLib.timeout_add(1, self._next_step)
                return g.GLib.SOURCE_REMOVE
            if time.monotonic() > deadline:
                self._abort(spec["on_timeout"])
                return g.GLib.SOURCE_REMOVE
            return g.GLib.SOURCE_CONTINUE

        g.GLib.timeout_add(150, poll)

    # -- probe / finish -----------------------------------------------------

    def _probe(self, win):
        try:
            rec = Recorder.__new__(Recorder)      # no ffmpeg for a probe
            rec.g, rec.win = self.g, win
            rec.paintable = self.g.Gtk.WidgetPaintable.new(win)
            rec.renderer = win.get_native().get_renderer()
            rec.cap_w = win.get_width() * SUPERSAMPLE
            rec.cap_h = win.get_height() * SUPERSAMPLE
            rec.grab()                            # warm up
            data = rec.grab()
            if data is None:
                self._abort("render node was None (window not mapped?)")
                return
            raw = data.get_data()
            sample = raw[::4097][:4000]
            mean = sum(sample) / max(len(sample), 1)
            surface = win.get_native().get_surface()
            print(f"[probe] window   {win.get_width()}x{win.get_height()} "
                  f"scale={surface.get_scale_factor()}")
            print(f"[probe] captured {rec.cap_w}x{rec.cap_h}  bytes={len(raw)}")
            print(f"[probe] render   {rec.render_ms:.1f} ms   "
                  f"download {rec.download_ms:.1f} ms")
            print(f"[probe] mean px  {mean:.1f}")
            self._probe_formats(win, rec)
            print(f"[probe] artifact-check ok={not rec._artifact_visible()}")
            if mean < 5:
                self._abort(f"frame looks black (mean {mean:.1f})")
                return
            # Sanity: does feed_child reach PromptTracker?  The ask bar's
            # "carrying:" chip depends on it (native_terminal.py:4268).
            pane = self.pane()
            pane.insert_text("echo probe")
            self.g.GLib.timeout_add(500, lambda: self._probe_tracker(pane))
            return
        except Exception as exc:
            self._abort(f"probe failed: {exc}")

    def _probe_formats(self, win, rec):
        """Which download format is cheapest? A swizzle costs real ms/frame."""
        g = self.g
        snapshot = g.Gtk.Snapshot.new()
        rec.paintable.snapshot(snapshot, rec.cap_w, rec.cap_h)
        texture = rec.renderer.render_texture(snapshot.to_node(), None)
        print(f"[probe] native texture format: {texture.get_format().value_nick}")
        for name in ("R8G8B8A8", "B8G8R8A8", "R8G8B8A8_PREMULTIPLIED",
                     "B8G8R8A8_PREMULTIPLIED"):
            fmt = getattr(g.Gdk.MemoryFormat, name, None)
            if fmt is None:
                continue
            downloader = g.Gdk.TextureDownloader.new(texture)
            downloader.set_format(fmt)
            downloader.download_bytes()                     # warm
            t = time.perf_counter()
            for _ in range(3):
                downloader.download_bytes()
            print(f"[probe]   {name:<26} "
                  f"{(time.perf_counter() - t) / 3 * 1000:.1f} ms")

    def _probe_tracker(self, pane):
        tracker = getattr(pane, "_tracker", None)
        typed = tracker.typed_prefix() if tracker else None
        print(f"[probe] typed_prefix after feed_child: {typed!r}")
        if typed != "echo probe":
            print("[probe] WARNING: draft carry-in will not work "
                  "(tracker did not see feed_child)", file=sys.stderr)
        pane.insert_text("\x15")
        self.app.quit()
        return self.g.GLib.SOURCE_REMOVE

    def _abort(self, message):
        self.failure = message
        print(f"[demo] ABORT: {message}", file=sys.stderr)
        self._finish()

    def _finish(self):
        if self.rec is not None:
            stats = {
                "emitted": self.rec.emitted,
                "dropped": self.rec.dropped,
                "suppressed": self.rec.suppressed,
            }
            self.rec.stop()
            duration = self.rec.emitted / FPS
            first = self.marks[1]["t"] if len(self.marks) > 1 else 0.0
            meta = {
                "master": "master.mkv",
                "fps": FPS, "w": self.rec.cap_w, "h": self.rec.cap_h,
                "duration": round(duration, 3),
                "t0": round(max(first - 0.5, 0.0), 3),
                "t1": round(duration, 3),
                "crop": self.crop,
                "background": nt.PALETTES["agent-dark"]["background"],
                "stats": stats,
                "failure": self.failure,
                "marks": self.marks,
            }
            (self.workdir / "meta.json").write_text(
                json.dumps(meta, indent=2), encoding="utf-8")
            print(f"[demo] {stats['emitted']} frames "
                  f"({duration:.1f}s), dropped={stats['dropped']}, "
                  f"artifact-frames={stats['suppressed']}")
            self.rec = None
        if not self.keep_open and self.app is not None:
            self.app.quit()


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def qc(master: Path):
    """Cheap post-take QC with ffmpeg only (no ImageMagick on this box)."""
    problems = []
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(master),
         "-vf", "blackdetect=d=0.4:pic_th=0.98:pix_th=0.05", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    if "blackdetect" in out:
        problems.append("black frames: " + out.split("blackdetect")[-1][:120])
    # Advisory only, and deliberately blunt: at 2560x1600 a few lines of new
    # text move too few pixels to clear freezedetect's threshold, so short
    # windows report false freezes.  A long one still means a hung take.
    out = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(master),
         "-vf", "freezedetect=n=-60dB:d=8", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    if "freeze_start" in out:
        problems.append("possible hang: >8s with no significant change")
    return problems


def chain(fps):
    """The master is already cropped and opaque -- just retime and downscale."""
    return f"fps={fps},scale={OUT_W}:-2:flags=lanczos"


def encode(workdir: Path, out_dir: Path):
    meta = json.loads((workdir / "meta.json").read_text(encoding="utf-8"))
    master = workdir / meta["master"]
    t0, t1 = str(meta["t0"]), str(meta["t1"])
    out_dir.mkdir(parents=True, exist_ok=True)
    palette = workdir / "palette.png"
    trim = ["-ss", t0, "-to", t1, "-i", str(master)]

    # GIF: one global palette (stats_mode=diff weights the *changing* pixels,
    # which is what a mostly-static terminal needs), and an ordered dither --
    # error diffusion would re-dither the static background every frame and
    # destroy inter-frame differencing, ballooning the file.
    run(["ffmpeg", "-y", "-v", "warning", *trim,
         "-vf", f"{chain(GIF_FPS)},palettegen=stats_mode=diff:"
                f"max_colors={GIF_COLORS}",
         "-update", "1", str(palette)])
    gif = out_dir / "demo.gif"
    run(["ffmpeg", "-y", "-v", "warning", *trim, "-i", str(palette),
         "-lavfi", f"{chain(GIF_FPS)}[v];"
                   "[v][1:v]paletteuse=dither=bayer:bayer_scale=5:"
                   "diff_mode=rectangle",
         "-loop", "0", "-final_delay", "200", str(gif)])

    # No -cr_threshold/-cr_size here: libwebp's conditional replenishment
    # collapses runs of "similar enough" frames, which on this content dropped
    # the animation from 24 distinct frames to 14 (measured via the GdkPixbuf
    # animation iterator) while making the file *larger*.
    lossy = workdir / "lossy.webp"
    run(["ffmpeg", "-y", "-v", "warning", *trim, "-vf", chain(WEBP_FPS),
         "-c:v", "libwebp_anim", "-pix_fmt", "yuv420p", "-lossless", "0",
         "-quality", "72", "-compression_level", "6", "-preset", "text",
         "-loop", "0", str(lossy)])
    lossless = workdir / "lossless.webp"
    run(["ffmpeg", "-y", "-v", "warning", *trim, "-vf", chain(WEBP_FPS),
         "-c:v", "libwebp_anim", "-pix_fmt", "bgra", "-lossless", "1",
         "-compression_level", "6", "-loop", "0", str(lossless)])
    webp = out_dir / "demo.webp"
    best = min(lossy, lossless, key=lambda p: p.stat().st_size)
    shutil.copyfile(best, webp)

    for path in (gif, webp):
        print(f"[demo] {path}  {path.stat().st_size / 1024:.0f} KB")
    print(f"[demo] webp source: {best.name}")
    return gif, webp


# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(prog="record-demo")
    parser.add_argument("--probe", action="store_true",
                        help="Capture one frame and print timings, then exit.")
    parser.add_argument("--record", action="store_true",
                        help="Run the storyboard and write master.mkv.")
    parser.add_argument("--encode-only", action="store_true",
                        help="Re-encode deliverables from an existing master.")
    parser.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    parser.add_argument("--out", default=str(REPO_ROOT / "docs" / "demo"))
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out)

    if args.encode_only:
        encode(workdir, out_dir)
        return 0

    if args.record:
        # The storyboard ends on a "5 largest files" command run inside this
        # repo, so a previous take's GIF would show up in its own output.
        for stale in out_dir.glob("demo.*"):
            stale.unlink()

    if args.probe or args.record:
        demo = Demo(workdir, probe_only=args.probe)
        code = demo.run()
        if code != 0:
            return code
        if args.probe:
            return 0
        problems = qc(workdir / "master.mkv")
        for problem in problems:
            print(f"[demo] QC: {problem}", file=sys.stderr)
        encode(workdir, out_dir)
        return 0

    parser.error("pick one of --probe / --record / --encode-only")


if __name__ == "__main__":
    sys.exit(main())
