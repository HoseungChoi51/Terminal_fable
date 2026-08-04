# 0013 — A recorded demo of the usecase for the README

- Developed: 2026-07 · Status: Built · ADR: —

## Request

> "Review this repository and make a quick demo (as GIF or webp) of the
> usecase"

**Rephrased.** The repo has no images at all and the README opens with prose.
Produce a short looping animation showing what this terminal is actually for,
and put it where a first-time reader will see it.

## Findings

Two constraints decided the whole design, both verified before planning
finished rather than assumed:

- **No input injection is possible on this machine.** `xdotool`, `ydotool`,
  `wtype`, `xte` are absent and cannot be installed; `/dev/uinput` is
  root-only. So "record the screen while someone drives the app" is not
  available — the app has to drive itself.
- **`ffmpeg -f x11grab` of a screen region returns black.** GNOME's rootless
  Xwayland composite-redirects toplevels away from the root window, so
  region capture yields nothing. Measured, not inferred (`-window_id` does
  work, and became the documented fallback).

A third finding shaped the storyboard: GTK4 popovers, dialogs and extra
windows are separate `GtkNative` surfaces and are **not** in the toplevel's
render tree, so nothing that renders on its own surface can be captured by the
chosen method. The command menu, model picker and session browser are
therefore out of reach; the ask bar is not, because it is deliberately a
`Gtk.Revealer` inside the window (ADR 0010).

## Decisions

| Question | Answer |
|---|---|
| What should the demo show? | **Copilot + layout tour** — one window: splits, `sls`, tests, tint/focus/fit, then the ask-mode payoff. (Rejected: a workspaces tour, which needs extra windows and so a screen-region recorder.) |
| Make live LLM calls while recording? | **Yes** — a real request through the configured chain. The answer on camera is the model's, not a script's. |
| Isolated scratch `HOME`, or the real one? | **Real repo, real home** — accepting that the real shell prompt (username/host) is on camera, in exchange for the demo being the actual daily-driver environment. |
| Where does it land? | **Files + README embed**, plus the recorder kept in-tree so it can be re-run. |

## Approach

`tools/demo/record_demo.py` boots the app in-process and drives a storyboard of
`win.` `Gio.SimpleAction` activations — the same actions the accelerators fire
— plus `TerminalPane.insert_text()`, the `feed_child` path the ask bar's Take
button already uses. Nothing is staged: the ask bar really calls the model and
the Take button really is clicked.

Frames are pulled from the window's own render tree
(`Gtk.WidgetPaintable` → `Gtk.Snapshot.to_node()` → `Gsk.Renderer.render_texture()`
→ `Gdk.TextureDownloader`) and piped as raw RGBA into ffmpeg. That is
pixel-exact, independent of monitor scale, and immune to occlusion — nothing
else on the desktop can appear in a frame. It is captured at 2× and downscaled
to 1000 px as supersampling.

`docs/demo/README.md` records the measurements behind the non-obvious choices
(native texture format, where the alpha flatten has to happen, GIF at 10 fps,
why libwebp's conditional replenishment is off) so they are not re-derived.

## Status

Built. `docs/demo/demo.gif` (~530 KB) and `docs/demo/demo.webp` (~290 KB),
1000×626, embedded at the top of `README.md`. Nothing under `agent_terminal/`
changed — the recorder only calls the app's existing surface — and the 501
tests stay green.

Deferred: anything needing a popover, dialog or second window (the command
menu and the workspaces restore, in particular) would need the
`org.gnome.Shell.Screencast` route, which captures a screen rectangle and so
carries a privacy review with it.
