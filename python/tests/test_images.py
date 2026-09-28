import base64
import io
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

from mupyjava.agent import Agent
from mupyjava.cancel import CancellationToken, TurnCancelled
from mupyjava.capabilities import ModelCapabilities
from mupyjava.image_ops import MAX_SOURCE_BYTES
from mupyjava.judge import DecisionEngine
from mupyjava.model import ChatCompletionsModel, prepare_messages
from mupyjava.sessions import SessionStore
from mupyjava.tool_result import ImageContent
from mupyjava.tools import WorkspaceTools

try:
    from PIL import Image
except ImportError:
    Image = None


# Tiny PNG allows capability and transport tests without the optional decoder.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aN2sAAAAASUVORK5CYII=")
VISION = ModelCapabilities(supports_images=True)


def encoded_image(size=(12, 8), format="PNG", **kwargs):
    output = io.BytesIO()
    Image.new("RGB", size, (50, 120, 200)).save(output, format=format, **kwargs)
    return output.getvalue()


class ImageTests(unittest.TestCase):
    @unittest.skipIf(Image is None, "Install optional Pillow for image-processing tests")
    def test_formats_detected_by_bytes_and_bmp_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root)
            formats = ["PNG", "JPEG", "GIF", "BMP"]
            if "WEBP" in Image.registered_extensions().values():
                formats.append("WEBP")
            for format in formats:
                with self.subTest(format=format):
                    (root / "misnamed.txt").write_bytes(encoded_image(format=format))
                    result = tools.execute_result("read_file", {"path": "misnamed.txt"}, capabilities=VISION)
                    self.assertFalse(result.is_error)
                    self.assertEqual(result.details["width"], 12)
                    self.assertEqual(result.details["height"], 8)
                    self.assertEqual(result.details["mime_type"], "image/png" if format == "BMP" else "image/" + format.lower())
                    image = next(block for block in result.content if isinstance(block, ImageContent))
                    with Image.open(io.BytesIO(base64.b64decode(image.data))) as decoded:
                        decoded.load()
                        self.assertEqual(decoded.size, (12, 8))
                    self.assertNotIn(image.data, result.text)
            with self.assertRaises(ValueError):
                tools.execute_result("read_file", {"path": "misnamed.txt", "limit": 1}, capabilities=VISION)
            (root / "fake.png").write_text("really text\n")
            self.assertEqual(tools.execute("read_file", {"path": "fake.png"}), "really text\n")
            (root / "bmp-prefix.txt").write_text("BM is ordinary text, not an image header.\n")
            self.assertEqual(tools.execute("read_file", {"path": "bmp-prefix.txt"}),
                             "BM is ordinary text, not an image header.\n")

    @unittest.skipIf(Image is None, "Install optional Pillow for image-processing tests")
    def test_resize_byte_budget_alpha_and_exif_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root)
            (root / "wide").write_bytes(encoded_image(size=(2400, 1200)))
            result = tools.execute_result("read_file", {"path": "wide"}, capabilities=VISION)
            self.assertEqual((result.details["width"], result.details["height"]), (2000, 1000))
            self.assertTrue(result.details["processed"])
            rgba = Image.new("RGBA", (120, 120), (20, 40, 60, 100))
            output = io.BytesIO()
            rgba.save(output, format="PNG")
            (root / "alpha").write_bytes(output.getvalue())
            small = ModelCapabilities(True, max_image_dimension=40, max_image_base64_bytes=180)
            result = tools.execute_result("read_file", {"path": "alpha"}, capabilities=small)
            block = next(block for block in result.content if isinstance(block, ImageContent))
            self.assertLessEqual(len(block.data), 180)
            with Image.open(io.BytesIO(base64.b64decode(block.data))) as decoded:
                self.assertEqual(decoded.mode, "RGBA")
                self.assertEqual(decoded.getpixel((0, 0))[3], 100)
            exif = Image.Exif()
            exif[274] = 6
            (root / "rotated").write_bytes(encoded_image(size=(12, 8), format="JPEG", exif=exif))
            result = tools.execute_result("read_file", {"path": "rotated"}, capabilities=VISION)
            self.assertEqual((result.details["width"], result.details["height"]), (8, 12))
            self.assertTrue(result.details["processed"])
            with self.assertRaisesRegex(ValueError, "attachment limit"):
                tools.execute_result("read_file", {"path": "alpha"}, capabilities=ModelCapabilities(True, 1, 1))

    def test_text_only_and_missing_decoder_are_explicit_omissions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "image").write_bytes(PNG)
            tools = WorkspaceTools(root)
            with patch.dict("sys.modules", {"PIL": None}):
                result = tools.execute_result("read_file", {"path": "image"})
                self.assertEqual(result.details["omission_reason"], "text_only_model")
                self.assertEqual(len(result.content), 1)
                result = tools.execute_result("read_file", {"path": "image"}, capabilities=VISION)
                self.assertEqual(result.details["omission_reason"], "missing_image_processor")
            with patch.dict(os.environ, {"MU_MODEL": "unknown", "MU_MODEL_SUPPORTS_IMAGES": "true"}):
                self.assertTrue(ChatCompletionsModel.from_environment().capabilities.supports_images)
            with patch.dict(os.environ, {"MU_MODEL": "unknown", "MU_MODEL_SUPPORTS_IMAGES": "maybe"}):
                with self.assertRaises(ValueError):
                    ChatCompletionsModel.from_environment()

    @unittest.skipIf(Image is None, "Install optional Pillow for image-processing tests")
    def test_corrupt_animated_and_pixel_bomb_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = WorkspaceTools(root)
            (root / "bad").write_bytes(b"\x89PNG\r\n\x1a\n" + b"bad" * 10)
            with self.assertRaises((ValueError, OSError)):
                tools.execute_result("read_file", {"path": "bad"}, capabilities=VISION)
            stream = io.BytesIO()
            frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
            frames[0].save(stream, format="GIF", save_all=True, append_images=frames[1:], duration=50, loop=0)
            (root / "animated").write_bytes(stream.getvalue())
            result = tools.execute_result("read_file", {"path": "animated"}, capabilities=VISION)
            self.assertEqual(result.details["omission_reason"], "animated_image")
            (root / "pixels").write_bytes(encoded_image(size=(20, 20)))
            with patch("mupyjava.image_ops.MAX_SOURCE_PIXELS", 100):
                with self.assertRaisesRegex(ValueError, "pixel"):
                    tools.execute_result("read_file", {"path": "pixels"}, capabilities=VISION)

    def test_binary_backend_is_bounded_cancellable_and_confined(self):
        class BinaryBackend:
            def __init__(self):
                self.limits = []

            def read_bytes(self, path, limit, cancel=None):
                self.limits.append(limit)
                return PNG[:limit]

        class ImageBackend:
            def process(self, data, mime, capabilities, cancel=None):
                from mupyjava.tool_result import ToolResult, TextContent
                return ToolResult((TextContent("Remote image"), ImageContent(base64.b64encode(data).decode(), mime)))

        with tempfile.TemporaryDirectory() as directory:
            backend = BinaryBackend()
            tools = WorkspaceTools(Path(directory), file_ops=backend, image_ops=ImageBackend())
            result = tools.execute_result("read_file", {"path": "remote"}, capabilities=VISION)
            self.assertEqual(result.text, "Remote image")
            self.assertEqual(backend.limits, [32, MAX_SOURCE_BYTES + 1])
            self.assertFalse((Path(directory) / "remote").exists())
            with self.assertRaises(PermissionError):
                tools.execute_result("read_file", {"path": "../escape"}, capabilities=VISION)
            token = CancellationToken()
            token.cancel()
            with self.assertRaises(TurnCancelled):
                tools.execute_result("read_file", {"path": "remote"}, capabilities=VISION, cancel=token)
            self.assertEqual(len(backend.limits), 2)
            with patch.object(backend, "read_bytes", return_value=PNG + b"x" * 33):
                with self.assertRaisesRegex(ValueError, "bounded bytes"):
                    tools.execute_result("read_file", {"path": "remote"}, capabilities=VISION)
            def oversize(path, limit, cancel=None):
                return PNG[:limit] if limit == 32 else PNG + b"x" * MAX_SOURCE_BYTES
            with patch.object(backend, "read_bytes", side_effect=oversize):
                with self.assertRaisesRegex(ValueError, "20 MiB"):
                    tools.execute_result("read_file", {"path": "remote"}, capabilities=VISION)

    @unittest.skipIf(Image is None, "Install optional Pillow for image-processing tests")
    def test_http_batch_attachment_order_and_session_restore_to_text_model(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(payload)
                if len(requests) == 1:
                    calls = [{"id": name, "type": "function", "function": {"name": name,
                             "arguments": json.dumps(arguments)}} for name, arguments in (
                        ("read_file", {"path": "picture"}), ("list_files", {"path": "."}))]
                    message = {"role": "assistant", "content": None, "tool_calls": calls}
                else:
                    message = {"role": "assistant", "content": "Image received."}
                body = json.dumps({"choices": [{"message": message}]}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server, tempfile.TemporaryDirectory() as directory:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = Path(directory)
                (root / "picture").write_bytes(encoded_image())
                store = SessionStore.open(root, root / "sessions", resume=False)
                store.append("turn.started", {}, "turn")
                endpoint = f"http://127.0.0.1:{server.server_port}/v1"
                agent = Agent(ChatCompletionsModel(endpoint, "", "vision", capabilities=VISION),
                              WorkspaceTools(root), DecisionEngine("off"))
                records = []
                list(agent.run("Inspect picture", on_message=lambda message:
                     store.append("message", {"message": message}, "turn"),
                     on_tool_event=lambda call, kind, payload: records.append((kind, payload))))
                store.append("turn.completed", {}, "turn")
                wire_messages = requests[1]["messages"]
                self.assertEqual([message["role"] for message in wire_messages],
                                 ["system", "user", "assistant", "tool", "tool", "user"])
                self.assertTrue(wire_messages[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
                self.assertIn("read_file", wire_messages[-1]["content"][0]["text"])
                self.assertTrue(all("image_blocks" not in message for message in wire_messages))
                result = next(payload for kind, payload in records if kind == "tool.result" and payload["tool"] == "read_file")
                self.assertEqual(result["content"][1]["type"], "image")
                restored = SessionStore.open(root, root / "sessions", resume=True).restore_messages()
                self.assertEqual(restored, agent.messages)
                saved_blocks = next(message["image_blocks"] for message in restored if "image_blocks" in message)
                text_agent = Agent(ChatCompletionsModel(endpoint, "", "text"), WorkspaceTools(root), DecisionEngine("off"))
                text_agent.messages = restored
                list(text_agent.run("Continue"))
                text_request = requests[2]["messages"]
                self.assertFalse(any(isinstance(message.get("content"), list) for message in text_request))
                self.assertTrue(any("Image omitted" in str(message.get("content")) for message in text_request))
                self.assertEqual(next(message["image_blocks"] for message in restored if "image_blocks" in message), saved_blocks)
                # A vision model can reuse the stored attachment without reading the file again.
                (root / "picture").unlink()
                self.assertTrue(any(isinstance(message.get("content"), list) for message in prepare_messages(restored, VISION)))
            finally:
                server.shutdown()
                thread.join(timeout=5)

    def test_invalid_image_content_is_rejected(self):
        for data, mime in (("%%%", "image/png"), ("", "image/png"), ("YQ==", "image/bmp")):
            with self.subTest(data=data, mime=mime), self.assertRaises(ValueError):
                ImageContent(data, mime)
        with self.assertRaises(ValueError):
            ModelCapabilities(True, max_image_dimension=0)
