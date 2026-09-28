import json, http.server, itertools, sys
c = itertools.count(1)

def sse(data):
    return "data: " + json.dumps(data) + "\n\n"

class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json(200, {"object": "list", "data": [{"id": "mock-model", "object": "model"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        i = next(c)
        try:
            req = json.loads(raw)
            print("[mock] call #%d stream=%r model=%r messages=%d tools=%d" % (
                i, req.get("stream"), req.get("model"),
                len(req.get("messages") or []), len(req.get("tools") or [])), flush=True)
        except Exception:
            print("[mock] unparsed:", raw[:300], flush=True)
        if not req.get("stream"):
            self._json(200, {"id": "chatcmpl-mock-%d" % i, "object": "chat.completion",
                "created": 0, "model": "mock-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "PONG"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
            return
        cid = "chatcmpl-mock-%d" % i
        body = sse({"id": cid, "object": "chat.completion.chunk", "created": 0, "model": "mock-model",
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                 "finish_reason": None}]})
        body += sse({"id": cid, "object": "chat.completion.chunk", "created": 0, "model": "mock-model",
                    "choices": [{"index": 0, "delta": {"content": "PONG"}, "finish_reason": None}]})
        body += sse({"id": cid, "object": "chat.completion.chunk", "created": 0, "model": "mock-model",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
        body += "data: [DONE]\n\n"
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)
    def log_message(self, *a): pass

http.server.ThreadingHTTPServer(("0.0.0.0", 4000), H).serve_forever()
