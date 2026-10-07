import asyncio
import base64
import json

import pytest

from testrail_mcp import ops
from testrail_mcp.client import Settings, TestRail, TestRailError

from fake_testrail import KEY, USER, start

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


@pytest.fixture()
def fake(monkeypatch):
    srv, state, url = start()
    env = {"TESTRAIL_URL": url, "TESTRAIL_USER": USER, "TESTRAIL_API_KEY": KEY,
           "TESTRAIL_PROJECT_ID": "7", "TESTRAIL_SUITE_ID": "11"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    for k in ("TESTRAIL_SECTION_ID", "TESTRAIL_DEFAULT_FIELDS", "TESTRAIL_TEMPLATE_ID"):
        monkeypatch.delenv(k, raising=False)
    yield state
    srv.shutdown()


def tr():
    return TestRail(Settings.from_env())


CASE = {"title": "Verify login", "preconditions": "User exists",
        "steps": [{"content": "Open the login page", "expected": "Login form is shown"},
                  {"content": "Sign in", "expected": "Dashboard is shown"}]}


def test_settings_never_print_key(fake):
    assert KEY not in repr(Settings.from_env())


def test_missing_config(monkeypatch):
    monkeypatch.delenv("TESTRAIL_API_KEY", raising=False)
    monkeypatch.setenv("TESTRAIL_URL", "https://x.invalid")
    monkeypatch.setenv("TESTRAIL_USER", "a@b.c")
    with pytest.raises(TestRailError, match="TESTRAIL_API_KEY"):
        TestRail(Settings.from_env()).request("GET", "get_projects")


def test_bad_key(fake, monkeypatch):
    monkeypatch.setenv("TESTRAIL_API_KEY", "wrong")
    with pytest.raises(TestRailError, match="401"):
        ops.connection(tr())


def test_connection_and_browsing(fake):
    c = ops.connection(tr())
    assert c["signed_in_as"] == "Test User" and c["project"]["name"] == "Demo"
    assert c["suites"] == [{"id": 11, "name": "Master"}]
    assert [p["id"] for p in ops.list_projects(tr())] == [7]


def test_sections_tree_paginated_and_search(fake):
    secs = ops.list_sections(tr())
    assert [s["path"] for s in secs] == ["Root", "Root > Editor", "Root > Login", "Other root"]
    assert [s["id"] for s in ops.list_sections(tr(), search="edit")] == [2]


def test_fields_filtered_to_template_and_project(fake):
    fields = {f["name"]: f for f in ops.case_fields(tr())}
    assert set(fields) == {"custom_automation_type", "custom_status"}
    assert fields["custom_automation_type"]["options"][3] == {"id": 3, "label": "Not Automated"}
    assert [f["name"] for f in ops.case_fields(tr(), required_only=True)] == ["custom_automation_type"]


def test_required_field_error_stops_early(fake):
    r = ops.create_cases(tr(), [CASE, CASE], section_id=3)
    assert r["created"] == 0 and "testrail_get_fields" in r["results"][0]["error"]
    assert "Not attempted" in r["results"][1]["error"]
    assert sum(c.startswith("POST add_case") for c in fake.calls) == 1


def test_add_cases_with_label_value(fake):
    r = ops.create_cases(tr(), [CASE], section_id=3, refs="ABC-1",
                         fields={"custom_automation_type": "not automated"})
    assert r["ok"] and r["created"] == 1
    saved = fake.cases[r["results"][0]["case_id"]]
    assert saved["custom_automation_type"] == 3 and saved["refs"] == "ABC-1"
    assert saved["custom_preconds"] == "User exists" and saved["template_id"] == 2
    assert saved["custom_steps_separated"][1] == {"content": "Sign in", "expected": "Dashboard is shown"}
    assert r["results"][0]["url"].endswith(f"/index.php?/cases/view/{r['results'][0]['case_id']}")


def test_default_fields_from_env(fake, monkeypatch):
    monkeypatch.setenv("TESTRAIL_DEFAULT_FIELDS", '{"custom_automation_type": 3}')
    monkeypatch.setenv("TESTRAIL_SECTION_ID", "2")
    r = ops.create_cases(tr(), [CASE])
    assert r["ok"] and fake.cases[r["results"][0]["case_id"]]["section"] == 2


def test_bad_label_lists_options(fake):
    with pytest.raises(TestRailError, match="1 Automated"):
        ops.create_cases(tr(), [CASE], section_id=3, fields={"custom_automation_type": "maybe"})


def test_validation(fake):
    with pytest.raises(TestRailError, match="Section ID"):
        ops.create_cases(tr(), [CASE])
    with pytest.raises(TestRailError, match="no title"):
        ops.create_cases(tr(), [{"steps": [{"content": "a"}]}], section_id=3)
    with pytest.raises(TestRailError, match="no steps"):
        ops.create_cases(tr(), [{"title": "t", "steps": []}], section_id=3)


def test_dry_run_creates_nothing(fake):
    r = ops.create_cases(tr(), [CASE], section_id=3, fields={"custom_automation_type": 3}, dry_run=True)
    assert r["dry_run"] and r["would_create"][0]["title"] == "Verify login"
    assert not fake.cases and not any(c.startswith("POST") for c in fake.calls)


def test_rate_limit_retry(fake):
    fake.rate_limit_once = True
    assert ops.list_suites(tr()) == [{"id": 11, "name": "Master"}]


def test_case_pack_with_images(fake, tmp_path):
    pack = {"format": "testrail-case-pack", "version": 1, "refs": "ABC-2", "notes": ["check X"],
            "section_id": 3, "image_placement": "expected",
            "fields": {"custom_automation_type": 1},
            "cases": [{"title": "Verify editor", "preconditions": "",
                       "steps": [{"content": "Open editor", "expected": "Editor opens", "images": ["img1"]},
                                 {"content": "Type", "expected": "Text shows", "images": []}]}],
            "images": {"img1": {"name": "editor.png", "mime": "image/png", "data": PNG}}}
    f = tmp_path / "x-cases.json"
    f.write_text(json.dumps(pack), encoding="utf-8")
    r = ops.upload_case_pack(tr(), str(f))
    assert r["ok"] and r["results"][0]["images"] == 1 and r["notes_from_file"] == ["check X"]
    case = fake.cases[r["results"][0]["case_id"]]
    att = fake.attachments[0]["id"]
    assert case["custom_steps_separated"][0]["expected"] == f"Editor opens\n\n![](index.php?/attachments/get/{att})"
    assert case["custom_steps_separated"][1]["expected"] == "Text shows"
    assert case["custom_automation_type"] == 1


def test_real_sample_file_format(fake, tmp_path):
    """Same shape as the files Claude already produces (no images, no section, no fields)."""
    f = tmp_path / "s.json"
    f.write_text('{"format": "testrail-case-pack", "version": 1, "refs": "", "notes": [], "cases": '
                 '[{"title": "Verify f", "preconditions": "p", "steps": [{"content": "a", "expected": "b", '
                 '"images": []}]}], "images": {}}', encoding="utf-8-sig")
    r = ops.upload_case_pack(tr(), str(f), section_id=2, fields={"custom_automation_type": "None"})
    assert r["ok"]


def test_case_pack_errors(fake, tmp_path):
    with pytest.raises(TestRailError, match="not found"):
        ops.load_case_pack(str(tmp_path / "nope.json"))
    bad = tmp_path / "bad.json"
    bad.write_text('{"cases": [{"title": "t", "steps": [{"content": "a", "images": ["img9"]}]}]}')
    with pytest.raises(TestRailError, match="img9"):
        ops.upload_case_pack(tr(), str(bad), section_id=3)


def test_list_cases(fake):
    ops.create_cases(tr(), [CASE], section_id=3, fields={"custom_automation_type": 3})
    assert [c["title"] for c in ops.list_cases(tr(), 3)] == ["Verify login"]


def test_mcp_tools_registered_and_callable(fake):
    from testrail_mcp import server

    async def go():
        tools = {t.name for t in await server.mcp.list_tools()}
        assert tools == {"testrail_check_connection", "testrail_list_projects", "testrail_list_suites",
                         "testrail_list_sections", "testrail_list_cases", "testrail_get_fields",
                         "testrail_add_cases", "testrail_upload_case_file"}
        res = await server.mcp.call_tool("testrail_list_sections", {"search": "login"})
        return res

    res = asyncio.run(go())
    assert "Root > Login" in json.dumps(res, default=str)
