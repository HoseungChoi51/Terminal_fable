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

# Restored-episode history seed. When the app opens a "resume" pane it points
# AGENT_TERMINAL_SEED_HISTFILE at a temp file holding just that episode's
# commands. Load them so up-arrow recalls the episode, and relocate HISTFILE
# onto that temp file so this restored pane never writes into the user's
# global ~/.bash_history. `history -c` first, so only the episode is recalled
# (not the whole loaded history); combined with the HISTFILE relocation this
# is correct whether bash loaded its history before or after this rcfile.
# (Runs before the NO_INTEGRATION / already-installed guards: seeding is
# independent of command journaling.)
if [ -n "${AGENT_TERMINAL_SEED_HISTFILE:-}" ] \
        && [ -f "${AGENT_TERMINAL_SEED_HISTFILE}" ]; then
    builtin history -c
    builtin history -r "${AGENT_TERMINAL_SEED_HISTFILE}"
    HISTFILE="${AGENT_TERMINAL_SEED_HISTFILE}"
    unset AGENT_TERMINAL_SEED_HISTFILE
fi

[ -z "${AGENT_TERMINAL_NO_INTEGRATION:-}" ] || return 0
[ -z "${_agentterm_installed:-}" ] || return 0
_agentterm_installed=1

case ":${HISTCONTROL:-}:" in
    *:ignorespace:*|*:ignoreboth:*) ;;
    *) HISTCONTROL="${HISTCONTROL:+$HISTCONTROL:}ignorespace" ;;
esac

# Keep repository context beside the command being typed, where it is useful
# before every invocation.  The window status bar owns the cwd instead, so
# remove Bash's standard \w/\W prompt escapes and put a compact repo/branch
# segment immediately before \$ (which still expands to "$" for a user and
# "#" for root).  Other/custom prompt machinery is left alone when it has no
# \$ mark.
#
# Keep the names in variables referenced by PS1 rather than interpolating them
# into PS1 itself.  Besides preserving unusual-but-valid Git names literally,
# this lets Bash's \[...\] markers exclude the color escapes from Readline's
# cursor-width calculation.
_agentterm_update_prompt() {
    local root repo branch
    [[ -n ${_agentterm_prompt_managed:-} ]] || return 0
    _agentterm_repo=""
    _agentterm_branch=""
    if root="$(command git rev-parse --show-toplevel 2>/dev/null)"; then
        repo="${root##*/}"
        if branch="$(command git symbolic-ref \
                --quiet --short HEAD 2>/dev/null)"; then
            _agentterm_repo="$repo"
            _agentterm_branch="$branch"
        elif branch="$(command git rev-parse --short HEAD 2>/dev/null)"; then
            _agentterm_repo="$repo"
            _agentterm_branch="@$branch"
        fi
    fi
    if [[ -n $_agentterm_repo && -n $_agentterm_branch ]]; then
        PS1="${_agentterm_prompt_prefix}"
        PS1+=' \[\e[1;36m\]${_agentterm_repo}\[\e[0m\]'
        PS1+=': \[\e[1;35m\]${_agentterm_branch}\[\e[0m\]\$'
        PS1+="${_agentterm_prompt_suffix}"
    else
        PS1="${_agentterm_prompt_prefix}"'\$'"${_agentterm_prompt_suffix}"
    fi
    return 0
}

if [[ ${PS1:-} == *'\$'* ]]; then
    _agentterm_prompt_base=${PS1//'\w'/}
    _agentterm_prompt_base=${_agentterm_prompt_base//'\W'/}
    _agentterm_prompt_prefix=${_agentterm_prompt_base%'\$'*}
    _agentterm_prompt_suffix=${_agentterm_prompt_base##*'\$'}
    _agentterm_prompt_managed=1
    unset _agentterm_prompt_base
    _agentterm_update_prompt
fi

# OpenSSH's default TCP timeout can leave a dead peer looking alive for a very
# long time.  Terminal Fable passes these values only to its default
# interactive Bash panes; wrap the client here so aliases/functions such as
# `connect-x670` that ultimately invoke `ssh` inherit the liveness probe.
#
# Preserve a user-defined `ssh` function: it may intentionally add a jump
# host, a hardware token, or its own transport policy.  A user can disable
# this scoped wrapper through assistant.ssh.keepalive or either zero value.
case "${AGENT_TERMINAL_SSH_SERVER_ALIVE_INTERVAL:-}:${AGENT_TERMINAL_SSH_SERVER_ALIVE_COUNT_MAX:-}" in
    *[!0-9:]*|:*|*:|0:*|*:0) ;;
    *)
        if ! declare -F ssh >/dev/null 2>&1; then
            ssh() {
                command ssh \
                    -o "ServerAliveInterval=${AGENT_TERMINAL_SSH_SERVER_ALIVE_INTERVAL}" \
                    -o "ServerAliveCountMax=${AGENT_TERMINAL_SSH_SERVER_ALIVE_COUNT_MAX}" \
                    "$@"
            }
        fi
        ;;
esac

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
    # Nothing reads mouse or focus reports at a Bash prompt, so any such
    # mode still on was left behind by a program that never got to turn it
    # off -- typically remote tmux/vim whose SSH link died (suspend, network
    # drop).  VTE would otherwise type every mouse move into Readline as
    # "^[[<35;12;7M..." and Ctrl+C cannot stop it.  Reset the X10/normal/
    # button/any-event tracking modes, the UTF-8/SGR/urxvt/SGR-pixel
    # encodings, and focus reporting.  Bracketed paste (2004) is left to
    # Readline, which manages it itself.
    out+=$'\e[?9l\e[?1000l\e[?1001l\e[?1002l\e[?1003l\e[?1004l'
    out+=$'\e[?1005l\e[?1006l\e[?1015l\e[?1016l'
    printf '%s' "$out"
    _agentterm_update_prompt
    return "$errsv"
}

# Baseline against pre-existing (loaded) history so a stale entry is
# never reported as the first command of this session.
_agentterm_last_hist="$(HISTTIMEFORMAT='' builtin history 1 2>/dev/null)" \
    || _agentterm_last_hist=""

# PS0 is expanded after a command is read and before it executes
# (bash >= 4.4); prepend so an existing PS0 keeps working.
#
# Use bash prompt-escape form (\e, \\) rather than pre-expanded escape
# bytes. If we prepend real bytes ending in "ESC \" and the pre-existing
# PS0 begins with a literal "\e" (as vte.sh's "\e]133;C…" does), bash's
# prompt expansion merges our trailing backslash with their leading one
# (\\ -> \) and eats the ESC that starts their OSC — leaking a "]133;C"
# fragment on every command. A fully-literal marker expands cleanly at the
# boundary, so both sequences survive.
PS0='\e]666;vte.shell.preexec!\e\\'"${PS0:-}"

# Prepend to PROMPT_COMMAND so $? still holds the user command's exit
# status when our hook runs.
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then
    PROMPT_COMMAND=(_agentterm_precmd "${PROMPT_COMMAND[@]}")
else
    PROMPT_COMMAND="_agentterm_precmd${PROMPT_COMMAND:+;$PROMPT_COMMAND}"
fi
