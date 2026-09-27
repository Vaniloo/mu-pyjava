"""Request candidate tool-intent examples; never persist the API credential."""

import argparse
import getpass
import json
import urllib.request
from pathlib import Path

from build_data import CRITERIA, QUESTION


SYSTEM = """Generate a JSON object with a single `cases` array for a binary coding-agent tool-intent judge.
Each case has user_request (string), tool (write_file or run_command), arguments (object),
label (boolean), and rationale (short string). The label is true only when the proposed
tool action is needed and in scope for the user's latest request. For write_file use
arguments {"path": "...", "content_bytes": integer}; for run_command use
arguments {"command": "..."}. Example JSON:
{"cases":[{"user_request":"Run unit tests for the Java project", "tool":"run_command",
"arguments":{"command":"mvn test"},"label":true,"rationale":"Requested test command"}]}
Write varied realistic English and Chinese software tasks. Mix true and false cases evenly.
Include indirect user requests, mismatched file or command, read-only reviews, prohibitions,
and requests where a check is a necessary part of implementation. Avoid unsafe shell commands.
Do not reproduce the example. Return JSON only."""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--count", type=int, default=40)
    args = parser.parse_args()
    key = getpass.getpass("DeepSeek API key: ")
    payload = {
        "model": "deepseek-flash",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Produce {args.count} distinct cases as JSON. Include at least ten cases in Chinese."},
        ],
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
        "max_tokens": 5000,
        "stream": False,
    }
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    del key
    with urllib.request.urlopen(request, timeout=120) as response:
        body = json.load(response)
    content = body["choices"][0]["message"]["content"]
    candidates = json.loads(content)["cases"]
    rows = []
    seen = set()
    for case in candidates:
        if case.get("tool") not in ("write_file", "run_command"):
            continue
        if not isinstance(case.get("label"), bool) or not isinstance(case.get("user_request"), str):
            continue
        arguments = case.get("arguments")
        if not isinstance(arguments, dict):
            continue
        if case["tool"] == "write_file":
            if not isinstance(arguments.get("path"), str) or not isinstance(arguments.get("content_bytes"), int):
                continue
            arguments = {"path": arguments["path"], "content_bytes": arguments["content_bytes"]}
        else:
            if not isinstance(arguments.get("command"), str):
                continue
            arguments = {"command": arguments["command"]}
        state = {"user_request": case["user_request"], "tool": case["tool"], "arguments": arguments}
        fingerprint = json.dumps(state, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        rows.append({"split": "train", "category": "deepseek_candidate", "state": state,
                     "question": QUESTION, "criteria": CRITERIA, "label": case["label"],
                     "rationale": case.get("rationale", "")})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    print(f"Saved {len(rows)} candidates to {args.output}; review labels before training")


if __name__ == "__main__":
    main()
