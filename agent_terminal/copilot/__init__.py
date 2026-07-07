"""Pure core of the terminal copilot (see copilot-development-plan.md).

Every module in this package is GTK-free and curses-free. The one-way
import rule from ADR 0005 applies: native_terminal imports copilot.*;
no module here may import native_terminal. Existing pure helpers are
injected as parameters instead.
"""
