"""Small TestRail API v2 client using only the Python standard library.

All connection details come from environment variables, never from code:

    TESTRAIL_URL       e.g. https://yourcompany.testrail.io
    TESTRAIL_USER      the email you sign in to TestRail with
    TESTRAIL_API_KEY   TestRail > My Settings > API Keys
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field


class TestRailError(Exception):
    """A TestRail call failed in a way the user can act on."""

    __test__ = False  # stop pytest from collecting this as a test class


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


@dataclass
class Settings:
    url: str = ""
    user: str = ""
    api_key: str = field(default="", repr=False)  # never printed
    project_id: str = ""
    suite_id: str = ""
    section_id: str = ""
    template_id: str = "2"
    steps_field: str = "custom_steps_separated"
    preconds_field: str = "custom_preconds"
    default_fields: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        raw_defaults = _env("TESTRAIL_DEFAULT_FIELDS")
        try:
            defaults = json.loads(raw_defaults) if raw_defaults else {}
            if not isinstance(defaults, dict):
                raise ValueError
        except ValueError:
            raise TestRailError(
                'TESTRAIL_DEFAULT_FIELDS must be a JSON object, e.g. {"custom_automation_type": 3}'
            )
        return cls(
            url=_env("TESTRAIL_URL").rstrip("/"),
            user=_env("TESTRAIL_USER"),
            api_key=_env("TESTRAIL_API_KEY"),
            project_id=_env("TESTRAIL_PROJECT_ID"),
            suite_id=_env("TESTRAIL_SUITE_ID"),
            section_id=_env("TESTRAIL_SECTION_ID"),
            template_id=_env("TESTRAIL_TEMPLATE_ID", "2"),
            steps_field=_env("TESTRAIL_STEPS_FIELD", "custom_steps_separated"),
            preconds_field=_env("TESTRAIL_PRECONDS_FIELD", "custom_preconds"),
            default_fields=defaults,
        )

    def missing(self) -> list[str]:
        need = {"TESTRAIL_URL": self.url, "TESTRAIL_USER": self.user, "TESTRAIL_API_KEY": self.api_key}
        return [k for k, v in need.items() if not v]


class TestRail:
    __test__ = False

    def __init__(self, settings: Settings, timeout: float = 120, max_retries: int = 3):
        self.s = settings
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------------ core
    def _auth_header(self) -> str:
        token = base64.b64encode(f"{self.s.user}:{self.s.api_key}".encode()).decode()
        return f"Basic {token}"

    def request(self, method: str, endpoint: str, payload=None, file=None):
        """Call `index.php?/api/v2/<endpoint>`.

        `file` is an optional (filename, bytes, mime) tuple sent as multipart field "attachment".
        """
        missing = self.s.missing()
        if missing:
            raise TestRailError(
                "Not configured: set " + ", ".join(missing) + " in the MCP server's env settings."
            )
        url = f"{self.s.url}/index.php?/api/v2/{endpoint}"
        headers = {"Authorization": self._auth_header(), "Accept": "application/json"}
        body = None
        if file is not None:
            boundary = uuid.uuid4().hex
            name, data, mime = file
            safe_name = name.replace('"', "")
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="attachment"; filename="{safe_name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"

        what = endpoint.split("/")[0].split("&")[0]
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode("utf-8", "replace")
                    return json.loads(text) if text.strip() else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < self.max_retries:  # TestRail rate limit
                    time.sleep(min(int(e.headers.get("Retry-After") or 5), 60))
                    continue
                text = e.read().decode("utf-8", "replace")
                try:
                    msg = json.loads(text).get("error") or text
                except ValueError:
                    msg = text[:300]
                if e.code == 401:
                    msg += " (check TESTRAIL_USER and TESTRAIL_API_KEY, and that the API is enabled)"
                raise TestRailError(f"TestRail {what} failed ({e.code}): {msg}") from None
            except urllib.error.URLError as e:
                raise TestRailError(f"Could not reach TestRail at {self.s.url}: {e.reason}") from None
            except ValueError:
                return {}
        raise TestRailError(f"TestRail {what} kept hitting the rate limit; try again in a minute.")

    def get_all(self, endpoint: str, key: str) -> list:
        """Follows pagination; also handles old TestRail versions that return a bare list."""
        out: list = []
        while endpoint:
            data = self.request("GET", endpoint)
            if isinstance(data, list):
                return data
            items = data.get(key, [])
            out.extend(items)
            nxt = (data.get("_links") or {}).get("next")
            endpoint = nxt.split("/api/v2/", 1)[-1] if nxt and items else None
        return out

    # --------------------------------------------------------------- helpers
    def case_url(self, case_id) -> str:
        return f"{self.s.url}/index.php?/cases/view/{case_id}"

    @staticmethod
    def image_markdown(attachment_id) -> str:
        return f"![](index.php?/attachments/get/{attachment_id})"

    def upload_attachment(self, case_id, name: str, data: bytes, mime: str | None = None):
        mime = mime or mimetypes.guess_type(name)[0] or "application/octet-stream"
        res = self.request("POST", f"add_attachment_to_case/{case_id}", file=(name, data, mime))
        return res.get("attachment_id") or res.get("id")
