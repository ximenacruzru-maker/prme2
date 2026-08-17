#!/usr/bin/env python3
"""
Ironwood Insurance — Nudge Check

A lightweight, read-only check against AgencyZoom's own public API
(https://app.agencyzoom.com/openapi/). It does NOT touch the dashboard or
any commission numbers. All it does:

    1. Pulls every WON lead and every lead currently in a "Quoted" stage.
    2. Compares that against a snapshot from the last run (state.json,
       committed to the repo).
    3. Reports anything NEW since last time — a lead that wasn't WON
       before and now is, or a lead that's newly sitting in a Quoted stage.
    4. Writes the new snapshot back to state.json for next time.

This is advisory only — a nudge to bring new activity to Claude for proper
verification against the sales ledger, not an automatic update to real
numbers. See refresh_pipeline.py's docstring for why sales/commissions
aren't safe to fully automate with this account's AgencyZoom visibility.

Required environment variables:
    AZ_USERNAME   - AgencyZoom login email
    AZ_PASSWORD   - AgencyZoom login password

Usage:
    python3 nudge_check.py state.json nudge_output.md
"""

import os
import sys
import json
import time
import requests

API_BASE = "https://api.agencyzoom.com"

QUOTED_STAGES = [
    {"workflowId": 16851, "workflowStageId": "54166", "name": "1 Pipeline"},
    {"workflowId": 68506, "workflowStageId": "292558", "name": "2 Quotes Not Closed"},
    {"workflowId": 68507, "workflowStageId": "292567", "name": "3 Leads Not Quoted/Aged"},
    {"workflowId": 4539, "workflowStageId": "13617", "name": "Pipeline"},
]


def login(username, password):
    r = requests.post(f"{API_BASE}/v1/api/auth/login",
                       json={"username": username, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["jwt"]


def api_post(jwt, path, payload):
    r = requests.post(f"{API_BASE}{path}", headers={"Authorization": f"Bearer {jwt}"},
                       json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_all(jwt, path, base_payload, max_pages=15):
    results = []
    page = 0
    while page < max_pages:
        body = api_post(jwt, path, {**base_payload, "page": page, "pageSize": 100})
        leads = body.get("leads", [])
        results.extend(leads)
        if len(leads) < 100:
            break
        page += 1
        time.sleep(0.25)
    return results


def snapshot(jwt):
    won = fetch_all(jwt, "/v1/api/leads/list", {"status": 2})
    won_ids = {str(l["id"]): {"name": f"{l.get('firstname','')} {l.get('lastname','')}".strip(),
                               "producer": f"{l.get('assignToFirstname','')} {l.get('assignToLastname','')}".strip(),
                               "soldDate": l.get("soldDate")} for l in won}

    quoted_ids = {}
    for stage in QUOTED_STAGES:
        leads = fetch_all(jwt, "/v1/api/leads/list",
                           {"workflowId": stage["workflowId"], "workflowStageId": stage["workflowStageId"]})
        for l in leads:
            quoted_ids[str(l["id"])] = {"name": f"{l.get('firstname','')} {l.get('lastname','')}".strip(),
                                         "producer": f"{l.get('assignToFirstname','')} {l.get('assignToLastname','')}".strip(),
                                         "stage": stage["name"]}

    return {"won": won_ids, "quoted": quoted_ids}


def diff(old, new):
    new_won = {lid: v for lid, v in new["won"].items() if lid not in old.get("won", {})}
    new_quoted = {lid: v for lid, v in new["quoted"].items() if lid not in old.get("quoted", {})}
    return new_won, new_quoted


def main():
    if len(sys.argv) != 3:
        print("Usage: python3 nudge_check.py state.json nudge_output.md", file=sys.stderr)
        sys.exit(1)
    state_path, output_path = sys.argv[1], sys.argv[2]

    username = os.environ.get("AZ_USERNAME")
    password = os.environ.get("AZ_PASSWORD")
    if not username or not password:
        print("ERROR: AZ_USERNAME and AZ_PASSWORD must be set.", file=sys.stderr)
        sys.exit(1)

    jwt = login(username, password)
    new_snapshot = snapshot(jwt)

    old_snapshot = {"won": {}, "quoted": {}}
    if os.path.exists(state_path):
        with open(state_path) as f:
            old_snapshot = json.load(f)

    new_won, new_quoted = diff(old_snapshot, new_snapshot)

    lines = []
    if new_won or new_quoted:
        lines.append(f"## AgencyZoom activity since last check ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())})\n")
        if new_won:
            lines.append(f"### 🟢 {len(new_won)} newly WON lead(s)")
            for lid, v in new_won.items():
                lines.append(f"- **{v['name']}** — {v['producer']} — sold {v['soldDate']} ([open](https://app.agencyzoom.com/lead/index?id={lid}))")
            lines.append("")
        if new_quoted:
            lines.append(f"### 🟡 {len(new_quoted)} newly quoted lead(s)")
            for lid, v in new_quoted.items():
                lines.append(f"- **{v['name']}** — {v['producer']} — {v['stage']} ([open](https://app.agencyzoom.com/lead/index?id={lid}))")
            lines.append("")
        lines.append("_Bring these to Claude to verify and reconcile against the sales ledger — this check doesn't update the dashboard or any commission numbers on its own._")
    else:
        lines.append(f"No new AgencyZoom activity since last check ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}).")

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    with open(state_path, "w") as f:
        json.dump(new_snapshot, f)

    print(f"New WON: {len(new_won)}, New Quoted: {len(new_quoted)}")
    # Write a simple flag file the workflow step can check (avoids the deprecated ::set-output syntax)
    with open("has_updates.flag", "w") as f:
        f.write("true" if (new_won or new_quoted) else "false")


if __name__ == "__main__":
    main()
