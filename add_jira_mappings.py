#!/usr/bin/env python3
"""
Bulk Jira Mapping Creator for ArmorCode

Creates Jira mappings in bulk via the ArmorCode API.

Usage:
    # Dry-run to validate your CSV first:
    python3 add_jira_mappings.py --dry-run --login-config-id 123

    # Create mappings from CSV:
    python3 add_jira_mappings.py --login-config-id 123 --mappings-csv mappings.csv

Token is read from ARMORCODE_TOKEN in .env (or pass --token).
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_URL = "https://app.armorcode.com"
DEFAULT_DELAY = 0.3   # seconds between requests (avoid rate limiting)
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0

# ---------------------------------------------------------------------------
# Custom field registry
# Keyed by the CSV column prefix (cf_<prefix>_value / cf_<prefix>_active).
# Sourced from GET /user/tickets/jira/custom-fields for the ENG/Bug issue type.
# ---------------------------------------------------------------------------
CUSTOM_FIELD_REGISTRY = {
    "parent":        {"key": "parent",            "name": "Parent",               "type": "issuelink",       "dataType": "issuelink"},
    "sprint":        {"key": "customfield_10020",  "name": "Sprint",               "type": "gh-sprint",       "dataType": "array"},
    "flagged":       {"key": "customfield_10021",  "name": "Flagged",              "type": "multicheckboxes", "dataType": "array"},
    "development":   {"key": "customfield_10000",  "name": "Development",          "type": "devsummarycf",    "dataType": "any"},
    "vulnerability": {"key": "customfield_10033",  "name": "Vulnerability",        "type": "vulnerabilitycf", "dataType": "any"},
    "design":        {"key": "customfield_10036",  "name": "Design",               "type": "designcf",        "dataType": "array"},
    "story_points":  {"key": "customfield_10016",  "name": "Story point estimate", "type": "jsw-story-points","dataType": "number"},
    "rank":          {"key": "customfield_10019",  "name": "Rank",                 "type": "gh-lexo-rank",    "dataType": "any"},
    "linked_issues": {"key": "issuelinks",         "name": "Linked Issues",        "type": "array",           "dataType": "array"},
    "due_date":      {"key": "duedate",            "name": "Due Date",             "type": "duedate",         "dataType": "duedate"},
}


def _load_env(path: str = ".env"):
    """Load key=value pairs from a .env file into os.environ (does not override existing env vars)."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_env()


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method: str, path: str, token: str, body: dict = None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            if e.code == 429 or e.code >= 500:
                wait = RETRY_BACKOFF ** attempt
                print(f"    [WARN] HTTP {e.code} on attempt {attempt}/{MAX_RETRIES}, retrying in {wait:.1f}s …")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code} {method} {path}: {raw[:400]}")
        except urllib.error.URLError as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF ** attempt)
                continue
            raise RuntimeError(f"Network error {method} {path}: {e}")
    raise RuntimeError(f"Exhausted retries for {method} {path}")


def get(path: str, token: str):
    return _request("GET", path, token)


def post(path: str, token: str, body: dict):
    return _request("POST", path, token, body)


# ---------------------------------------------------------------------------
# Login config lookup
# ---------------------------------------------------------------------------

def find_login_config_id(token: str, name: str) -> int:
    """Look up a Jira login config ID by its name."""
    configs = get("/user/tickets/jira/configuration/login/JIRA", token)
    if not isinstance(configs, list):
        configs = configs.get("data", [])
    matches = [c for c in configs if c.get("name") == name]
    if not matches:
        available = [c.get("name") for c in configs]
        raise RuntimeError(
            f"No Jira login config found with name '{name}'.\n"
            f"Available: {available}"
        )
    if len(matches) > 1:
        ids = [c.get("id") for c in matches]
        raise RuntimeError(
            f"Multiple login configs named '{name}': {ids}. Use --login-config-id instead."
        )
    return int(matches[0]["id"])


# ---------------------------------------------------------------------------
# Custom field builder
# ---------------------------------------------------------------------------

def build_custom_fields(mapping: dict) -> list:
    """
    Build the customFields array from two sources:

    1. Registry columns — CSV columns of the form:
         cf_<prefix>_value   e.g. cf_due_date_value = ${finding.resolutionDueDate}
                             active=true if value is present, active=false if blank
       Valid prefixes: parent, sprint, flagged, development, vulnerability,
                       design, story_points, rank, linked_issues, due_date

    2. Raw JSON column — a 'customFields' column containing a JSON array for
       any fields not in the registry, e.g.:
         [{"key": "customfield_99999", "name": "MyField", "value": "x", "active": true}]
    """
    fields = []

    # Source 1: registry columns
    for prefix, meta in CUSTOM_FIELD_REGISTRY.items():
        value = mapping.get(f"cf_{prefix}_value", "").strip()
        active = bool(value)
        fields.append({
            "key":      meta["key"],
            "name":     meta["name"],
            "type":     meta["type"],
            "dataType": meta["dataType"],
            "defaultVal": value or None,
            "value":    None,
            "active":   active,
        })

    # Source 2: raw JSON column (for anything not in the registry)
    raw_json = mapping.get("customFields", "").strip()
    if raw_json:
        try:
            extra = json.loads(raw_json)
            if isinstance(extra, list):
                fields.extend(extra)
        except json.JSONDecodeError as e:
            print(f"  [WARN] Could not parse customFields JSON: {e}")

    return fields


# ---------------------------------------------------------------------------
# Mapping creation
# ---------------------------------------------------------------------------

def create_mapping(token: str, mapping: dict) -> dict:
    """
    Create a single Jira mapping (project <-> ArmorCode product/sub-product).
    mapping keys (all optional except projectKey):
        projectKey, projectId, loginConfigId,
        issueType, issueTypeId,
        product (list[int] or comma-separated str), subProduct (list[int] or comma-separated str),
        group (list[int] or comma-separated str), subGroup (list[int] or comma-separated str),
        enabled, configurationType
    """
    enabled = True

    body = {
        "ticketSystemType": "JIRA",
        "enabled": enabled,
        "projectKey": mapping["projectKey"],
    }

    optional_str = ["projectId", "issueType", "issueTypeId", "configurationType",
                    "resolvedStatus", "reopenStatus", "configurationKey"]
    for k in optional_str:
        if mapping.get(k):
            body[k] = mapping[k]

    if mapping.get("loginConfigId"):
        body["loginConfigId"] = int(mapping["loginConfigId"])

    for list_key in ["product", "subProduct", "group", "subGroup", "labels", "ticketClosureStatus"]:
        val = mapping.get(list_key)
        if val:
            if isinstance(val, str):
                parsed = [int(x.strip()) for x in val.split(",") if x.strip()]
                body[list_key] = parsed
            elif isinstance(val, list):
                body[list_key] = [int(x) for x in val]

    custom_fields = build_custom_fields(mapping)
    if custom_fields:
        body["customFields"] = custom_fields

    return post("/user/tickets/jira/configuration", token, body)


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_mappings_csv(path: str) -> list:
    """
    Load mappings from CSV. Required column: projectKey.
    Optional columns: projectId, loginConfigId, issueType, issueTypeId,
                      product, subProduct, enabled, configurationType,
                      labels, resolvedStatus, reopenStatus
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=2):
            row = {k.strip(): (v.strip() if v is not None else "") for k, v in row.items() if k}
            if not row.get("projectKey"):
                print(f"  [WARN] Row {i} missing projectKey, skipping.")
                continue
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Progress tracker
# ---------------------------------------------------------------------------

class Progress:
    def __init__(self, path: str):
        self.path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text())
        return {"created": [], "failed": []}

    def save(self):
        self.path.write_text(json.dumps(self._data, indent=2))

    def mark_created(self, key: str, result: dict):
        self._data["created"].append({"key": key, "result": result})
        self.save()

    def mark_failed(self, key: str, error: str):
        self._data["failed"].append({"key": key, "error": error})
        self.save()

    def already_done(self, key: str) -> bool:
        return any(r["key"] == key for r in self._data["created"])

    @property
    def created_count(self) -> int:
        return len(self._data["created"])

    @property
    def failed_count(self) -> int:
        return len(self._data["failed"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Bulk-add Jira mappings to ArmorCode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--token", default=os.environ.get("ARMORCODE_TOKEN", ""),
                   help="ArmorCode API token (or set ARMORCODE_TOKEN in .env)")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate inputs and print what would be created without calling the API")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY,
                   help=f"Seconds between requests (default {DEFAULT_DELAY})")

    login_grp = p.add_mutually_exclusive_group(required=True)
    login_grp.add_argument("--login-config-id", type=int,
                           help="ArmorCode Jira login config ID to attach mappings to")
    login_grp.add_argument("--integration-name",
                           help="ArmorCode Jira integration name (looked up via API)")

    p.add_argument("--mappings-csv", default="mappings.csv",
                   help="CSV file with mapping rows (default: mappings.csv)")
    p.add_argument("--count", type=int,
                   help="Limit to first N rows (useful for testing)")

    default_grp = p.add_argument_group("Mapping defaults (applied when CSV column is empty)")
    default_grp.add_argument("--default-issue-type", default="",
                              help="Default Jira issue type name (e.g. Bug)")
    default_grp.add_argument("--default-issue-type-id", default="",
                              help="Default Jira issue type ID")
    default_grp.add_argument("--default-product", default="",
                              help="Default ArmorCode product IDs (comma-separated)")
    default_grp.add_argument("--default-sub-product", default="",
                              help="Default ArmorCode sub-product IDs (comma-separated)")

    p.add_argument("--progress-file", default="progress.json",
                   help="File to track progress and support resume (default: progress.json)")

    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    token = args.token
    if not token:
        print("ERROR: No API token found. Set ARMORCODE_TOKEN in .env or pass --token.")
        sys.exit(1)

    if args.integration_name:
        print(f"Looking up login config for integration '{args.integration_name}' …")
        try:
            login_config_id = find_login_config_id(token, args.integration_name)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        print(f"  Resolved to login config ID: {login_config_id}\n")
    else:
        login_config_id = args.login_config_id

    print("ArmorCode Jira Bulk Mapper")
    print(f"  Base URL        : {BASE_URL}")
    print(f"  Login config ID : {login_config_id}")
    print(f"  Dry-run         : {args.dry_run}")
    print()

    # ---- Load mappings from CSV ----
    csv_path = args.mappings_csv
    if not Path(csv_path).exists():
        print(f"ERROR: Mappings CSV not found: {csv_path}")
        print(f"\nCreate '{csv_path}' with columns:")
        print("  projectKey,projectId,issueType,issueTypeId,product,subProduct,enabled")
        sys.exit(1)

    mappings = load_mappings_csv(csv_path)
    if args.count:
        mappings = mappings[: args.count]

    if not mappings:
        print("No valid mapping rows found in CSV.")
        sys.exit(1)

    print(f"Loaded {len(mappings)} mapping(s) from '{csv_path}'")

    # Apply defaults
    defaults = {
        "loginConfigId": login_config_id,
        "issueType": args.default_issue_type,
        "issueTypeId": args.default_issue_type_id,
        "product": args.default_product,
        "subProduct": args.default_sub_product,
    }
    for m in mappings:
        for k, v in defaults.items():
            if v and not m.get(k):
                m[k] = v
        if not m.get("loginConfigId"):
            m["loginConfigId"] = login_config_id

    # ---- Create mappings ----
    progress = Progress(args.progress_file)
    total = len(mappings)
    created = 0
    skipped = 0
    failed = 0

    print(f"\nCreating {total} mapping(s) …\n")

    for i, m in enumerate(mappings, start=1):
        key_parts = [m["projectKey"]]
        if m.get("issueTypeId"):
            key_parts.append(m["issueTypeId"])
        elif m.get("issueType"):
            key_parts.append(m["issueType"])
        key = "_".join(str(p) for p in key_parts)
        label = f"[{i}/{total}] projectKey={m['projectKey']} issueType={m.get('issueType', '')}"

        if progress.already_done(key):
            print(f"  {label} — already created, skipping.")
            skipped += 1
            continue

        if args.dry_run:
            # Show the actual body that would be sent, not the raw CSV row
            dry_body = {
                "ticketSystemType": "JIRA",
                "enabled": str(m.get("enabled", "true")).strip().lower() != "false",
                "projectKey": m["projectKey"],
                "loginConfigId": int(m["loginConfigId"]) if m.get("loginConfigId") else None,
            }
            for k in ["projectId", "issueType", "issueTypeId", "configurationType"]:
                if m.get(k):
                    dry_body[k] = m[k]
            cf = build_custom_fields(m)
            if cf:
                dry_body["customFields"] = cf
            print(f"  {label} — [DRY-RUN] would POST: {json.dumps(dry_body, indent=4)}")
            created += 1
            continue

        try:
            result = create_mapping(token, m)
            mapping_id = result.get("id", "?")
            print(f"  {label} — OK (id={mapping_id})")
            progress.mark_created(key, result)
            created += 1
        except RuntimeError as e:
            print(f"  {label} — FAILED: {e}")
            progress.mark_failed(key, str(e))
            failed += 1

        if args.delay > 0:
            time.sleep(args.delay)

    # ---- Summary ----
    print(f"\n{'='*50}")
    print("Done.")
    print(f"  Created : {created}")
    print(f"  Skipped : {skipped} (already existed in progress file)")
    print(f"  Failed  : {failed}")
    if not args.dry_run:
        print(f"  Progress: {args.progress_file}")
    if failed:
        print("\nRe-run the script to retry failed items (they are not in the progress file).")


if __name__ == "__main__":
    main()

