"""Shared tool classification for permission, judgment and lifecycle handling."""

COMMAND_TOOLS = frozenset({"run_command", "bash", "powershell"})
FILE_MUTATIONS = frozenset({"write_file", "edit_file"})
MUTATING_TOOLS = COMMAND_TOOLS | FILE_MUTATIONS
