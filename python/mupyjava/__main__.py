"""CLI and the desktop's long-lived backend process."""

import argparse
import os
import sys
from pathlib import Path

from .agent import Agent
from .judge import DecisionEngine, LayaBooleanJudge, ModelBooleanJudge
from .model import ChatCompletionsModel, model_from_environment
from .tools import WorkspaceTools
from .wire import event_line, parse_request


def build_agent(args: argparse.Namespace) -> Agent:
    model = model_from_environment()
    mode = os.environ.get("MU_JUDGE_MODE", "off")
    judge_model_name = os.environ.get("MU_JUDGE_MODEL")
    laya_path = os.environ.get("MU_JUDGE_LAYA_PATH")
    if mode != "off" and not (judge_model_name or laya_path):
        raise ValueError("Set MU_JUDGE_MODEL or MU_JUDGE_LAYA_PATH when MU_JUDGE_MODE is shadow or active")
    if judge_model_name and laya_path:
        raise ValueError("Set only one of MU_JUDGE_MODEL and MU_JUDGE_LAYA_PATH")
    judge_model = None
    if judge_model_name:
        judge_model = ChatCompletionsModel(
            os.environ.get("MU_API_BASE", "https://api.openai.com/v1"),
            os.environ.get("MU_API_KEY", ""),
            judge_model_name,
        )
    backend = LayaBooleanJudge(laya_path, device=os.environ.get("MU_JUDGE_LAYA_DEVICE")) if laya_path else (
        ModelBooleanJudge(judge_model) if judge_model else None
    )
    judge = DecisionEngine(
        mode=mode,
        backend=backend,
        ledger=Path(args.ledger) if args.ledger else None,
    )
    tools = WorkspaceTools(Path(args.workspace), args.allow_write, args.allow_command)
    return Agent(model, tools, judge)


def main() -> int:
    parser = argparse.ArgumentParser(description="mu-pyjava prototype")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--prompt")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-command", action="store_true")
    parser.add_argument("--ledger", help="Optional JSONL path for decision records")
    args = parser.parse_args()
    if not args.server and not args.prompt:
        parser.error("Pass --prompt TEXT or --server")
    try:
        agent = build_agent(args)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.prompt:
        for kind, message in agent.run(args.prompt):
            print(kind + ": " + message)
        return 0
    for raw in sys.stdin:
        request_id = "?"
        try:
            _, request_id, prompt = parse_request(raw)
            for kind, message in agent.run(prompt):
                print(event_line(request_id, kind, message), flush=True)
        except Exception as error:
            print(event_line(request_id, "error", str(error)), flush=True)
        print(event_line(request_id, "done", ""), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
