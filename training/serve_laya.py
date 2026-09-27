"""Serve a fine-tuned Laya judge on lab loopback for SSH-tunneled shadow runs."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from mupyjava.judge import LayaBooleanJudge


def handler_for(judge):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/health":
                self.send_error(404)
                return
            self._reply(200, {"status": "ready"})

        def do_POST(self):
            if self.path != "/judge":
                self.send_error(404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 100_000:
                    raise ValueError("Request body must be at most 100 KB")
                payload = json.loads(self.rfile.read(size))
                question, state = payload["question"], payload["state"]
                if not isinstance(question, str) or not isinstance(state, dict):
                    raise ValueError("Expected a question and a state object")
                result = judge.evaluate(question, state)
                self._reply(200, {"answer": result.answer, "probability": result.probability})
            except (KeyError, TypeError, ValueError) as error:
                self._reply(400, {"error": str(error)})

        def _reply(self, status, body):
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format, *args):
            return  # Prompts and state must not appear in server logs.

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--port", type=int, default=18765)
    args = parser.parse_args()
    judge = LayaBooleanJudge(args.checkpoint, device=args.device)
    server = HTTPServer(("127.0.0.1", args.port), handler_for(judge))
    print(f"Laya judge ready on 127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
