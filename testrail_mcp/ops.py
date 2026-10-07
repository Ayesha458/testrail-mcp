"""TestRail operations used by the MCP tools. Kept separate from the server so they are easy to test."""

from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path

from .client import TestRail, TestRailError

FIELD_TYPES = {1: "string", 2: "integer", 3: "text", 4: "url", 5: "checkbox", 6: "dropdown",
               7: "user", 8: "date", 9: "milestone", 10: "steps", 11: "steps", 12: "multiselect"}

STOP_ERRORS = ("is a required field", "(401)", "(403)", "is not a valid field", "not a valid section")


def _id(value, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise TestRailError(f"{what} is missing or not a number (got {value!r}).") from None


def _project_id(tr: TestRail, project_id=None) -> int:
    return _id(project_id or tr.s.project_id, "Project ID (pass project_id or set TESTRAIL_PROJECT_ID)")


# ---------------------------------------------------------------- browsing
def connection(tr: TestRail) -> dict:
    user = {}
    try:
        me = tr.request("GET", "get_user_by_email&email=" + urllib.parse.quote(tr.s.user))
        user = {"id": me.get("id"), "name": me.get("name")}
    except TestRailError as e:
        if "(401)" in str(e):
            raise
    out = {"ok": True, "url": tr.s.url, "signed_in_as": user.get("name") or "(unknown)",
           "defaults": {"project_id": tr.s.project_id or None, "suite_id": tr.s.suite_id or None,
                        "section_id": tr.s.section_id or None, "template_id": tr.s.template_id,
                        "fields": tr.s.default_fields or {}}}
    if tr.s.project_id:
        p = tr.request("GET", f"get_project/{_project_id(tr)}")
        out["project"] = {"id": p.get("id"), "name": p.get("name"), "suite_mode": p.get("suite_mode")}
        out["suites"] = list_suites(tr)
    return out


def list_projects(tr: TestRail) -> list[dict]:
    return [{"id": p["id"], "name": p["name"], "suite_mode": p.get("suite_mode")}
            for p in tr.get_all("get_projects", "projects") if not p.get("is_completed")]


def list_suites(tr: TestRail, project_id=None) -> list[dict]:
    data = tr.request("GET", f"get_suites/{_project_id(tr, project_id)}")
    if isinstance(data, dict):
        data = data.get("suites", [])
    return [{"id": s["id"], "name": s["name"]} for s in data]


def list_sections(tr: TestRail, project_id=None, suite_id=None, search: str = "") -> list[dict]:
    endpoint = f"get_sections/{_project_id(tr, project_id)}"
    suite_id = suite_id or tr.s.suite_id
    if suite_id:
        endpoint += f"&suite_id={_id(suite_id, 'suite_id')}"
    sections = tr.get_all(endpoint, "sections")

    by_parent: dict = {}
    for s in sections:
        by_parent.setdefault(s.get("parent_id"), []).append(s)
    for kids in by_parent.values():
        kids.sort(key=lambda s: s.get("display_order", 0))
    ordered: list[dict] = []

    def walk(parent, trail):
        for s in by_parent.get(parent, []):
            path = trail + [s["name"]]
            ordered.append({"id": s["id"], "path": " > ".join(path), "depth": len(trail)})
            walk(s["id"], path)

    walk(None, [])
    if search:
        q = search.lower()
        ordered = [s for s in ordered if q in s["path"].lower()]
    return ordered


def list_cases(tr: TestRail, section_id, project_id=None, suite_id=None) -> list[dict]:
    endpoint = f"get_cases/{_project_id(tr, project_id)}&section_id={_id(section_id, 'section_id')}"
    suite_id = suite_id or tr.s.suite_id
    if suite_id:
        endpoint += f"&suite_id={_id(suite_id, 'suite_id')}"
    return [{"id": c["id"], "title": c["title"], "refs": c.get("refs")}
            for c in tr.get_all(endpoint, "cases")]


def _parse_items(items) -> list[dict]:
    out = []
    for line in (items or "").splitlines():
        if "," in line:
            k, v = line.split(",", 1)
            out.append({"id": int(k) if k.strip().isdigit() else k.strip(), "label": v.strip()})
    return out


def case_fields(tr: TestRail, project_id=None, required_only: bool = False) -> list[dict]:
    """Custom case fields that apply to the configured template and project."""
    fields = tr.request("GET", "get_case_fields")
    if isinstance(fields, dict):
        fields = fields.get("case_fields", fields.get("fields", []))
    pid = _project_id(tr, project_id)
    tid = _id(tr.s.template_id, "TESTRAIL_TEMPLATE_ID")
    skip = {tr.s.steps_field, tr.s.preconds_field}
    out = []
    for f in fields:
        name = f.get("system_name") or ""
        if not name or name in skip or f.get("is_active") is False:
            continue
        if f.get("template_ids") and tid not in f["template_ids"]:
            continue
        configs = f.get("configs") or []
        cfg = next((c for c in configs
                    if (c.get("context") or {}).get("is_global")
                    or pid in ((c.get("context") or {}).get("project_ids") or [])), None)
        if cfg is None:
            continue
        opts = cfg.get("options") or {}
        required = bool(opts.get("is_required"))
        if required_only and not required:
            continue
        entry = {"name": name, "label": f.get("label") or name,
                 "type": FIELD_TYPES.get(f.get("type_id"), "string"), "required": required}
        if opts.get("default_value") not in (None, ""):
            entry["default"] = opts.get("default_value")
        items = _parse_items(opts.get("items"))
        if items:
            entry["options"] = items
        out.append(entry)
    return out


# ---------------------------------------------------------------- creating
def _convert(meta: dict | None, name: str, value):
    """Turn a friendly value (e.g. a dropdown label) into what TestRail expects."""
    if meta is None or value in (None, ""):
        return value
    ftype = meta["type"]
    options = meta.get("options") or []

    def one(v):
        if isinstance(v, bool) or isinstance(v, int):
            return v
        s = str(v).strip()
        if s.lstrip("-").isdigit():
            return int(s)
        for o in options:
            if o["label"].lower() == s.lower():
                return o["id"]
        raise TestRailError(
            f"'{v}' is not a valid value for {meta['label']} ({name}). "
            f"Options: " + ", ".join(f"{o['id']} {o['label']}" for o in options))

    if ftype in ("dropdown", "integer", "user", "milestone"):
        return one(value)
    if ftype == "multiselect":
        return [one(v) for v in (value if isinstance(value, list) else [value])]
    if ftype == "checkbox":
        return value in (True, "true", "True", "1", 1, "yes")
    return value


def _resolve_fields(tr: TestRail, fields: dict, project_id) -> dict:
    if not fields:
        return {}
    try:
        meta = {f["name"]: f for f in case_fields(tr, project_id)}
    except TestRailError:
        meta = {}
    return {k: _convert(meta.get(k), k, v) for k, v in fields.items() if v not in (None, "")}


def create_cases(tr: TestRail, cases: list[dict], section_id=None, refs: str = "",
                 fields: dict | None = None, images: dict | None = None,
                 image_placement: str = "expected", project_id=None, dry_run: bool = False) -> dict:
    """Create cases in a section. `images` (label -> {name, mime, data}) only comes from case-pack files."""
    section = _id(section_id or tr.s.section_id,
                  "Section ID (pass section_id, or set TESTRAIL_SECTION_ID)")
    if not cases:
        raise TestRailError("No cases to create.")
    if image_placement not in ("expected", "content"):
        raise TestRailError("image_placement must be 'expected' or 'content'.")
    images = images or {}
    merged = {**(tr.s.default_fields or {}), **(fields or {})}
    extra = _resolve_fields(tr, merged, project_id) if merged and not dry_run else dict(merged)
    refs = (refs or "").strip()

    payloads = []
    for i, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise TestRailError(f"Case {i} must be an object with title and steps.")
        title = (case.get("title") or "").strip()
        if not title:
            raise TestRailError(f"Case {i} has no title.")
        steps = [s for s in case.get("steps") or []
                 if s.get("content") or s.get("expected") or s.get("images")]
        if not steps:
            raise TestRailError(f"Case {i} ('{title}') has no steps.")
        missing = {lbl for s in steps for lbl in s.get("images") or []} - images.keys()
        if missing:
            raise TestRailError(f"Case {i} ('{title}') uses images not in the file: {sorted(missing)}")
        payload = {
            "title": title,
            "template_id": _id(tr.s.template_id, "TESTRAIL_TEMPLATE_ID"),
            tr.s.steps_field: [{"content": s.get("content", ""), "expected": s.get("expected", "")}
                               for s in steps],
        }
        if case.get("preconditions"):
            payload[tr.s.preconds_field] = case["preconditions"]
        case_refs = (case.get("refs") or refs or "").strip()
        if case_refs:
            payload["refs"] = case_refs
        payload.update(extra)
        payloads.append((payload, steps))

    if dry_run:
        return {"ok": True, "dry_run": True, "section_id": section,
                "would_create": [p for p, _ in payloads],
                "images_per_case": [sum(len(s.get("images") or []) for s in st) for _, st in payloads]}

    results, stopped = [], None
    for payload, steps in payloads:
        entry = {"title": payload["title"]}
        if stopped:
            entry.update({"ok": False, "error": "Not attempted (stopped after an earlier error)."})
            results.append(entry)
            continue
        try:
            created = tr.request("POST", f"add_case/{section}", payload)
            case_id = created["id"]
            entry.update({"case_id": case_id, "url": tr.case_url(case_id)})
            uploaded, warnings = {}, []
            for s in steps:
                for lbl in s.get("images") or []:
                    if lbl in uploaded:
                        continue
                    img = images[lbl]
                    try:
                        raw = base64.b64decode(img["data"])
                        uploaded[lbl] = tr.upload_attachment(
                            case_id, img.get("name") or f"{lbl}.png", raw, img.get("mime"))
                    except (TestRailError, ValueError, KeyError) as e:
                        warnings.append(f"{img.get('name', lbl)}: {e}")
            if uploaded:
                new_steps = []
                for s in steps:
                    step = {"content": s.get("content", ""), "expected": s.get("expected", "")}
                    md = "\n".join(tr.image_markdown(uploaded[l]) for l in s.get("images") or []
                                   if l in uploaded)
                    if md:
                        step[image_placement] = (step[image_placement] + "\n\n" + md).strip()
                    new_steps.append(step)
                tr.request("POST", f"update_case/{case_id}", {tr.s.steps_field: new_steps})
            entry.update({"ok": True, "images": len(uploaded)})
            if warnings:
                entry["warnings"] = warnings
        except TestRailError as e:
            msg = str(e)
            if "is a required field" in msg:
                msg += (" Call testrail_get_fields to see the options, then pass it in `fields` "
                        "or set TESTRAIL_DEFAULT_FIELDS.")
            entry.update({"ok": False, "error": msg})
            if any(k in str(e) for k in STOP_ERRORS):
                stopped = True
        results.append(entry)

    created_n = sum(1 for r in results if r.get("ok"))
    return {"ok": created_n == len(results), "created": created_n, "failed": len(results) - created_n,
            "section_id": section, "results": results}


# ---------------------------------------------------------------- case-pack files
def load_case_pack(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise TestRailError(f"File not found: {p}")
    try:
        pack = json.loads(p.read_text(encoding="utf-8-sig"))
    except ValueError as e:
        raise TestRailError(f"{p.name} is not valid JSON: {e}") from None
    if not isinstance(pack, dict) or not isinstance(pack.get("cases"), list):
        raise TestRailError(f"{p.name} has no 'cases' list, so it isn't a case-pack file.")
    if pack.get("format") not in (None, "testrail-case-pack"):
        raise TestRailError(f"{p.name} has format '{pack.get('format')}', expected 'testrail-case-pack'.")
    return pack


def upload_case_pack(tr: TestRail, path: str, section_id=None, fields: dict | None = None,
                     project_id=None, dry_run: bool = False) -> dict:
    pack = load_case_pack(path)
    result = create_cases(
        tr, pack["cases"],
        section_id=section_id or pack.get("section_id"),
        refs=pack.get("refs") or "",
        fields={**(pack.get("fields") or {}), **(fields or {})},
        images=pack.get("images") or {},
        image_placement=pack.get("image_placement") or "expected",
        project_id=project_id, dry_run=dry_run,
    )
    if pack.get("notes"):
        result["notes_from_file"] = pack["notes"]
    return result
