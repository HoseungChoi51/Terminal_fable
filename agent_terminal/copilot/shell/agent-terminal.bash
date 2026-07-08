# Agent Terminal shell integration.
#
# Emits VTE termprops (OSC 666) so the terminal can journal commands:
# preexec from PS0 when a command starts; on the next prompt, the new
# `history 1` entry (base64), the exit status, and the prompt mark.
# Commands typed with a leading space never enter bash history and are
# therefore never reported — ignorespace is enforced below so that
# privacy guarantee cannot silently depend on the user's HISTCONTROL.
#
# Idempotent; bash only; interactive shells only.
# Disable with AGENT_TERMINAL_NO_INTEGRATION=1.

[ -n "${BASH_VERSION:-}" ] || return 0
[[ $- == *i* ]] || return 0
[ -z "${AGENT_TERMINAL_NO_INTEGRATION:-}" ] || return 0
[ -z "${_agentterm_installed:-}" ] || return 0
_agentterm_installed=1

case ":${HISTCONTROL:-}:" in
    *:ignorespace:*|*:ignoreboth:*) ;;
    *) HISTCONTROL="${HISTCONTROL:+$HISTCONTROL:}ignorespace" ;;
esac

_agentterm_signal() {
    local errsv="$?"
    printf '\033]666;%s!\033\\' "$1"
    return "$errsv"
}

_agentterm_precmd() {
    local errsv="$?" entry rest b64 cmd out
    entry="$(HISTTIMEFORMAT='' builtin history 1 2>/dev/null)" || entry=""
    cmd=""
    if [ -n "$entry" ] && [ "$entry" != "$_agentterm_last_hist" ]; then
        _agentterm_last_hist="$entry"
        # `history` prints "%5d%c %s": digits, a marker char, one space,
        # then the command verbatim. A command that begins with a space
        # was deliberately hidden by the user (ignorespace convention);
        # never report it, even when a history framework (bash-preexec
        # strips ignorespace from HISTCONTROL) let it into history.
        rest="${entry#"${entry%%[0-9]*}"}"   # drop indent before number
        rest="${rest#"${rest%%[!0-9]*}"}"    # drop the number
        rest="${rest#?}"                      # drop the marker char
        rest="${rest#?}"                      # drop the separator space
        # Encode only the command (not the history number/padding): a
        # shorter OSC payload means a shorter transient render if VTE
        # paints the marker before parsing it.
        if [ -n "$rest" ] && [ "${rest# }" = "$rest" ]; then
            if b64="$(printf '%s' "$rest" | base64 2>/dev/null)"; then
                cmd="${b64//$'\n'/}"
            fi
        fi
    fi
    # Emit the whole prompt-boundary burst in ONE printf/write so VTE
    # processes it in a single input pass, minimizing the chance a frame
    # is painted with a half-parsed marker on screen.
    out=$(printf '\033]666;vte.shell.postexec=%s\033\\' "$errsv")
    [ -n "$cmd" ] && out+=$(printf '\033]666;vte.ext.agentterm.cmd=%s\033\\' \
        "$cmd")
    out+=$(printf '\033]666;vte.shell.precmd!\033\\')
    printf '%s' "$out"
    return "$errsv"
}

# Baseline against pre-existing (loaded) history so a stale entry is
# never reported as the first command of this session.
_agentterm_last_hist="$(HISTTIMEFORMAT='' builtin history 1 2>/dev/null)" \
    || _agentterm_last_hist=""

# PS0 is expanded after a command is read and before it executes
# (bash >= 4.4); prepend so an existing PS0 keeps working.
PS0="$(_agentterm_signal vte.shell.preexec)${PS0:-}"

# Prepend to PROMPT_COMMAND so $? still holds the user command's exit
# status when our hook runs.
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND=(_agentterm_precmd "${PROMPT_COMMAND[@]}")
else
    PROMPT_COMMAND="_agentterm_precmd${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi
