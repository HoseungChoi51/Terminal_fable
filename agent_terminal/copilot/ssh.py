"""Pure detection of failed OpenSSH sessions from journal records.

OpenSSH, not the terminal emulator, is authoritative about whether a remote
connection is dead.  This module runs only *after* the SSH client returned to
the local prompt with a failure.  It recognizes the standard diagnostics so
the GTK shell can offer a safe reconnect affordance without treating a quiet
remote command as a failed connection.
"""

from __future__ import annotations

import re


# These are diagnostics emitted by OpenSSH clients across connection setup,
# transport, and shutdown.  Keep the broad "Connection to HOST closed" form:
# it lets shell aliases/functions such as ``connect-x670`` work too, even
# though the history entry does not begin with the word ``ssh``.
_SSH_DISCONNECT_OUTPUT = re.compile(
    r"(?:"
    r"\bssh:\s+connect to host\b"
    r"|\bconnection to .+ (?:closed|reset|timed out|refused|aborted|lost|broken)\b"
    r"|\bconnection (?:reset|timed out|refused|aborted|lost|broken)\b"
    r"|\bclient_loop:\s+send disconnect:"
    r"|\bkex_exchange_identification:"
    r"|\bconnection closed by (?:remote|foreign) host\b"
    r")",
    re.IGNORECASE,
)


def is_disconnect(record) -> bool:
    """True for a non-zero OpenSSH-style connection failure.

    A normal ``exit`` from a remote shell may print "Connection to … closed",
    so a non-zero client exit is required.  Require an OpenSSH diagnostic too:
    a direct ``ssh`` command can fail for an unrelated reason (for example bad
    authentication), while a user-defined alias must not be identified from
    its name alone.
    """
    if record is None or getattr(record, "exit_code", None) in (None, 0):
        return False
    output = getattr(record, "output_tail", None) or ()
    return bool(_SSH_DISCONNECT_OUTPUT.search("\n".join(map(str, output))))
