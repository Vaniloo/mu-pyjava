"""CLI and the desktop's long-lived backend process."""

import argparse
import os
import sys
import threading
import uuid
from pathlib import Path

from .agent import Agent
from .cancel import CancellationToken, TurnCancelled
from .context import ContextSettings, context_status, format_context
from .judge_config import engine_from_environment
from .model import ChatCompletionsModel, model_from_environment
from .permissions import ApprovalManager
from .sessions import SessionStore, format_judgment
from .task_frame import format_frame
from .admission import AdmissionSettings
from .summary import SummarySettings, format_summary
from .tools import WorkspaceTools
from .tool_result import format_result_details
from .wire import decode_text, encode_text, event_line, parse_request


def build_agent(args: argparse.Namespace) -> Agent:
    context_settings = ContextSettings.from_environment()
    summary_settings = SummarySettings.from_environment()
    admission_settings = AdmissionSettings.from_environment()
    model = model_from_environment(max_output_tokens=context_settings.reserve_tokens)
    judge = engine_from_environment(Path(args.ledger) if args.ledger else None)
    # The interactive server gates each call before reaching these tool methods.
    tools = WorkspaceTools(Path(args.workspace), args.allow_write or args.server,
                           args.allow_command or args.server,
                           allow_custom=args.server or getattr(args, "allow_custom_tools", False))
    modules = list(getattr(args, "tool_module", None) or [])
    modules.extend(name.strip() for name in os.environ.get("MU_TOOL_MODULES", "").split(",") if name.strip())
    for name in dict.fromkeys(modules):
        tools.registry.load_module(name)
    summary_model = None
    if summary_settings.mode == "model":
        if not os.environ.get("MU_SUMMARY_MODEL"):
            raise ValueError("MU_SUMMARY_MODE=model requires MU_SUMMARY_MODEL")
        summary_model = ChatCompletionsModel(
            os.environ.get("MU_SUMMARY_API_BASE", os.environ.get("MU_API_BASE", "https://api.openai.com/v1")),
            os.environ.get("MU_SUMMARY_API_KEY", os.environ.get("MU_API_KEY", "")),
            os.environ["MU_SUMMARY_MODEL"], timeout=summary_settings.wait_seconds, max_output_tokens=2048)
    return Agent(model, tools, judge, context_settings=context_settings, admission_settings=admission_settings,
                 summary_settings=summary_settings, summary_model=summary_model)


def main() -> int:
    parser = argparse.ArgumentParser(description="mu-pyjava prototype")
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--prompt")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-command", action="store_true")
    parser.add_argument("--allow-custom-tools", action="store_true", help="Allow custom mutation tools in noninteractive CLI mode")
    parser.add_argument("--tool-module", action="append", help="Load a trusted Python module exporting register_tools(registry)")
    parser.add_argument("--ledger", help="Optional JSONL path for decision records")
    parser.add_argument("--session-dir", help="Local directory for saved sessions")
    parser.add_argument("--new-session", action="store_true", help="Start a fresh server session")
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
    session_root = Path(args.session_dir) if args.session_dir else None
    store = SessionStore.open(agent.tools.root, session_root, resume=not args.new_session)
    agent.messages = store.restore_messages()
    agent.frame = store.restore_frame()
    agent.summaries.restore(agent.messages, store.summary_records())
    agent.tools.output_root = store.output_dir
    output_lock = threading.Lock()
    current_turn = None
    current_request_id = None
    current_cancel = None

    def emit(request_id: str, kind: str, message: str) -> None:
        with output_lock:
            print(event_line(request_id, kind, message), flush=True)

    def emit_action(request_id: str, kind: str, message: str) -> None:
        if current_turn is not None and kind == "approval.request":
            fields = message.split("\t")
            if len(fields) == 6 and fields[0] == "v1":
                store.append("approval.request", {"approval_id": fields[1], "summary": decode_text(fields[4])},
                             current_turn, decode_text(fields[2]))
        elif current_turn is not None and kind == "approval.resolved":
            fields = message.split("\t")
            if len(fields) == 3 and fields[0] == "v1":
                store.append("approval.resolved", {"approval_id": fields[1], "answer": fields[2]}, current_turn)
        emit(request_id, kind, message)

    manager = ApprovalManager(agent.tools, emit_action, args.allow_write, args.allow_command)
    worker = None

    def run_chat(request_id: str, prompt: str, turn_id: str, cancel: CancellationToken) -> None:
        nonlocal current_turn, current_request_id, current_cancel
        try:
            store.append("turn.started", {"request_id": request_id}, turn_id)
            store.append("display", {"kind": "you", "text": prompt}, turn_id)

            def save_message(message: dict) -> None:
                store.append("message", {"message": message}, turn_id, message.get("tool_call_id"))

            def save_artifact(call_id: str, artifact_id: str) -> None:
                store.append("tool.artifact", {"id": artifact_id}, turn_id, call_id)
                emit(request_id, "tool.artifact", "Full output id: " + artifact_id)

            def save_tool_event(call_id: str, kind: str, payload: dict) -> None:
                store.append(kind, payload, turn_id, call_id)
                if kind == "tool.change":
                    location = ("\nFirst changed line: " + str(payload["first_changed_line"])) \
                        if payload.get("first_changed_line") is not None else ""
                    emit(request_id, kind, f"{payload['path']}: {payload['before_bytes']} → "
                         f"{payload['after_bytes']} bytes" + location + "\n" + payload["diff"])
                elif kind == "tool.result":
                    summary = format_result_details(payload["tool"], payload)
                    if summary:
                        store.append("display", {"kind": "tool.detail", "text": summary}, turn_id, call_id)
                        emit(request_id, "tool.detail", summary)

            def save_context(record: dict) -> None:
                store.append("context.budget", record, turn_id)
                emit(request_id, "context.status", context_status(record))
                emit(request_id, "context.detail", format_context(record))

            def save_judgment(record: dict) -> None:
                store.append("judge.record", record, turn_id)
                emit(request_id, "judge.detail", format_judgment(record))

            def save_frame(record: dict) -> None:
                store.append("task.frame", record, turn_id)
                emit(request_id, "frame.detail", format_frame(record))

            def save_summary(record: dict) -> None:
                store.append("context.summary", record, turn_id)
                emit(request_id, "summary.detail", format_summary(record))

            for kind, message in agent.run(
                prompt, approval=lambda call_id, name, arguments:
                    manager.request(request_id, call_id, name, arguments),
                on_message=save_message,
                cancel=cancel,
                on_tool_update=lambda call_id, chunk: emit(request_id, "tool.update", chunk),
                on_tool_artifact=save_artifact,
                on_tool_event=save_tool_event,
                output_dir=store.output_dir,
                expected_change=manager.take_expected_change,
                on_context=save_context,
                on_judgment=save_judgment,
                on_frame=save_frame,
                on_summary=save_summary,
                risk_approval=lambda call_id, name, arguments, flag: manager.request(
                    request_id, call_id, name, arguments, force_confirmation=True, risk_flag=flag),
            ):
                store.append("display", {"kind": kind, "text": message}, turn_id)
                emit(request_id, kind, message)
            store.append("turn.completed", {}, turn_id)
        except TurnCancelled:
            store.append("turn.interrupted", {"reason": "cancelled"}, turn_id)
            agent.messages = store.restore_messages()
            agent.frame = store.restore_frame()
            agent.summaries.restore(agent.messages, store.summary_records())
            emit(request_id, "cancelled", "Turn cancelled")
        except Exception as error:
            store.append("turn.interrupted", {"reason": type(error).__name__}, turn_id)
            agent.messages = store.restore_messages()
            agent.frame = store.restore_frame()
            agent.summaries.restore(agent.messages, store.summary_records())
            emit(request_id, "error", str(error))
        finally:
            current_turn = None
            current_request_id = None
            current_cancel = None
            emit(request_id, "done", "")

    try:
        for raw in sys.stdin:
            fields = raw.rstrip("\n").split("\t")
            if len(fields) == 3 and fields[0] == "APPROVAL":
                manager.resolve(fields[1], fields[2])
                continue
            if len(fields) == 2 and fields[0] == "CANCEL" and fields[1]:
                if fields[1] == current_request_id and current_cancel is not None:
                    current_cancel.cancel()
                    manager.cancel_request(fields[1])
                    emit(fields[1], "turn.cancel_requested", "Stopping the active turn")
                continue
            if fields[0] in {"HISTORY", "NEW", "SESSIONS", "POINTS", "SELECT", "FORK"}:
                request_id = fields[1] if len(fields) > 1 and fields[1] else "?"
                try:
                    kind = fields[0]
                    expected = {"HISTORY": 2, "NEW": 2, "SESSIONS": 2, "POINTS": 3, "SELECT": 4, "FORK": 4}[kind]
                    if len(fields) != expected or request_id == "?":
                        raise ValueError("Invalid session request")
                    if kind in {"NEW", "SELECT", "FORK"} and current_turn is not None:
                        raise ValueError("A turn is already running")
                    if kind == "SESSIONS":
                        for item in SessionStore.catalog(agent.tools.root, session_root):
                            origin = (item.get("forked_from") or {}).get("session_id", "")
                            emit(request_id, "session.item", "\t".join([
                                "v1", item["session_id"], encode_text(item["title"]),
                                encode_text(item["updated_at"]), origin]))
                        emit(request_id, "session.list_done", "")
                    elif kind == "POINTS":
                        source = SessionStore.load(agent.tools.root, store.path.parent, fields[2])
                        for point in source.points():
                            emit(request_id, "session.point", "\t".join([
                                "v1", point["point_id"], encode_text(point["title"]), encode_text(point["at"])]))
                        emit(request_id, "session.points_done", "")
                    else:
                        if kind != "HISTORY":
                            if kind == "NEW":
                                candidate = SessionStore.create(agent.tools.root, session_root, activate=False)
                            else:
                                source = SessionStore.load(agent.tools.root, store.path.parent, fields[2])
                                candidate = (source.fork(fields[3], activate=False) if kind == "FORK"
                                             else source.select(fields[3], persist=False))
                            messages = candidate.restore_messages()
                            task_state = candidate.restore_frame()
                            candidate.activate()
                            store = candidate
                            agent.messages = messages
                            agent.frame = task_state
                            agent.summaries.restore(messages, candidate.summary_records())
                            agent.tools.output_root = store.output_dir
                            manager.reset_grants()
                            emit(request_id, "session.reset", "")
                        emit(request_id, "session.info", store.session_id)
                        for event_kind, message in store.history():
                            emit(request_id, event_kind, message)
                        emit(request_id, "history.done", "")
                except Exception as error:
                    emit(request_id, "error", str(error))
                    if fields[0] == "HISTORY":
                        emit(request_id, "done", "")
                if fields[0] != "HISTORY":
                    emit(request_id, "done", "")
                continue
            request_id = "?"
            try:
                _, request_id, prompt = parse_request(raw)
                if current_turn is not None:
                    emit(request_id, "error", "A turn is already running")
                    emit(request_id, "done", "")
                    continue
                current_turn = str(uuid.uuid4())
                current_request_id = request_id
                current_cancel = CancellationToken()
                worker = threading.Thread(target=run_chat,
                                          args=(request_id, prompt, current_turn, current_cancel), daemon=True)
                worker.start()
            except Exception as error:
                emit(request_id, "error", str(error))
                emit(request_id, "done", "")
    finally:
        manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
