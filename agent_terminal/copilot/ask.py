"""Pure state for in-place copilot "ask mode" (see copilot-development-plan).

Ask mode turns the prompt into an LLM chat: the half-typed shell command
is carried in as context (the "seed"), the user types a natural-language
question, and the model answers with a command to take onto the shell
line. This module owns the GTK-free logic — the conversation state
machine, multi-turn query composition, and the safety predicates that
decide whether an answer may be taken or auto-run — so it is unit-tested
without a display. The GTK overlay drives it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent_terminal.copilot import risk as risk_mod

# Shell constructs the risk classifier cannot see into (command
# substitution, backticks, process substitution, redirection). A command
# containing any of these may hide destructive work in a place the
# classifier never inspects, so it is never auto-run — only taken.
_UNSAFE_AUTORUN = re.compile(r"\$\(|`|[<>]")

# States (the overlay's lifecycle while open).
INPUT = "input"          # entry focused, composing the question
THINKING = "thinking"    # a model request is in flight
ANSWER = "answer"        # an answer is shown; Take/Cancel/Explain available
ERROR = "error"          # the last request failed
GATED = "gated"          # no eligible endpoint (cloud off, no local)


@dataclass(frozen=True)
class Turn:
    role: str                 # "question" | "answer" | "error"
    text: str = ""            # question text / error text
    command: str = ""         # answer: the suggested command
    description: str = ""
    risk_display: str = ""    # answer: risk classification label
    endpoint: str = ""        # answer: which endpoint served it


class AskSession:
    """One in-place conversation. Mutable; the overlay reads/advances it."""

    def __init__(self, *, seed="", carry_draft=True, max_turns=8):
        self.seed = seed if carry_draft else ""
        self.parked = bool(self.seed)   # was the shell line cleared/parked?
        self.max_turns = max(int(max_turns), 1)
        self.turns: list[Turn] = []
        self.state = INPUT

    # -- transcript ------------------------------------------------------

    def begin_question(self, text) -> str:
        """Record a question, move to THINKING, return the composed query."""
        query = self.compose_query(text)
        self.turns.append(Turn("question", text=text))
        del self.turns[:-2 * self.max_turns]   # cap history
        self.state = THINKING
        return query

    def add_answer(self, *, command, description="", risk_display="",
                   endpoint=""):
        self.turns.append(Turn("answer", command=command,
                               description=description,
                               risk_display=risk_display, endpoint=endpoint))
        self.state = ANSWER

    def add_error(self, text):
        self.turns.append(Turn("error", text=text))
        self.state = ERROR

    def last_answer(self) -> Turn | None:
        for turn in reversed(self.turns):
            if turn.role == "answer" and turn.command:
                return turn
        return None

    def compose_query(self, question) -> str:
        """Multi-turn: thread the prior suggestion into the new question."""
        previous = self.last_answer()
        if previous is not None:
            return (f"Previous suggestion: {previous.command}\n"
                    f"Refine it per this: {question}")
        return question


# -- safety predicates ---------------------------------------------------

def hotkeys_armed(state, entry_focused) -> bool:
    """Y/N/T act as one-key actions only on a shown answer while the text
    entry does NOT have focus.

    Keying off focus (not entry emptiness) removes the ambiguity where the
    first character of a typed follow-up would otherwise be swallowed as an
    accept: after an answer the overlay moves focus off the entry, so Y/N/T
    are unambiguous; clicking into the entry to type a follow-up disarms
    them and they become ordinary characters.
    """
    return state == ANSWER and not entry_focused


def can_take(command, risk_display, *, auto_pilot=False,
             ceiling=risk_mod.LOCAL_CHANGE):
    """(take_ok, run_ok) for accepting a suggested command.

    Take is offered only for a single-line command (a newline would run
    everything up to it when fed to the shell). Auto-run additionally
    requires auto-pilot and a risk no higher than the ceiling and not
    ``unknown`` — destructive/privileged/unknown never run on a keystroke.
    """
    if not command or "\n" in command or "\r" in command:
        return (False, False)
    if not auto_pilot:
        return (True, False)
    severity = risk_mod.SEVERITY.get(risk_display, 0)
    ceiling_severity = risk_mod.SEVERITY.get(ceiling, risk_mod.SEVERITY[
        risk_mod.LOCAL_CHANGE])
    run_ok = 1 <= severity <= ceiling_severity   # excludes unknown (0)
    if run_ok and _UNSAFE_AUTORUN.search(command):
        # Defense in depth: a substitution/redirection can hide destructive
        # work the classifier graded low. Take it, but never on a keystroke.
        run_ok = False
    return (True, run_ok)
