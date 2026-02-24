# ArmorCode – Bulk Jira Mapping Creator

Adds a large number of Jira mappings to ArmorCode via the REST API.

---

## Authentication

The script requires an ArmorCode API token. Provide it in one of two ways:

**Option 1 – `.env` file (recommended):**

```
ARMORCODE_TOKEN=your-token-here
```

**Option 2 – `--token` flag:**

```bash
python3 add_jira_mappings.py --token YOUR_TOKEN --integration-name "My Jira Integration"
```

**Option 3 – inline environment variable:**

```bash
ARMORCODE_TOKEN=your-token python3 add_jira_mappings.py --integration-name "My Jira Integration"
```

---

## Quick start

### 1. Edit `mappings.csv`

Each row is one Jira mapping. The only **required** columns are `projectKey` and `issueType`.

| Column | Required | Notes |
|---|---|---|
| `projectKey` | **Yes** | Jira project key (e.g. `PROJ`) |
| `projectId` | No | Jira numeric project ID |
| `issueType` | **Yes** | e.g. `Bug`, `Story`, `Task` |
| `issueTypeId` | No | Jira numeric issue type ID |
| `group` | No | ArmorCode group IDs, comma-separated (defaults to 'All') |
| `subGroup` | No | ArmorCode sub-group IDs, comma-separated (defaults to 'All') |
| `cf_parent_value` | No | Parent — non-blank value sets `active=true`, blank sets `active=false` |
| `cf_sprint_value` | No | Sprint |
| `cf_flagged_value` | No | Flagged |
| `cf_development_value` | No | Development |
| `cf_vulnerability_value` | No | Vulnerability |
| `cf_design_value` | No | Design |
| `cf_story_points_value` | No | Story point estimate |
| `cf_rank_value` | No | Rank |
| `cf_linked_issues_value` | No | Linked Issues |
| `cf_due_date_value` | No | Due Date (e.g. `${finding.resolutionDueDate}`) |
| `customFields` | No | JSON array of raw custom field objects for fields not listed above |

### 2. Run

Pass the integration by name (recommended):

```bash
python3 add_jira_mappings.py \
  --integration-name "My Jira Integration" \
  --mappings-csv mappings.csv
```

Or by numeric ID if you already know it:

```bash
python3 add_jira_mappings.py \
  --login-config-id 123 \
  --mappings-csv mappings.csv
```

`--integration-name` and `--login-config-id` are mutually exclusive; one is required.

---

## Options

```
--token TOKEN               ArmorCode API token (default: ARMORCODE_TOKEN env var / .env file)
--dry-run                   Print what would be created without calling the API
--delay SECONDS             Pause between requests to avoid rate-limiting (default 0.3)
--count N                   Only process first N rows (good for smoke tests)
--progress-file FILE        JSON file to track progress; re-run resumes where it left off (default: progress.json)

Required (one of):
  --integration-name NAME   ArmorCode Jira integration name (looked up via API)
  --login-config-id ID      ArmorCode Jira login config ID (use if you already know it)

CSV:
  --mappings-csv FILE       Path to mappings CSV (default: mappings.csv)

Mapping defaults (applied when CSV column is blank):
  --default-issue-type      Issue type name (e.g. Bug)
  --default-issue-type-id   Issue type ID
  --default-product         ArmorCode product IDs (comma-separated)
  --default-sub-product     ArmorCode sub-product IDs (comma-separated)
```

---

## Dry-run

Best practice: do a dry-run or a small sample set to validate your CSV before creating a large number of mappings:

```bash
python3 add_jira_mappings.py --dry-run --integration-name "My Jira Integration" --mappings-csv mappings.csv
```

---

## Resume / retry

The script writes a `progress.json` file after each success.
Re-running skips already-created mappings and retries only the failures.

```bash
# First run (partial failure)
python3 add_jira_mappings.py --integration-name "My Jira Integration" --mappings-csv mappings.csv

# Re-run — skips successes, retries failures
python3 add_jira_mappings.py --integration-name "My Jira Integration" --mappings-csv mappings.csv
```

Use `--progress-file` to keep separate progress files for different runs:

```bash
python3 add_jira_mappings.py \
  --integration-name "My Jira Integration" \
  --mappings-csv mappings.csv \
  --progress-file run1_progress.json
```
