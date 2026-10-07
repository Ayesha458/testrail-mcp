"""TestRail MCP server: lets Claude look up your TestRail structure and upload test cases.

Claude writes the test cases in the chat; this server only talks to TestRail.
Credentials are read from environment variables set in your MCP client config,
so nothing personal lives in this code or the repository.
"""

from __future__ import annotations

from typing import Any

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

from . import ops
from .client import Settings, TestRail, TestRailError

INSTRUCTIONS = """\
Uploads test cases to TestRail. You (Claude) write the cases in the conversation; these tools only
read TestRail's structure and create cases.

Typical flow:
1. testrail_check_connection - confirms the credentials and shows the configured defaults.
2. testrail_list_sections(search=...) - find the section ID to put the cases in.
3. testrail_get_fields(required_only=True) - see required custom fields (e.g. a dropdown) and their options.
4. Show the user the drafted cases and confirm, then testrail_add_cases(...) - or
   testrail_upload_case_file(path=...) for a saved testrail-case-pack .json file.
Use dry_run=True first when unsure; it shows exactly what would be sent without creating anything.
Each step has one action ("content") and one expected result ("expected")."""

mcp = _Server("testrail", instructions=INSTRUCTIONS)


def _client() -> TestRail:
    try:
        return TestRail(Settings.from_env())
    except TestRailError as e:
        raise ToolError(str(e)) from None


def _call(fn, *args, **kwargs):
    try:
        return fn(_client(), *args, **kwargs)
    except TestRailError as e:
        raise ToolError(str(e)) from None


@mcp.tool()
def testrail_check_connection() -> dict:
    """Check the TestRail credentials work and show the configured defaults (project, suite, section, fields)."""
    return _call(ops.connection)


@mcp.tool()
def testrail_list_projects() -> list[dict]:
    """List active TestRail projects (id, name, suite_mode)."""
    return _call(ops.list_projects)


@mcp.tool()
def testrail_list_suites(project_id: int | None = None) -> list[dict]:
    """List the test suites in a project. project_id defaults to TESTRAIL_PROJECT_ID."""
    return _call(ops.list_suites, project_id)


@mcp.tool()
def testrail_list_sections(project_id: int | None = None, suite_id: int | None = None,
                           search: str = "") -> list[dict]:
    """List sections as a tree with full paths ("Parent > Child"). `search` filters by text in the path.
    project_id / suite_id default to TESTRAIL_PROJECT_ID / TESTRAIL_SUITE_ID."""
    return _call(ops.list_sections, project_id, suite_id, search)


@mcp.tool()
def testrail_list_cases(section_id: int, project_id: int | None = None,
                        suite_id: int | None = None) -> list[dict]:
    """List existing cases (id, title, refs) in a section, e.g. to avoid creating duplicates."""
    return _call(ops.list_cases, section_id, project_id, suite_id)


@mcp.tool()
def testrail_get_fields(required_only: bool = False, project_id: int | None = None) -> list[dict]:
    """Custom case fields for the configured template: name, label, type, required, and dropdown options."""
    return _call(ops.case_fields, project_id, required_only)


@mcp.tool()
def testrail_add_cases(cases: list[dict[str, Any]], section_id: int | None = None, refs: str = "",
                       fields: dict[str, Any] | None = None, project_id: int | None = None,
                       dry_run: bool = False) -> dict:
    """Create test cases in a TestRail section.

    cases: [{"title": "Verify ...", "preconditions": "...", "refs": "optional per-case refs",
             "steps": [{"content": "one action", "expected": "one expected result"}]}]
    section_id: defaults to TESTRAIL_SECTION_ID.
    refs: references applied to every case (e.g. a story key), unless a case sets its own.
    fields: custom field values for every case, e.g. {"custom_automation_type": "Not Automated"}.
            Dropdowns accept the option id or its label. Merged over TESTRAIL_DEFAULT_FIELDS.
    dry_run: return the payloads that would be sent, without creating anything.
    """
    return _call(ops.create_cases, cases, section_id=section_id, refs=refs, fields=fields,
                 project_id=project_id, dry_run=dry_run)


@mcp.tool()
def testrail_upload_case_file(path: str, section_id: int | None = None,
                              fields: dict[str, Any] | None = None, project_id: int | None = None,
                              dry_run: bool = False) -> dict:
    """Upload a saved testrail-case-pack .json file from this computer (full path).
    Embedded images in the file are attached to their steps. section_id overrides the file's
    section_id, which overrides TESTRAIL_SECTION_ID."""
    return _call(ops.upload_case_pack, path, section_id=section_id, fields=fields,
                 project_id=project_id, dry_run=dry_run)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
