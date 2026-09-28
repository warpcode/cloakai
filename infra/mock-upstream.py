"""A minimal OpenAI-compatible upstream, for exercising the proxy path without a provider key.

This exists so `isolation-tests.sh` test 8 can prove a REAL model call end to end —
opencode -> litellm -> HTTP -> response — rather than skipping the one test that
demonstrates the container can still do its actual job.

It is not a mock of cloakai. It sits behind the real proxy, so the proxy, the
network, the credentials and the isolation are all genuinely exercised. Only the
final model call is canned, and it says so in the response.

Not used outside the `test` compose profile.
"""

import itertools
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "4001"))
REPLY = os.environ.get("MOCK_REPLY", "PONG")

counter = itertools.count(1)


def sse(data) -> str:
    return "data: " + json.dumps(data) + "\n\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list",
                             "data": [{"id": "default", "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except ValueError:
            req = {}

        n = next(counter)
        print(f"[upstream] call #{n} model={req.get('model')!r} "
              f"stream={req.get('stream')} messages={len(req.get('messages') or [])}",
              flush=True)

        if not req.get("stream"):
            self._json(200, {
                "id": f"chatcmpl-upstream-{n}", "object": "chat.completion",
                "created": 0, "model": req.get("model", "default"),
                "choices": [{"index": 0,
                             "message": {"role": "assistant", "content": REPLY},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            })
            return

        cid = f"chatcmpl-upstream-{n}"
        payload = (
            sse({"id": cid, "object": "chat.completion.chunk", "created": 0,
                 "model": req.get("model", "default"),
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                              "finish_reason": None}]})
            + sse({"id": cid, "object": "chat.completion.chunk", "created": 0,
                   "model": req.get("model", "default"),
                   "choices": [{"index": 0, "delta": {"content": REPLY},
                                "finish_reason": None}]})
            + sse({"id": cid, "object": "chat.completion.chunk", "created": 0,
                   "model": req.get("model", "default"),
                   "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
            + "data: [DONE]\n\n"
        )
        body = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"[upstream] listening on 0.0.0.0:{PORT}, replying {REPLY!r}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
