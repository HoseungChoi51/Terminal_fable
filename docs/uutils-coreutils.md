# coreutils: uutils (Rust) as the default, with fallbacks

This machine runs the [uutils](https://github.com/uutils/coreutils) Rust
reimplementation of coreutils as the default, chosen for its memory safety and
active, Ubuntu-backed trajectory. This note records the setup, the **caveat**,
and the **fallback plan**.

## Setup

- **Default (interactive shells): uutils 0.9.0**, built with
  `cargo install coreutils --version 0.9.0` into `~/.cargo/bin`, with a symlink
  per utility → the `coreutils` multicall. A block in `~/.bashrc`
  (`# ===== uutils coreutils (user default: 0.9.0) =====`) prepends
  `~/.cargo/bin` to `PATH`, so every interactive shell — including Terminal
  Fable panes, which source `~/.bashrc` — uses 0.9.0.
- **Base (system): uutils 0.8.0**, apt-managed (`rust-coreutils`,
  `coreutils-from-uutils`), still installed underneath as the system default and
  a fallback.
- **GNU coreutils: installed in parallel** (`gnu-coreutils`) as per-command
  binaries with a `gnu` prefix — `gnutty`, `gnusort`, `gnuls`, … — always
  available when strict GNU behavior is needed.

## Caveat — imperfections may cause unexpected behavior

uutils targets GNU behavior and passes most of the GNU test suite, but **it is
not 100% compatible**. Its imperfections can surface as unexpected behavior,
most often:

- **Output-format drift** — a utility's output differs byte-for-byte from GNU.
  (Current example: `tty` omits the trailing newline GNU emits, so a shell
  prompt renders glued to `/dev/pts/N`.)
- **Missing or partial flags / utilities**, differing **exit codes / error
  messages**, or **locale/collation** differences that can change how scripts
  behave.
- Treat the **destructive utilities** (`rm`, `mv`, `cp`, `dd`, `chmod -R`, …)
  with the most care — that is where a compatibility bug has the largest blast
  radius. Keep backups and fall back to GNU for these if anything looks off.

## Where to report coreutils issues — upstream, not here

If a `coreutils` command misbehaves (wrong output, a missing flag, a newline
like the `tty` case), that is a **uutils** issue, **not a Terminal Fable bug**.
Terminal Fable is a terminal emulator: it faithfully displays whatever bytes a
program writes, and it must not invent or strip newlines (real programs — `printf
x`, prompts, progress bars — legitimately omit trailing newlines).

**Report such issues to the uutils/coreutils project:**
<https://github.com/uutils/coreutils/issues> — first confirm it against GNU
(`gnu<util>`, e.g. `gnutty`) so the report is precise. Please do **not** file
coreutils-behavior bugs against Terminal Fable.

## Fallback plan

From least to most drastic:

1. **Use GNU for one command** — call the `gnu`-prefixed binary directly
   (`gnutty`, `gnusort`, …).
2. **Override one utility persistently** — repoint its symlink to GNU, e.g.
   `ln -sf /usr/bin/gnutty ~/.cargo/bin/tty` (wins because `~/.cargo/bin` is
   first on `PATH`).
3. **Drop back to system uutils 0.8.0** — delete the
   `# ===== uutils coreutils (user default: 0.9.0) =====` block from
   `~/.bashrc`; new shells use the apt-managed default again.
4. **Make GNU the system default** — an apt operation on `coreutils-from-uutils`
   / `gnu-coreutils` (needs sudo). Both implementations are installed, so this
   is reversible at any time.

## Verifying / comparing behavior

Compare a utility against the GNU reference under a real terminal:

```bash
gnutty        # GNU: prints /dev/pts/N followed by a newline
tty           # uutils: (0.8.0 and 0.9.0) prints it without the newline
```
