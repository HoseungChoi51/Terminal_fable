# The README animation

`demo.gif` and `demo.webp` are produced by
[`bin/record-demo`](../../bin/record-demo), which drives
[`tools/demo/record_demo.py`](../../tools/demo/record_demo.py).

```bash
bin/record-demo                # probe, record a take, QC it, encode both files
bin/record-demo --probe        # capture one frame and print timings
bin/record-demo --encode-only  # re-encode from the last master, no app run
```

A take opens a real window for ~40 s and takes keyboard focus. Leave the
machine alone while it runs. Retakes are cheap; tuning the GIF is cheaper —
`--encode-only` reuses the lossless master under `$CLAUDE_JOB_DIR/tmp` (or
`/tmp`).

## Why it works the way it does

**The app is driven in process, not by synthetic input.** There is no reliable
way to inject keystrokes into a Wayland session (`ydotool`/`wtype` need a
uinput device or a compositor that will cooperate; `xdotool` only reaches
XWayland clients). So the driver imports the app, builds the real
`NativeTerminalApplication`, and runs a storyboard of `win.` action
activations — the same `Gio.SimpleAction`s the accelerators fire — plus
`TerminalPane.insert_text()`, which is the `feed_child` path the ask bar's
Take button already uses. Nothing in the recording is staged: the ask bar
really calls the model, and the Take button really is clicked.

**Frames come from the window's render tree, not from the screen.**
`ffmpeg -f x11grab` of a screen region returns black under GNOME's rootless
Xwayland, because toplevels are composite-redirected away from the root
window. Instead each frame is `Gtk.WidgetPaintable` → `Gtk.Snapshot.to_node()`
→ `Gsk.Renderer.render_texture()` → `Gdk.TextureDownloader`, piped as raw RGBA
into ffmpeg. That is pixel-exact, immune to occlusion (nothing else on the
desktop can appear in a frame), and independent of monitor scale.

Two consequences worth knowing before editing the storyboard:

- **Popovers and dialogs cannot be captured.** They are separate `GtkNative`
  surfaces with their own `GdkSurface`, so they are not in the toplevel's
  render tree. The command menu, the model picker and the session browser are
  therefore out of scope. The ask bar is fine — it is a `Gtk.Revealer` inside
  the window, deliberately never a popover.
- **The window must be visible.** `Gtk.Snapshot.to_node()` returns `None` for
  an unmapped window, and lowering the opacity bakes the opacity into the
  render node. There is no offscreen mode.

## Details that were measured, not guessed

- Download the texture in the renderer's own format
  (`R8G8B8A8_PREMULTIPLIED`). Asking for anything else costs a swizzle pass:
  12.5 ms/frame versus 3.7 ms at 2560×1600.
- Frames are captured at 2× the window size purely as supersampling for the
  downscale to 1000 px, which is why the text stays crisp.
- The crop and the alpha flatten happen in the *capture* ffmpeg, not at encode
  time, because `libx264rgb` has no alpha channel — anything left transparent
  in the master arrives as black. The overlay is told the source is
  premultiplied, or it multiplies by alpha twice and dirties the window's
  antialiased corners.
- GIF runs at 10 fps because GIF frame delays are in centiseconds; 12 fps
  rounds to 8 cs and plays back at the wrong speed. It uses one global palette
  with `stats_mode=diff` and an ordered (Bayer) dither — error diffusion
  re-dithers the static background every frame and destroys inter-frame
  differencing.
- The WebP does **not** use libwebp's `-cr_threshold`/`-cr_size` conditional
  replenishment. On this content it collapsed the animation from 24 distinct
  frames to 14 *and* made the file bigger.
- `freezedetect` in the QC pass is advisory only. At 2560×1600 a few lines of
  new text move too few pixels to clear its threshold, so it reports false
  freezes; only a very long stall means a genuinely hung take.

## Storyboard

The step list lives in `Demo.storyboard()`. Each entry is
`(delay_ms, name, callable)`, and every step's timestamp is written to
`meta.json` next to the master so the encoder can trim precisely.

Two storyboard choices are load-bearing and easy to undo by accident:

- The ask question is anchored on *"this command"*. Ask mode also carries the
  recent shell commands as context, so a bare "sort these by size" gets
  answered about the `git log` further up the pane instead of the parked draft.
- The answer has to print something. An answer whose output is empty makes the
  final shot look like nothing happened.
