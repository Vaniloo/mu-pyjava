"""Conservative command flags; matching is a heuristic, not shell parsing."""

import re


RULES = (
    ("recursive or forced delete", r"\b(?:rm|Remove-Item|ri|del|erase|rd|rmdir)\b[^|;\n]*(?:\s-[A-Za-z]*[rf]\b|-(?:Recurse|Force)\b|/[sq]\b)"),
    ("recursive or forced delete", r"\bfind\b[^|;&\n]*\s-(?:delete\b|(?:exec|execdir|ok|okdir)\s+(?:\S*/)?rm\b)"),
    ("discards git work", r"\bgit\s+(?:reset\b[^|;&\n]*--hard\b|clean\b[^|;&\n]*(?:-[A-Za-z]*f\b|--force\b)|checkout\s+(?:--\s|\.(?:\s|$)|-f\b|--force\b)|stash\s+(?:drop|clear)\b|branch\b[^|;&\n]*(?:\s-D\b|--delete\b[^|;&\n]*--force\b|--force\b[^|;&\n]*--delete\b))"),
    ("discards git work", r"\bgit\s+restore\b(?:(?![^|;&\n]*--staged)|(?=[^|;&\n]*(?:--worktree\b|\s-W\b)))"),
    ("force push", r"\bgit\s+push\b[^|;&\n]*(?:--force(?:-with-lease)?\b|\s-f\b|--mirror\b|--delete\b|\s-d\b|\s\+\S|\s:\S)"),
    ("drops database objects", r"\b(?:drop|truncate)\s+(?:table|database|schema)\b"),
    ("overwrites a device", r"\b(?:mkfs(?:\.\w+)?|Format-Volume|Clear-Disk)\b|\bdd\b[^|;&\n]*\bof=/dev/|\bformat\s+[a-z]:"),
    ("opens permissions recursively", r"\bchmod\s+-R\s+0?777\b"),
    ("runs a downloaded script", r"\b(?:curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm)\b[^;&\n]*\|\s*(?:sudo\s+)?(?:[a-z]*sh|python\d*|perl|ruby|node|Invoke-Expression|iex)\b|<\(\s*(?:curl|wget)\b|\b[a-z]*sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b|\b(?:Invoke-Expression|iex)\b[^;\n]*\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString)\b"),
    ("runs as administrator", r"\bStart-Process\b[^|;\n]*-Verb\s+RunAs\b|\brunas\b"),
    ("runs as root", r"\b(?:sudo|doas|pkexec)\b"),
)


def risk_flag(command: str):
    plain = re.sub(r"\\(?=[A-Za-z])", "", command)
    plain = re.sub(r"([\"'])([^\"'\s]*)\1", r"\2", plain)
    for label, pattern in RULES:
        if re.search(pattern, command, re.I) or re.search(pattern, plain, re.I):
            return label
    return None
