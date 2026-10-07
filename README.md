# testrail-mcp

An [MCP](https://modelcontextprotocol.io) server that lets Claude upload test cases to **TestRail**.

You describe the feature to Claude in a chat; Claude writes the test cases there. This server's only job
is to talk to TestRail: find the right section, check required fields, and create the cases (with
step-by-step actions and expected results). No user-story form, no screenshot area, no AI key —
the chat is the interface.

- Python, one dependency (`mcp`); HTTP calls use the standard library.
- Your TestRail URL, email and API key are read **only from environment variables** set in your
  MCP client's config. Nothing personal is stored in this repository.

## Tools

| Tool | What it does |
|---|---|
| `testrail_check_connection` | Confirms the credentials work and shows the configured defaults |
| `testrail_list_projects` | Active projects |
| `testrail_list_suites` | Suites in a project |
| `testrail_list_sections` | Sections as a tree (`Parent > Child`), with optional text search |
| `testrail_list_cases` | Existing cases in a section, to avoid duplicates |
| `testrail_get_fields` | Custom case fields for your template, with dropdown options and which are required |
| `testrail_add_cases` | Creates cases: title, preconditions, refs, steps (`content` + `expected`), custom fields |
| `testrail_upload_case_file` | Uploads a saved `testrail-case-pack` JSON file, attaching any embedded images to their steps |

`testrail_add_cases` and `testrail_upload_case_file` accept `dry_run: true` to preview exactly what
would be sent without creating anything. Dropdown fields accept the option label
(e.g. `"Not Automated"`) or its numeric id.

## Install

Requires Python 3.10+ and [uv](https://docs.astral.sh/uv/) (or plain `pip`).

```bash
git clone https://github.com/<your-github-user>/testrail-mcp.git
cd testrail-mcp
uv sync            # or: pip install -e .
```

## Connect it to Claude Desktop

Open **Settings → Developer → Edit Config** in Claude Desktop and add a server entry
(full example: [`examples/claude_desktop_config.json`](examples/claude_desktop_config.json)):

```json
{
  "mcpServers": {
    "testrail": {
      "command": "uv",
      "args": ["--directory", "C:\\path\\to\\testrail-mcp", "run", "testrail-mcp"],
      "env": {
        "TESTRAIL_URL": "https://yourcompany.testrail.io",
        "TESTRAIL_USER": "you@company.com",
        "TESTRAIL_API_KEY": "your-api-key",
        "TESTRAIL_PROJECT_ID": "1"
      }
    }
  }
}
```

Restart Claude Desktop. The tools then appear in your chats.

The config file lives only on your computer, so your key never goes into git. Use a TestRail
**API key** (TestRail → My Settings → API Keys), not your password, so you can revoke it any time.

### Settings

| Variable | Required | Meaning |
|---|---|---|
| `TESTRAIL_URL` | yes | e.g. `https://yourcompany.testrail.io` |
| `TESTRAIL_USER` | yes | Email you sign in to TestRail with |
| `TESTRAIL_API_KEY` | yes | TestRail → My Settings → API Keys |
| `TESTRAIL_PROJECT_ID` | recommended | Default project |
| `TESTRAIL_SUITE_ID` | no | Default suite (multi-suite projects) |
| `TESTRAIL_SECTION_ID` | no | Default section for new cases |
| `TESTRAIL_TEMPLATE_ID` | no | Case template, default `2` ("Test Case (Steps)") |
| `TESTRAIL_DEFAULT_FIELDS` | no | JSON of custom field values applied to every new case, e.g. `{"custom_automation_type": "Not Automated"}` |
| `TESTRAIL_STEPS_FIELD` | no | Default `custom_steps_separated` |
| `TESTRAIL_PRECONDS_FIELD` | no | Default `custom_preconds` |

If TestRail says a field "is a required field", ask Claude to run `testrail_get_fields` and then either
pass the value per upload or put it in `TESTRAIL_DEFAULT_FIELDS`.

## Using it

Example prompts:

> Write test cases for the CSV export story below, then add them to the "Reports > Export" section with refs PROJ-123.

> Upload `C:\Users\me\Downloads\export-cases.json` to section 42.

Claude will look up the section, check required fields, show you the cases, and create them.
Each result links to the new case in TestRail.

### Case-pack file format

`testrail_upload_case_file` reads this JSON shape (images are optional and embedded as base64):

```json
{
  "format": "testrail-case-pack",
  "version": 1,
  "refs": "PROJ-123",
  "section_id": null,
  "fields": {"custom_automation_type": "Not Automated"},
  "image_placement": "expected",
  "notes": ["anything to double-check before uploading"],
  "cases": [
    {
      "title": "Verify CSV export downloads a file",
      "preconditions": "User is signed in",
      "steps": [
        {"content": "Open Reports and click Export CSV", "expected": "A .csv file downloads", "images": ["img1"]}
      ]
    }
  ],
  "images": {"img1": {"name": "export.png", "mime": "image/png", "data": "<base64>"}}
}
```

`section_id` and `fields` in the call override the file; the file overrides the environment defaults.

## Development

```bash
uv sync --extra dev
uv run pytest
```

Tests run against an in-memory fake TestRail (`tests/fake_testrail.py`), so they never touch a real
instance.

## License

MIT
