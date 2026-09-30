"""Isolated Celery smoke worker with a loopback-only scripted chat endpoint."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from uuid import uuid4

from app.celery_app import app


class ChatHandler(BaseHTTPRequestHandler):
    calls = 0
    lock = Lock()

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 2_000_000:
            self.send_error(413)
            return
        request = json.loads(self.rfile.read(size))
        if request.get("model") != "docucheck-smoke-model":
            self.send_error(400)
            return
        with self.lock:
            type(self).calls += 1
            number = type(self).calls
        evidence_id = os.environ["DOCUCHECK_SMOKE_EVIDENCE_ID"]
        if number == 1:
            name, arguments = "search_evidence", {
                "query": "已装内存", "supplier": "vendor-a",
            }
        elif number == 2:
            name, arguments = "read_source", {"evidence_ids": [evidence_id]}
        elif number == 3:
            name, arguments = "submit_answer", {
                "kind": "evidence", "evidence_ids": [evidence_id],
            }
        else:
            self.send_error(400)
            return
        response = {
            "id": f"chatcmpl-smoke-{uuid4().hex}",
            "object": "chat.completion",
            "created": 0,
            "model": "docucheck-smoke-model",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": f"smoke-call-{number}",
                        "type": "function",
                        "function": {
                            "name": name, "arguments": json.dumps(arguments),
                        },
                    }],
                },
            }],
            "usage": {
                "prompt_tokens": 100, "completion_tokens": 20,
                "total_tokens": 120,
            },
        }
        payload = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        print(f"smoke_model_call={number}", flush=True)

    def log_message(self, format, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 18742), ChatHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    print("smoke_model_ready", flush=True)
    app.worker_main([
        "worker", "--loglevel=INFO", "--pool=solo", "--concurrency=1",
        "--hostname=docucheck-smoke@worker", "-Q", "runs",
    ])


if __name__ == "__main__":
    main()
