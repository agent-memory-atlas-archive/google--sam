#!/usr/bin/env python3
# A minimal OpenAI-compatible backend for e2e tests: one model, one canned
# completion. Standard library only, so it runs wherever python3 does.
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = "e2e-model"


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "e2e"}]})
            return
        self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            req = {}
        if self.path == "/v1/chat/completions" and req.get("model") == MODEL:
            self._json(200, {
                "id": "cmpl-e2e", "object": "chat.completion", "model": MODEL,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "hello from the mesh"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 4, "total_tokens": 5},
            })
            return
        self._json(404, {"error": {"message": "unknown model", "code": "model_not_found"}})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 0), Handler)
    print(f"listening on port {server.server_address[1]}", flush=True)
    server.serve_forever()
