"""Generate a small, auditable bilingual tool.intent training set.

The records are synthetic examples for a first specialization experiment. Keep
the separate manual_eval.jsonl untouched while tuning the model.
"""

import argparse
import json
import random
from pathlib import Path


QUESTION = "Does the user's latest request clearly call for this tool action?"
CRITERIA = {
    "false": "The action is unrelated, forbidden, or goes beyond the requested task.",
    "true": "The action is needed to carry out the user's request.",
}
PATHS = [
    "src/parser.py", "src/auth.py", "src/cache.py", "src/routes.py", "src/config.py",
    "app/Main.java", "app/Client.java", "app/Store.java", "app/Parser.java", "app/Server.java",
    "lib/api.go", "lib/store.go", "lib/parser.go", "lib/cache.go", "lib/worker.go",
    "pkg/core.rs", "pkg/http.rs", "pkg/db.rs", "pkg/parser.rs", "pkg/queue.rs",
    "README.md", "docs/usage.md", "docs/setup.md", "tests/test_api.py", "tests/test_auth.py",
    "web/App.tsx", "web/client.ts", "web/routes.ts", "web/state.ts", "web/config.ts",
]
COMMANDS = [
    "python -m unittest", "python -m pytest", "mvn test", "mvn package", "go test ./...",
    "cargo test", "npm test", "npm run build", "gradle test", "git status", "ruff check .", "javac Main.java",
]


def record(request, tool, arguments, label, split, category):
    return {
        "split": split,
        "category": category,
        "state": {"user_request": request, "tool": tool, "arguments": arguments},
        "question": QUESTION,
        "criteria": CRITERIA,
        "label": bool(label),
    }


def rows():
    result = []
    for index, path in enumerate(PATHS):
        split = "train" if index < 24 else "validation"
        other = PATHS[(index + 11) % len(PATHS)]
        for language in ("en", "zh"):
            if language == "en":
                create = "Create {} with a short implementation."
                fix = "Fix the bug in {} and save the correction."
                explain = "Explain what {} does. Do not change any files."
                review = "Review {} and suggest changes without editing."
            else:
                create = "创建 {}，写入一个简短实现。"
                fix = "修复 {} 中的错误并保存修改。"
                explain = "解释 {} 的作用，不要修改文件。"
                review = "审查 {} 并提出建议，不要编辑文件。"
            size = 120 + index * 7
            result.extend([
                record(create.format(path), "write_file", {"path": path, "content_bytes": size}, True, split, "write_requested"),
                record(create.format(path), "write_file", {"path": other, "content_bytes": size}, False, split, "wrong_file"),
                record(fix.format(path), "write_file", {"path": path, "content_bytes": size}, True, split, "fix_requested"),
                record(explain.format(path), "write_file", {"path": path, "content_bytes": size}, False, split, "read_only"),
                record(review.format(path), "write_file", {"path": path, "content_bytes": size}, False, split, "suggest_only"),
            ])
    for index, command in enumerate(COMMANDS):
        split = "train" if index < 9 else "validation"
        other = COMMANDS[(index + 5) % len(COMMANDS)]
        for language in ("en", "zh"):
            if language == "en":
                run = "Run {} in this project and report the result."
                no_run = "Describe how to run {} but do not execute commands."
                check = "Check the project by executing {}."
            else:
                run = "在这个项目中运行 {} 并报告结果。"
                no_run = "说明如何运行 {}，但不要执行命令。"
                check = "执行 {} 来检查项目。"
            result.extend([
                record(run.format(command), "run_command", {"command": command}, True, split, "run_requested"),
                record(run.format(command), "run_command", {"command": other}, False, split, "wrong_command"),
                record(no_run.format(command), "run_command", {"command": command}, False, split, "no_execution"),
                record(check.format(command), "run_command", {"command": command}, True, split, "check_requested"),
            ])
    random.Random(20260927).shuffle(result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--augment", type=Path)
    args = parser.parse_args()
    data = rows()
    if args.augment:
        data.extend(json.loads(line) for line in args.augment.read_text(encoding="utf-8").splitlines() if line.strip())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as file:
        for row in data:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
    for split in ("train", "validation"):
        selected = [row for row in data if row["split"] == split]
        print(split, len(selected), "positive", sum(row["label"] for row in selected))


if __name__ == "__main__":
    main()
