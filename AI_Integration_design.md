# Design Document: Context-Aware Terminal Copilot

## 1. Purpose

This document defines the product requirements and rationale for a context-aware terminal assistant built into a custom terminal emulator.

The goal is to make terminal use faster, safer, and more understandable by providing:

1. Inline command suggestions while the user types.
2. A side panel where the user can describe an intent in natural language.
3. Context-aware completions based on the current directory, recent commands, project files, local files, and session history.
4. Helpful command recipes for common tasks.
5. Lightweight session memory, summaries, and restoration.

This document intentionally avoids implementation details. Terminal developers should use it as a requirements and behavior specification.

---

## 2. Product Vision

The terminal should feel like a cooperative assistant that understands what the user is doing.

It should not merely complete filenames. It should understand situations such as:

```bash
py
```

inside a Python project, and suggest likely project commands.

It should understand:

```bash
sudo apt install ./
```

inside a downloads directory, and prioritize the most recent `.deb` file.

It should understand that:

```bash
git clone
```

expects a repository URL or repository path, not a random local filename.

It should let the user ask:

```text
split video into each frame
```

and receive a useful command template such as:

```bash
ffmpeg -i <input_video> <output_dir>/frame_%06d.png
```

The product should behave less like a chatbot bolted onto a terminal and more like an intelligent terminal interface.

---

## 3. Core User Experience Principles

### 3.1 Context before intelligence

The assistant should first use obvious local context:

* Current directory.
* Project type.
* Files in the current directory.
* Recently modified files.
* Recent commands.
* Recent failures.
* Known command patterns.
* Project documentation.
* User’s prior usage.

A good context-aware suggestion is often better than a generic AI-generated command.

---

### 3.2 Suggestions must be useful but not intrusive

The assistant should help without making the terminal feel unpredictable.

Inline suggestions should appear as optional suggestions, not as automatic edits. The user must stay in control.

The terminal should never silently rewrite a command in a way that could surprise the user.

---

### 3.3 Safety is part of the UX

Some commands are harmless. Some mutate the local filesystem. Some are destructive. Some affect remote or production systems.

The assistant must treat these differently.

Examples of sensitive commands include:

```bash
rm -rf
sudo
git reset --hard
git clean -fd
docker system prune
kubectl delete
terraform destroy
curl ... | sh
```

The assistant should be conservative around destructive, privileged, remote, or supply-chain-related commands.

---

### 3.4 Inline completion and intent assistance are different products

Inline completion should be fast and lightweight.

The side panel can be slower, richer, and more explanatory.

Inline completion answers:

```text
What is the likely next part of this command?
```

The side panel answers:

```text
What command or workflow would achieve this goal?
```

Both should share context, but they should not behave the same way.

---

## 4. Main Interaction Modes

## 4.1 Inline Suggestions

Inline suggestions appear while the user is typing in the terminal prompt.

Example:

```bash
$ py
```

Inside a Python project, suggestions may include:

```bash
python run.py
python main.py
uv run this_app
pytest
uv run pytest
```

The suggestion should be visibly marked as optional. The user can accept, ignore, dismiss, or open more options.

Inline suggestions should generally be short and directly executable.

They should not include long explanations. When explanation is needed, the user can open the side panel.

---

## 4.2 Completion Menu

When multiple valid suggestions exist, the terminal may show a small list.

Example:

```bash
$ py
```

Possible list:

```text
python run.py          from project files
uv run this_app        from README
pytest                 common project command
uv run pytest          uv project detected
```

The menu should make clear why each suggestion is being shown.

Good labels include:

```text
from README
from history
from project
recent file
known option
recipe
```

---

## 4.3 Intent Side Panel

The side panel lets the user describe a goal in natural language.

Example:

```bash
$ ffmpeg
```

User opens side panel and asks:

```text
split video into each frame
```

The assistant should return one or more command templates:

```bash
ffmpeg -i <input_video> <output_dir>/frame_%06d.png
```

With brief guidance:

```text
Extracts every frame from a video as numbered PNG files.
Replace <input_video> and <output_dir> before running.
```

If the current directory contains likely input files, the assistant may offer a tailored version as well:

```bash
ffmpeg -i demo.mp4 frames/frame_%06d.png
```

The side panel should support:

* Insert into terminal.
* Copy.
* Explain.
* Modify.
* Run, when safe and user-approved.

---

## 4.4 Recipe Search

The user should be able to search for command recipes using natural language or keywords.

Example searches:

```text
unzip all files
sort by size
number of lines
commit
mount
kill process on port
split video frames
find large files
```

Example result:

```bash
du -ah . | sort -h
```

Description:

```text
Show files and directories sorted by size.
```

Recipe results should include:

* Command or script.
* Short description.
* Placeholders if needed.
* Risk level.
* Whether the recipe has been used before.
* Whether it is project-specific, user-specific, or generic.

---

## 4.5 Session Resume Summary

When the user returns to a terminal after a long break, the terminal may offer a short summary.

Example:

```text
You were cleaning obsolete files in ~/old_project.

Recent activity:
- Checked disk usage.
- Searched for deprecated build artifacts.
- Removed old build directories.
- Last deletion failed due to permission issues.
```

This should be available through a deliberate trigger such as a keyboard shortcut, notification chip, or side-panel action.

A bare `?` can be supported only if it does not conflict with normal shell input.

---

## 4.6 Auto-Generated Terminal Titles

Each terminal tab should receive a useful title.

Examples:

```text
api-server: docker logs
old_project cleanup
repo-x: pytest failure
downloads: local deb install
ssh prod-web-1
```

Titles should help the user find the right terminal among many tabs.

The title should be based on:

* Current directory.
* Project name.
* Recent command.
* Long-running process.
* Inferred task.
* Remote host, if applicable.

The title should not change too frequently.

---

## 4.7 Session History and Restoration

The terminal should store meaningful terminal sessions for later review or restoration.

Trivial sessions should generally be ignored, such as a terminal where the user only ran:

```bash
ls
```

or opened:

```bash
top
```

and then closed it.

Stored sessions should include:

* Title.
* Last working directory.
* Command history.
* Important outputs or summaries.
* Exit statuses.
* Time range.
* Project or repository name, if known.

Restoration should mean:

* Reopen a terminal at the previous working directory.
* Show the previous session’s command history.
* Show the previous session summary.
* Allow the user to reuse or rerun previous commands.

The product should not promise to restore arbitrary dead process state. If a live session is still running in the background, reattaching to it may be supported as a separate capability.

---

# 5. Concrete Use Cases and Requirements

## 5.1 Python Project Suggestions

### User scenario

The user enters a directory that appears to be a Python project.

The project may contain:

```text
README.md
pyproject.toml
uv.lock
requirements.txt
setup.py
run.py
main.py
src/
tests/
```

The user types:

```bash
$ py
```

### Expected behavior

The terminal should suggest likely project commands.

Examples:

```bash
python run.py
python main.py
python -m this_app
pytest
uv run pytest
uv run this_app
```

If the README clearly explains how to run the project, those commands should be preferred.

Example README content:

```markdown
Run locally:

uv run this_app
```

Then the suggestion should include:

```bash
uv run this_app
```

marked as:

```text
from README
```

### Environment setup suggestions

If no virtual environment exists and the project appears to use `uv`, `poetry`, `venv`, or similar tooling, the assistant may suggest setup actions.

Example:

```bash
uv sync
uv venv
python -m venv .venv
pip install -r requirements.txt
```

However, setup commands should be treated more cautiously than run commands.

They should preferably appear in the side panel or completion menu, not as aggressive inline ghost text.

### MVP scope

MVP should support common Python project cases:

* `run.py`.
* `main.py`.
* `pytest`.
* `python -m pytest`.
* README command blocks.
* `uv run ...` when `uv` project files are present.
* `poetry run ...` when Poetry project files are present.

### Longer-term scope

Longer-term behavior may include:

* Better README interpretation.
* Framework-specific commands.
* Virtual environment detection.
* Dependency setup suggestions.
* Project-specific learned commands.
* Team-shared command conventions.

---

## 5.2 Natural Language Command Templates

### User scenario

The user types:

```bash
$ ffmpeg
```

Then opens the side panel and asks:

```text
split video into each frame
```

### Expected behavior

The assistant should return command templates with placeholders when required information is missing.

Example:

```bash
ffmpeg -i <input_video> <output_dir>/frame_%06d.png
```

Explanation:

```text
Extracts all frames from the input video as numbered PNG images.
```

If the user likely wants one frame per second, another option may be shown:

```bash
ffmpeg -i <input_video> -vf fps=1 <output_dir>/frame_%06d.png
```

The assistant should make placeholders obvious.

Good placeholder examples:

```text
<input_video>
<output_dir>
<repository_url>
<branch_name>
<container_name>
<port>
```

### MVP scope

MVP should support:

* Command template generation.
* Placeholders for missing arguments.
* Short explanations.
* Insert/copy actions.
* Context-aware suggestions when obvious local files exist.

### Longer-term scope

Longer-term behavior may include:

* Interactive slot filling.
* Multiple command variants.
* Workflow generation.
* User-specific command preferences.
* Project-specific command templates.

---

## 5.3 Recent File Completion for Local Package Install

### User scenario

The user navigates to a usual downloads directory and types:

```bash
$ sudo apt install ./
```

### Expected behavior

The terminal should recognize that this command likely expects a local Debian package file.

It should prioritize recently modified `.deb` files.

Example suggestions:

```bash
./my_package_1.2.3_amd64.deb
./driver_5.4.0_amd64.deb
```

The newest relevant `.deb` file should appear first.

The terminal should not show irrelevant files above `.deb` files.

### Safety behavior

Because the command uses `sudo` and installs software, the assistant should treat it as privileged and potentially sensitive.

File completion is acceptable.

Automatic execution is not acceptable.

The user must explicitly press Enter to run the command.

### MVP scope

This is MVP-worthy.

The feature is concrete, useful, and does not require broad semantic understanding.

---

## 5.4 Argument-Aware Completion

### User scenario

The user types:

```bash
$ git clone 
```

and presses Tab.

### Expected behavior

The terminal should not show arbitrary local files as the main suggestion list.

Instead, it should guide the user toward the expected argument:

```text
Enter a repository URL, for example:
https://github.com/org/repo.git
git@github.com:org/repo.git
```

If the clipboard contains a repository URL, the terminal may suggest it.

If the user has recently cloned or used a repository URL, the terminal may suggest that.

### Other examples

For:

```bash
cd 
```

suggest directories.

For:

```bash
cat 
```

suggest files.

For:

```bash
git add 
```

suggest changed files.

For:

```bash
git checkout 
```

suggest branches first, while still allowing paths when appropriate.

For:

```bash
ssh 
```

suggest known hosts.

For:

```bash
tar -xf 
```

suggest archive files.

For:

```bash
ffmpeg -i 
```

suggest media files.

### Guide text

If there are no concrete suggestions, the terminal should show a short guide.

Example:

```bash
$ git clone 
```

Guide:

```text
Input a repository URL or local repository path.
```

Guides may initially be generated automatically. If the user appears to find a guide useful, it can be kept for future use.

### MVP scope

MVP should support argument-aware behavior for a limited set of common commands.

Recommended MVP commands:

```text
cd
ls
cat
less
vim/nvim
git
python
pip
uv
apt
ssh
scp
rsync
tar
unzip
ffmpeg
docker
docker compose
kubectl
make
just
npm
pnpm
yarn
pytest
```

### Longer-term scope

Longer-term behavior should expand to more commands and improve accuracy through observed usage and user feedback.

### Key caution

This feature should be conservative when command semantics are unknown.

If the terminal does not know what an argument expects, it should fall back to normal shell behavior rather than pretending to understand.

---

## 5.5 Visible Typo Correction

### User scenario

The user types:

```bash
$ rysnc
```

The terminal recognizes the likely intended command:

```bash
rsync
```

Other examples:

```bash
ttps://example.com
```

should suggest:

```bash
https://example.com
```

```bash
cd .../
```

should suggest:

```bash
cd ../
```

### Expected behavior

Corrections must be visible and user-approved.

The terminal should not silently rewrite commands.

Good behavior:

```text
Did you mean: rsync
```

or:

```bash
rsync -av source/ target/
```

with a clear marker:

```text
corrected: rysnc → rsync
```

### Risk-sensitive behavior

Corrections that make a command destructive should be handled very carefully.

Example:

```bash
kubectl detele pod api
```

Correcting this to:

```bash
kubectl delete pod api
```

could turn a harmless failed command into a destructive command.

Such corrections should require explicit confirmation.

### MVP scope

MVP should support only high-confidence typo corrections:

* Mistyped known commands.
* Broken URL prefixes.
* Obvious path shorthand mistakes.
* Common repeated typo patterns.

---

## 5.6 Resume Summary

### User scenario

The user returns to a terminal after a long break.

They ask:

```text
What was I doing here?
```

or trigger a resume summary.

### Expected behavior

The terminal should provide a concise summary.

Example:

```text
You were cleaning obsolete files in ~/old_project.

You checked disk usage, searched for deprecated build artifacts, and removed old build directories.
The last command failed because cache/ could not be deleted without elevated permissions.
```

### MVP scope

MVP should support:

* Summary after idle period.
* Summary from recent commands and current directory.
* Mention of the last failed command, if useful.
* Short, non-intrusive notification.

### Longer-term scope

Longer-term behavior may include:

* Better task inference.
* Cross-session continuity.
* Project-level summaries.
* Suggested next actions.

---

## 5.7 Auto Terminal Titles

### User scenario

The user has many terminal tabs open.

Instead of generic titles like:

```text
zsh
bash
terminal
```

the terminal shows:

```text
api-server: logs
old_project cleanup
repo-x: tests
ssh staging-db
downloads: deb install
```

### Expected behavior

Terminal titles should be useful, compact, and stable.

The title should help the user identify the terminal’s purpose.

### MVP scope

MVP should support:

* Project-aware titles.
* Current command-aware titles.
* Long-running process titles.
* SSH/remote host titles.
* Simple task summaries.

This is a good MVP feature because it is low-risk and highly visible.

---

## 5.8 Session History and Restoration

### User scenario

The user wants to resume a meaningful previous terminal session.

They open a session list and see:

```text
old_project cleanup
api-server docker debugging
repo-x pytest failure
downloads deb install
```

They select one.

### Expected behavior

The terminal should restore useful context:

* Open in the previous working directory.
* Show the previous command list.
* Show a brief summary.
* Allow previous commands to be copied or rerun.
* Restore the terminal title.

### What should not be promised

The product should not claim that it can restore arbitrary program state after the process has ended.

For example, it generally cannot restore:

* A killed Python REPL state.
* A closed editor session.
* A dead SSH connection.
* A long-running process that was terminated.
* An arbitrary shell’s in-memory variables.

If the system supports persistent live sessions, that should be treated as a separate advanced feature.

### MVP scope

MVP should support historical restoration, not full process restoration.

---

## 5.9 Fuzzy Recipe Search

### User scenario

The user searches:

```text
sort by size
```

The terminal shows:

```bash
du -ah . | sort -h
```

Description:

```text
Show files and directories sorted by size.
```

The user searches:

```text
number of lines
```

The terminal shows:

```bash
find . -type f -name '*.py' -print0 | xargs -0 wc -l
```

The user searches:

```text
unzip all files
```

The terminal shows:

```bash
find . -name '*.zip' -exec unzip {} \;
```

### Expected behavior

Recipe search should support:

* Natural language search.
* Fuzzy keyword search.
* Personalized ranking.
* Frequently used commands.
* Project-specific commands.
* Safe insertion into the terminal.
* Risk labels.

### Recipe metadata

Each recipe should have:

```text
command
description
placeholders
risk level
platform notes
last used
usage count
source
```

### MVP scope

This is one of the strongest MVP features.

It is useful, bounded, and does not require fully understanding arbitrary command-line semantics.

---

# 6. MVP Recommendation

The MVP should demonstrate that the terminal understands context and intent without attempting to solve every command-line problem.

## 6.1 Recommended MVP Features

### Include in MVP

```text
Inline ghost suggestions
Completion menu
Recent file-aware completion
Basic project-aware command suggestions
Visible typo correction
Intent side panel for command templates
Fuzzy recipe search
Auto terminal titles
Session history list
Historical session restoration
Resume summaries
Basic risk labels
```

### Include partially in MVP

```text
Python project command suggestions
Argument-aware completion for common commands
README-based run command extraction
Setup command suggestions as side-panel actions
```

### Defer beyond MVP

```text
Universal manpage-based completion
Full arbitrary command semantics
Autonomous multi-step command execution
Deep project debugging agent
Persistent live process restoration
Team-shared command libraries
Highly personalized long-term ranking
```

---

# 7. Non-Goals for MVP

The MVP should not attempt to:

1. Understand every command-line tool.
2. Generate inline AI suggestions on every keystroke.
3. Silently correct user input.
4. Automatically run privileged, destructive, or remote commands.
5. Restore killed process state.
6. Replace shell-native completion entirely.
7. Make risky edits without explicit user action.
8. Hide uncertainty from the user.

The MVP should be conservative and predictable.

---

# 8. Safety Requirements

## 8.1 User approval

The assistant may suggest commands.

The user must remain responsible for accepting and executing them.

Commands should not run automatically unless the user explicitly requests that behavior and the command is safe.

---

## 8.2 Visible modifications

If the assistant changes what the user typed, the change must be clearly visible.

Examples:

```text
rysnc → rsync
ttps:// → https://
cd .../ → cd ../
```

The user should be able to reject or undo the correction easily.

---

## 8.3 Risk labels

Suggestions should be classified into simple risk categories.

Recommended categories:

```text
Read-only
Local change
Privileged
Destructive
Remote
Install/download
Unknown
```

Examples:

```bash
ls
```

Risk:

```text
Read-only
```

```bash
mkdir logs
```

Risk:

```text
Local change
```

```bash
sudo apt install ./package.deb
```

Risk:

```text
Privileged install
```

```bash
rm -rf build/
```

Risk:

```text
Destructive
```

```bash
kubectl delete pod api
```

Risk:

```text
Remote/destructive
```

---

## 8.4 Conservative behavior around dangerous commands

The assistant should avoid aggressive inline suggestions for destructive commands.

For dangerous commands, prefer:

* Side-panel explanation.
* Explicit confirmation.
* Clear risk label.
* Optional safer alternative.

Example:

```bash
git clean -fd
```

should be treated carefully because it removes untracked files.

---

## 8.5 Production and remote contexts

The assistant should be more conservative when the user is operating in:

* SSH sessions.
* Kubernetes contexts.
* Cloud CLI contexts.
* Production-like environments.
* System directories.
* Root shells.

The product should avoid casual mutation suggestions in these contexts.

---

# 9. Privacy Requirements

The terminal may observe sensitive information. Privacy must be a first-class product requirement.

The product should provide clear controls for:

* Whether command history is stored.
* How long session history is retained.
* Which directories are excluded.
* Whether command output is stored.
* Whether command output is summarized.
* Whether any context can be sent to remote services.
* Whether secrets are redacted.
* Whether private projects are indexed.

Sensitive values should not appear in suggestions, summaries, titles, or recipe search unless the user explicitly allows it.

Examples of sensitive values:

```text
tokens
passwords
private keys
API keys
cookies
authorization headers
database URLs
personal file paths
customer data
production hostnames
```

---

# 10. User Control Requirements

The user should be able to configure:

```text
Enable/disable inline suggestions
Enable/disable side panel
Enable/disable typo correction
Enable/disable terminal titles
Enable/disable session summaries
Enable/disable session history
Retention period
Excluded directories
Excluded commands
Remote/AI usage policy
Risk confirmation level
```

There should also be a quick way to pause all assistant features for the current terminal.

---

# 11. Success Criteria

The MVP should be considered successful if users feel that:

1. The terminal understands their current context.
2. Suggestions are usually relevant.
3. The assistant does not interrupt normal terminal flow.
4. The side panel reliably turns intent into usable commands.
5. Risky suggestions are clearly marked.
6. Session titles and summaries make multi-terminal work easier.
7. Recipe search reduces repeated web searches or memory burden.
8. The assistant is easy to ignore when not needed.

Suggested product-level metrics:

```text
Suggestion acceptance rate
Suggestion dismissal rate
Recipe usage rate
Side-panel command insertion rate
User correction/undo rate
Risky suggestion rejection rate
Session restore usage
Terminal title usefulness feedback
Resume summary usefulness feedback
```

The most important negative metric is:

```text
Surprising or unwanted command modification
```

That number should be extremely low.

---

# 12. Feature Priority Summary

## MVP

| Feature                                       |    Priority | Notes                                       |
| --------------------------------------------- | ----------: | ------------------------------------------- |
| Inline suggestions                            |        High | Core interaction.                           |
| Completion menu                               |        High | Needed when multiple suggestions exist.     |
| Python project suggestions                    |        High | Start with common patterns.                 |
| Recent `.deb` completion                      |        High | Concrete, useful, easy to validate.         |
| Intent side panel                             |        High | Major differentiator.                       |
| Command templates with placeholders           |        High | Strong LLM use case.                        |
| Fuzzy recipe search                           |        High | High utility, bounded scope.                |
| Visible typo correction                       | Medium-high | Only high-confidence corrections.           |
| Auto terminal titles                          | Medium-high | Low-risk, high polish.                      |
| Resume summary                                |      Medium | Useful if summaries are short and accurate. |
| Historical session restore                    |      Medium | Restore context, not process state.         |
| Argument-aware completion for common commands |      Medium | Start narrow.                               |

## Longer-Term

| Feature                       |      Priority | Notes                                       |
| ----------------------------- | ------------: | ------------------------------------------- |
| Broad command semantics       |          High | Major long-term moat.                       |
| Manpage/help-derived guidance |   Medium-high | Useful but imperfect.                       |
| Personalized ranking          |          High | Important after usage data exists.          |
| Deep debugging workflows      |   Medium-high | Requires more careful safety design.        |
| Persistent live sessions      |        Medium | Valuable but separate from history restore. |
| Team-shared recipes           |        Medium | Useful for enterprise/team workflows.       |
| Autonomous execution          | Low initially | Must be safety-gated.                       |

---

# 13. Key Product Risks

## 13.1 Wrong confidence

The assistant may act more confident than it should.

Mitigation:

```text
Show uncertainty.
Use menus instead of single completions when unsure.
Prefer guides over commands when intent is ambiguous.
```

---

## 13.2 Dangerous correctness

A suggestion can be syntactically correct but operationally dangerous.

Example:

```bash
kubectl delete pod api
```

may be the correct command but dangerous in the wrong context.

Mitigation:

```text
Risk labels.
Confirmation.
Remote/production awareness.
No aggressive inline completion for destructive commands.
```

---

## 13.3 Context leakage

Terminal history and output may contain secrets.

Mitigation:

```text
Local-first defaults.
Redaction.
Exclusion rules.
Explicit controls for remote processing.
Short retention by default.
```

---

## 13.4 Annoying UI

Too many suggestions will make the terminal feel noisy.

Mitigation:

```text
Only show suggestions when confidence is high.
Let users tune or disable features.
Avoid frequent title changes.
Avoid large popups during typing.
```

---

## 13.5 Overpromising restoration

Users may expect a restored session to continue exactly where it left off.

Mitigation:

```text
Clearly distinguish historical restore from live reattach.
Do not imply dead process state can be recovered.
```

---

# 14. Recommended MVP Definition

The first release should position the product as:

```text
A context-aware terminal assistant that improves command discovery, completion, and session continuity.
```

It should not position itself as:

```text
An autonomous terminal agent.
```

A strong MVP demo should show:

1. Enter a Python project.
2. Type `py`.
3. See relevant project run/test commands.
4. Type `sudo apt install ./`.
5. See the newest `.deb` file suggested.
6. Type `rysnc`.
7. See visible correction to `rsync`.
8. Open side panel from `ffmpeg`.
9. Ask “split video into each frame.”
10. Insert a placeholder-based command template.
11. Search recipes for “sort by size.”
12. Resume a previous terminal and see summary/title/history.

That would clearly demonstrate the product’s value while staying within a realistic and safe scope.

---

# 15. Final Product Direction

The long-term opportunity is not simply “LLM autocomplete for terminals.”

The stronger product direction is:

```text
A terminal that understands task context, command intent, file relevance, session memory, and operational risk.
```

The assistant should become progressively more useful by learning:

* Which commands the user runs.
* Which suggestions the user accepts.
* Which projects have special workflows.
* Which recipes are repeatedly useful.
* Which contexts require caution.

The MVP should establish the foundation: contextual suggestions, intent templates, recipes, titles, summaries, and safe session memory.

The long-term product should become a personalized command interface for expert terminal users.

