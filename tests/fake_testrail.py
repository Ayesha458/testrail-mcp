"""A tiny in-memory stand-in for the TestRail API v2, for tests only."""

from __future__ import annotations

import base64
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

USER, KEY = "tester@example.com", "test-key"

FIELDS = [
    {"system_name": "custom_steps_separated", "label": "Steps", "type_id": 10, "template_ids": [2],
     "configs": [{"context": {"is_global": True}, "options": {"is_required": False}}]},
    {"system_name": "custom_automation_type", "label": "Automation Type", "type_id": 6, "template_ids": [],
     "configs": [{"context": {"is_global": True},
                  "options": {"is_required": True, "default_value": "0",
                              "items": "0, None\n1, Automated\n2, Not Automatable\n3, Not Automated"}}]},
    {"system_name": "custom_status", "label": "Status", "type_id": 6, "template_ids": [2],
     "configs": [{"context": {"is_global": False, "project_ids": [7]},
                  "options": {"is_required": False, "items": "1, Draft\n2, Review\n3, Ready"}}]},
    {"system_name": "custom_other_template", "label": "Other", "type_id": 1, "template_ids": [1],
     "configs": [{"context": {"is_global": True}, "options": {"is_required": True}}]},
]


class State:
    def __init__(self):
        self.cases: dict[int, dict] = {}
        self.attachments: list[dict] = []
        self.next_id = 1000
        self.calls: list[str] = []
        self.rate_limit_once = False


def make_handler(state: State):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj, headers=None):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _route(self, method):
            auth = self.headers.get("Authorization", "")
            if auth != "Basic " + base64.b64encode(f"{USER}:{KEY}".encode()).decode():
                return self._send(401, {"error": "Authentication failed: invalid or missing user/password or session cookie."})
            m = re.match(r"^/index\.php\?/api/v2/(.+)$", self.path)
            if not m:
                return self._send(404, {"error": "bad path"})
            endpoint = m.group(1)
            state.calls.append(f"{method} {endpoint}")
            if state.rate_limit_once:
                state.rate_limit_once = False
                return self._send(429, {"error": "rate limit"}, {"Retry-After": "0"})
            name, _, rest = endpoint.partition("/")
            arg, _, qs = rest.partition("&")
            q = {k: v[0] for k, v in parse_qs(qs).items()}
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""

            if method == "GET" and name.startswith("get_user_by_email"):
                return self._send(200, {"id": 1, "name": "Test User", "email": USER})
            if method == "GET" and name == "get_projects":
                return self._send(200, {"offset": 0, "projects": [
                    {"id": 7, "name": "Demo", "suite_mode": 1, "is_completed": False},
                    {"id": 8, "name": "Old", "suite_mode": 1, "is_completed": True}], "_links": {"next": None}})
            if method == "GET" and name == "get_project":
                return self._send(200, {"id": int(arg), "name": "Demo", "suite_mode": 1})
            if method == "GET" and name == "get_suites":
                return self._send(200, [{"id": 11, "name": "Master"}])
            if method == "GET" and name == "get_sections":
                offset = int(q.get("offset", 0))
                pages = [
                    [{"id": 1, "name": "Root", "parent_id": None, "display_order": 1},
                     {"id": 3, "name": "Login", "parent_id": 1, "display_order": 2}],
                    [{"id": 2, "name": "Editor", "parent_id": 1, "display_order": 1},
                     {"id": 4, "name": "Other root", "parent_id": None, "display_order": 2}],
                ]
                page = pages[0] if offset == 0 else pages[1]
                nxt = f"/api/v2/get_sections/{arg}&offset=2" if offset == 0 else None
                return self._send(200, {"offset": offset, "sections": page, "_links": {"next": nxt}})
            if method == "GET" and name == "get_cases":
                sec = int(q["section_id"])
                return self._send(200, {"cases": [{"id": i, "title": c["title"], "refs": c.get("refs")}
                                                  for i, c in state.cases.items() if c["section"] == sec],
                                        "_links": {"next": None}})
            if method == "GET" and name == "get_case_fields":
                return self._send(200, FIELDS)
            if method == "POST" and name == "add_case":
                data = json.loads(raw)
                if "custom_automation_type" not in data:
                    return self._send(400, {"error": "Field :custom_automation_type is a required field."})
                if not isinstance(data["custom_automation_type"], int):
                    return self._send(400, {"error": "Field :custom_automation_type is not a valid integer."})
                if int(arg) == 999:
                    return self._send(400, {"error": "Field :section_id is not a valid section."})
                state.next_id += 1
                state.cases[state.next_id] = {**data, "section": int(arg)}
                return self._send(200, {"id": state.next_id, **data})
            if method == "POST" and name == "add_attachment_to_case":
                ctype = self.headers.get("Content-Type", "")
                assert ctype.startswith("multipart/form-data; boundary=")
                assert b'name="attachment"' in raw
                state.next_id += 1
                state.attachments.append({"case": int(arg), "id": state.next_id, "bytes": len(raw)})
                return self._send(200, {"attachment_id": state.next_id})
            if method == "POST" and name == "update_case":
                state.cases[int(arg)].update(json.loads(raw))
                return self._send(200, {"id": int(arg)})
            return self._send(400, {"error": f"unknown endpoint {endpoint}"})

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return H


def start():
    state = State()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, state, f"http://127.0.0.1:{srv.server_address[1]}"
