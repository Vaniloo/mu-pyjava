import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.judge import DecisionEngine
from mupyjava.permissions import ApprovalManager
from mupyjava.registry import ToolDefinition
from mupyjava.tool_result import ToolResult
from mupyjava.tools import WorkspaceTools


PARAMETERS = {"type": "object", "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 10}},
              "required": ["value"], "additionalProperties": False}


class OneCallModel:
    def __init__(self, arguments=None):
        self.arguments = {"value": 2} if arguments is None else arguments
        self.schemas = []

    def complete(self, messages, tools):
        self.schemas = tools
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": "Done."}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "custom-1", "type": "function",
                "function": {"name": "custom", "arguments": json.dumps(self.arguments)}}]}


class RegistryTests(unittest.TestCase):
    def test_read_only_tool_schema_updates_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            model = OneCallModel()
            def run(arguments, context):
                context.check_cancelled()
                context.on_update("checking")
                self.assertEqual(context.resolve_path("local"), tools.root / "local")
                return ToolResult.from_text(str(arguments["value"] * 2), {"count": 1})
            definition = ToolDefinition("custom", "Double a value.", PARAMETERS, run, effect="read")
            tools.registry.register(definition)
            # Registration takes a snapshot; the caller cannot change advertised validation.
            definition.parameters["properties"]["value"]["maximum"] = 1
            agent = Agent(model, tools, DecisionEngine("off"))
            updates, events = [], []
            def unexpected_approval(*args):
                raise AssertionError("Read tool asked for mutation approval")
            transcript = list(agent.run("Double 2", approval=unexpected_approval,
                on_tool_update=lambda call, text: updates.append(text),
                on_tool_event=lambda call, kind, payload: events.append((kind, payload))))
            self.assertIn(("tool", "custom: 4"), transcript)
            self.assertEqual(updates, ["checking"])
            self.assertEqual(len(model.schemas), 14)
            self.assertEqual([kind for kind, _ in events], ["tool.started", "tool.completed", "tool.result"])
            self.assertEqual(events[-1][1]["details"]["count"], 1)
            with self.assertRaisesRegex(ValueError, "frozen"):
                tools.registry.register(ToolDefinition("later", "Later", PARAMETERS, run))

    def test_mutation_approval_fresh_and_builtin_grants_cannot_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory), allow_write=True, allow_command=True, allow_custom=True)
            ran, previews, answers = [], [], iter(("deny", "session", "once", "once"))
            def run(arguments, context):
                ran.append(arguments)
                context.resolve_path("effect.txt").write_text(str(arguments["value"]))
                return ToolResult.from_text("Written")
            tools.registry.register(ToolDefinition("custom", "Write a custom value.", PARAMETERS, run))
            def emit(request, kind, payload):
                if kind != "approval.request":
                    return
                fields = payload.split("\t")
                previews.append(base64.b64decode(fields[5]).decode())
                self.assertEqual(fields[3], "deny,once")
                self.assertTrue(manager.resolve(fields[1], next(answers)))
            manager = ApprovalManager(tools, emit, full_write=True, full_command=True)
            for index in range(4):
                agent = Agent(OneCallModel(), tools, DecisionEngine("off"))
                transcript = list(agent.run("Write custom value", approval=lambda call, name, args:
                    manager.request("request", call, name, args)))
                if index < 2:
                    self.assertFalse((tools.root / "effect.txt").exists())
                    self.assertTrue(any("User did not allow" in text for _, text in transcript))
            self.assertEqual(len(previews), 4)
            self.assertEqual(len(ran), 2)
            self.assertIn('"value": 2', previews[0])

    def test_default_cli_permission_floor_and_active_judge_veto(self):
        class Decline:
            def answer(self, question, state):
                return False
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            tools = WorkspaceTools(Path(directory), allow_write=True, allow_command=True)
            tools.registry.register(ToolDefinition("custom", "Mutate", PARAMETERS,
                                    lambda args, context: calls.append(args) or ToolResult.from_text("OK")))
            transcript = list(Agent(OneCallModel(), tools, DecisionEngine("off")).run("Do it"))
            self.assertTrue(any("--allow-custom-tools" in text for _, text in transcript))
            tools.allow_custom = True
            transcript = list(Agent(OneCallModel(), tools, DecisionEngine("active", Decline())).run(
                "Do it", approval=lambda *args: self.fail("Judge veto reached approval")))
            self.assertTrue(any("Judge declined" in text for _, text in transcript))
            self.assertEqual(calls, [])

    def test_invalid_arguments_do_not_reach_approval_or_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory), allow_custom=True)
            tools.registry.register(ToolDefinition("custom", "Mutate", PARAMETERS,
                lambda *args: self.fail("Invalid arguments reached the handler")))
            for arguments in ({}, {"value": True}, {"value": 0}, {"value": 11}, {"value": 2, "extra": 1}):
                with self.subTest(arguments=arguments):
                    events = []
                    list(Agent(OneCallModel(arguments), tools, DecisionEngine("off")).run("Do it",
                         approval=lambda *args: self.fail("Invalid arguments reached approval"),
                         on_tool_event=lambda call, kind, payload: events.append((kind, payload))))
                    self.assertEqual([kind for kind, _ in events], ["tool.result"])
                    self.assertTrue(events[0][1]["is_error"])

    def test_judge_and_approval_callbacks_cannot_rewrite_executed_arguments(self):
        class MutatingJudge:
            def answer(self, question, state):
                state["arguments"]["value"] = 9
                return True

        with tempfile.TemporaryDirectory() as directory:
            seen = []
            tools = WorkspaceTools(Path(directory), allow_custom=True)
            tools.registry.register(ToolDefinition("custom", "Mutate", PARAMETERS,
                                    lambda args, context: seen.append(args["value"]) or ToolResult.from_text("OK")))
            def approve(call, name, arguments):
                self.assertEqual(arguments, {"value": 2})
                arguments["value"] = 10
                return True
            list(Agent(OneCallModel(), tools, DecisionEngine("active", MutatingJudge())).run("Do it", approval=approve))
            self.assertEqual(seen, [2])

    def test_custom_cancellation_is_terminal_and_errors_are_results(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            token = CancellationToken()
            def run(arguments, context):
                context.on_update("started")
                # Post-handler checking also detects a handler that returns after cancellation.
                return ToolResult.from_text("late result")
            tools.registry.register(ToolDefinition("custom", "Check", PARAMETERS, run, effect="read"))
            events = []
            with self.assertRaises(TurnCancelled):
                list(Agent(OneCallModel(), tools, DecisionEngine("off")).run("Check", cancel=token,
                     on_tool_update=lambda call, text: token.cancel(),
                     on_tool_event=lambda call, kind, payload: events.append((kind, payload))))
            self.assertEqual([kind for kind, _ in events], ["tool.started", "tool.cancelled"])
            for execute in (lambda *args: "wrong result", lambda *args: (_ for _ in ()).throw(RuntimeError("failed"))):
                other = WorkspaceTools(Path(directory))
                other.registry.register(ToolDefinition("custom", "Check", PARAMETERS, execute, effect="read"))
                events = []
                list(Agent(OneCallModel(), other, DecisionEngine("off")).run("Check",
                     on_tool_event=lambda call, kind, payload: events.append((kind, payload))))
                self.assertEqual([kind for kind, _ in events], ["tool.started", "tool.failed", "tool.result"])
                self.assertTrue(events[-1][1]["is_error"])

    def test_duplicate_reserved_names_and_unsupported_schemas_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            execute = lambda *args: ToolResult.from_text("OK")
            for name in ("read_file", "bash", "powershell"):
                with self.assertRaisesRegex(ValueError, "already registered"):
                    tools.registry.register(ToolDefinition(name, "Test", PARAMETERS, execute))
            tools.registry.register(ToolDefinition("custom", "Test", PARAMETERS, execute))
            with self.assertRaises(ValueError):
                tools.registry.register(ToolDefinition("custom", "Duplicate", PARAMETERS, execute))
            for schema in ({"type": "object", "$ref": "elsewhere"}, {"type": ["object", "null"]},
                           {"type": "object", "properties": {"x": {"type": "integer", "minimum": True}}},
                           {"type": "object", "required": ["missing"]},
                           {"type": "array"}, {"type": "object", "additionalProperties": {"type": "string"}}):
                with self.subTest(schema=schema), self.assertRaises(ValueError):
                    ToolDefinition("test", "Invalid", schema, execute)

    def test_module_loading_example_and_cli_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.txt").write_text("hello")
            tools = WorkspaceTools(root)
            tools.registry.load_module("mupyjava.extensions.file_digest")
            result = tools.execute_result("file_digest", {"path": "source.txt"})
            self.assertEqual(result.text, hashlib.sha256(b"hello").hexdigest())
            self.assertEqual(result.details["source_bytes"], 5)
            with self.assertRaises(ValueError):
                tools.registry.load_module("mupyjava.extensions.file_digest")
            with self.assertRaises(ValueError):
                tools.registry.load_module("../untrusted.py")
            repository = Path(__file__).resolve().parents[2]
            env = os.environ.copy()
            env.update({"PYTHONPATH": str(repository / "python"), "MU_MODEL_BACKEND": "echo", "MU_JUDGE_MODE": "off",
                        "MU_TOOL_MODULES": "mupyjava.extensions.file_digest"})
            command = [sys.executable, "-m", "mupyjava", "--workspace", directory, "--prompt", "hello",
                       "--tool-module", "mupyjava.extensions.file_digest"]
            result = subprocess.run(command, cwd=repository, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            env["MU_TOOL_MODULES"] = "module_that_does_not_exist"
            result = subprocess.run(command, cwd=repository, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 2)
            self.assertIn("Could not load trusted tool module", result.stderr)

    def test_bound_backend_handler_identity_and_failed_module_rollback(self):
        class Backend:
            def __init__(self):
                self.lock = threading.Lock()
                self.calls = 0

            def execute(self, arguments, context):
                with self.lock:
                    self.calls += 1
                return ToolResult.from_text("OK")

        with tempfile.TemporaryDirectory() as directory:
            tools = WorkspaceTools(Path(directory))
            backend = Backend()
            tools.registry.register(ToolDefinition("bound", "Bound backend", PARAMETERS, backend.execute, effect="read"))
            self.assertFalse(tools.execute_result("bound", {"value": 2}).is_error)
            self.assertEqual(backend.calls, 1)

            class PartialModule:
                def register_tools(self, registry):
                    registry.register(ToolDefinition("partial", "Partial", PARAMETERS, backend.execute))
                    raise RuntimeError("Registration failed")
            with patch("mupyjava.registry.importlib.import_module", return_value=PartialModule()):
                with self.assertRaises(ValueError):
                    tools.registry.load_module("partial_module")
            self.assertIsNone(tools.registry.resolve("partial"))
            self.assertIsNotNone(tools.registry.resolve("bound"))
