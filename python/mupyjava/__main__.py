"""CLI and the desktop's long-lived backend process."""

import argparse
import os
import sys
import threading
from pathlib import Path

from .agent import Agent
from .judge import DecisionEngine, LayaBooleanJudge, LayaHttpBooleanJudge, ModelBooleanJudge
from .model import ChatCompletionsModel, model_from_environment
from .permissions import ApprovalManager
from .tools import WorkspaceTools
from .wire import event_line, parse_request


def build_agent(args: argparse.Namespace) -> Agent:
    model = model_from_environment()
    mode = os.environ.get("MU_JUDGE_MODE", "off")
    judge_model_name = os.environ.get("MU_JUDGE_MODEL")
    laya_path = os.environ.get("MU_JUDGE_LAYA_PATH")
    laya_url = os.environ.get("MU_JUDGE_LAYA_URL")
    if (laya_path or laya_url) and mode == "active":
        raise ValueError("The experimental Laya judge is shadow-only until independently validated")
    if mode != "off" and not (judge_model_name or laya_path or laya_url):
        raise ValueError("Set MU_JUDGE_MODEL, MU_JUDGE_LAYA_PATH or MU_JUDGE_LAYA_URL for a judge mode")
    if sum(bool(value) for value in (judge_model_name, laya_path, laya_url)) > 1:
        raise ValueError("Set only one judge backend")
    judge_model = None
    if judge_model_name:
        judge_model = ChatCompletionsModel(
            os.environ.get("MU_API_BASE", "https://api.openai.com/v1"),
            os.environ.get("MU_API_KEY", ""),
            judge_model_name,
        )
    backend = (LayaHttpBooleanJudge(laya_url) if laya_url else
               LayaBooleanJudge(laya_path, device=os.environ.get("MU_JUDGE_LAYA_DEVICE")) if laya_path else
               ModelBooleanJudge(judge_model) if judge_model else None)
    judge = DecisionEngine(
        mode=mode,
        backend=backend,
        ledger=Path(args.ledger) if args.ledger else None,
    )
    # The interactive server gates each call before reaching these tool methods.
    tools = WorkspaceTools(Path(args.workspace), args.allow_write or args.server,
                           args.allow_command or args.server)
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
    output_lock = threading.Lock()

    def emit(request_id: str, kind: str, message: str) -> None:
        with output_lock:
            print(event_line(request_id, kind, message), flush=True)

    manager = ApprovalManager(agent.tools, emit, args.allow_write, args.allow_command)
    worker = None

    def run_chat(request_id: str, prompt: str) -> None:
        try:
            for kind, message in agent.run(
                prompt, approval=lambda call_id, name, arguments:
                    manager.request(request_id, call_id, name, arguments)
            ):
                emit(request_id, kind, message)
        except Exception as error:
            emit(request_id, "error", str(error))
        finally:
            emit(request_id, "done", "")

    try:
        for raw in sys.stdin:
            fields = raw.rstrip("\n").split("\t")
            if len(fields) == 3 and fields[0] == "APPROVAL":
                manager.resolve(fields[1], fields[2])
                continue
            request_id = "?"
            try:
                _, request_id, prompt = parse_request(raw)
                if worker is not None and worker.is_alive():
                    emit(request_id, "error", "A turn is already running")
                    emit(request_id, "done", "")
                    continue
                worker = threading.Thread(target=run_chat, args=(request_id, prompt), daemon=True)
                worker.start()
            except Exception as error:
                emit(request_id, "error", str(error))
                emit(request_id, "done", "")
    finally:
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
