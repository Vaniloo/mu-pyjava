"""Conservative offline guard for shell syntax incorrectly assigned to run_command.

This checks transport semantics, not command safety, installed programs or user intent.
Quoted shell scripts passed to an explicit shell executable remain valid argv.
"""
import shlex


def command_state_issue(state):
    if state.get('tool')!='run_command':return None
    command=state.get('arguments',{}).get('command')
    if not isinstance(command,str):return 'missing_command_string'
    try:argv=shlex.split(command)
    except ValueError:return 'invalid_argv_quoting'
    if not argv:return 'empty_argv'
    if argv[0] in {'command','cd','export','source','.', 'alias','unalias','set','unset','read','eval','exec'}:
        return 'shell_builtin_without_shell'
    quote=None;escaped=False
    for char in command:
        if escaped:escaped=False;continue
        if char=='\\' and quote!="'":escaped=True;continue
        if quote:
            if char==quote:quote=None
            continue
        if char in "\"'":quote=char;continue
        if char in ';&|<>`$()\n':return 'shell_syntax_without_shell'
    return None


def audit_command_states(rows):
    return [{'sample_id':row.get('sample_id'),'family':row.get('family',row.get('group_id')),
             'split':row.get('split'),'issue':issue} for row in rows
            if (issue:=command_state_issue(row['state'])) is not None]


def require_command_states(rows):
    issues=audit_command_states(rows)
    if issues:raise ValueError(f'{len(issues)} states assign shell syntax/builtins to direct run_command; recollect with correct tool semantics')
