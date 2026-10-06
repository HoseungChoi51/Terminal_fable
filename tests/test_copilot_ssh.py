"""Headless contracts for SSH disconnect recognition."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

from agent_terminal.copilot import ssh


def record(*, command="connect-x670", exit_code=255, output=()):
    return SimpleNamespace(cmd=command, exit_code=exit_code,
                           output_tail=tuple(output))


class DisconnectTests(unittest.TestCase):
    def test_alias_is_recognized_from_the_standard_closed_message(self):
        self.assertTrue(ssh.is_disconnect(record(output=(
            "Connection to x670 closed by remote host.",))))

    def test_common_openssh_transport_failures_are_recognized(self):
        for line in (
                "ssh: connect to host x670 port 22: No route to host",
                "Connection reset by 192.0.2.8 port 22",
                "client_loop: send disconnect: Broken pipe",
                "kex_exchange_identification: Connection closed by remote host",
        ):
            self.assertTrue(ssh.is_disconnect(record(command="ssh x670",
                                                     output=(line,))), line)

    def test_normal_remote_exit_never_prompts_to_reconnect(self):
        self.assertFalse(ssh.is_disconnect(record(
            exit_code=0, output=("Connection to x670 closed.",))))

    def test_failed_non_ssh_command_is_not_guessed_from_its_name(self):
        self.assertFalse(ssh.is_disconnect(record(
            command="connect-x670", output=("command failed",))))

    def test_missing_exit_or_output_is_not_a_confirmed_disconnect(self):
        self.assertFalse(ssh.is_disconnect(record(exit_code=None, output=(
            "Connection reset by peer",))))
        self.assertFalse(ssh.is_disconnect(record(output=())))


if __name__ == "__main__":
    unittest.main()
