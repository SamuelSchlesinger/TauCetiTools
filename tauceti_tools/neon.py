"""Operator-only provision and inspection of separately approved downstream databases."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .project import ConfigError

API = "https://console.neon.tech/api/v2"


def request(token, path, *, method="GET", data=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise ConfigError(
            f"Neon {method} failed with HTTP {error.code}; response withheld to protect credentials"
        ) from None
    except OSError:
        raise ConfigError(
            "Neon response unavailable. Inspect the existing project before retrying a mutation; no automatic retry was performed."
        ) from None


def private(path: Path, content: str, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    descriptor = os.open(path, flags, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(content)


def run(project, args):
    token = args.token_file.read_text().strip()
    if not token:
        raise ConfigError("Neon token file is empty")
    grants = json.loads(args.registry.read_text())
    grant = grants.get(project.content)
    if not grant or grant.get("approved") is not True:
        raise ConfigError(
            f"No operator approval for {project.content}. Add its entry to the private operator registry before provisioning."
        )
    project_id = grant.get("project_id")
    if args.action == "inspect":
        if not project_id:
            raise ConfigError("No project_id recorded; inspect the Neon console and update the operator registry")
        result = request(token, f"/projects/{urllib.parse.quote(project_id, safe='')}")["project"]
        print(
            json.dumps(
                {
                    key: result.get(key)
                    for key in ("id", "name", "region_id", "pg_version", "org_id", "history_retention_seconds")
                },
                indent=2,
            )
        )
        print("Console: https://console.neon.tech/app/projects/" + project_id)
        return
    if args.action == "handover":
        if not project_id:
            raise ConfigError("Record project_id before preparing handover")
        print(f"Project: {project_id}\nOpen https://console.neon.tech/app/projects/{project_id}")
        print(
            "Record the destination Neon organization ID with the downstream owner. In the project Settings, use Transfer to organization, selecting that organization. Alternatively export using pg_dump with credentials supplied privately, restore into their PostgreSQL service, switch DATABASE_URL, then verify Bors before retiring the old connection."
        )
        print("No resources, ownership or credentials changed.")
        return
    if args.action == "rotate":
        if not project_id or not grant.get("branch_id") or not grant.get("role"):
            raise ConfigError("Rotation requires the actual project_id, branch_id and role in the private registry")
        print(
            "Rotation replaces the role password and drops existing connections after the Neon operation completes. Coordinate the runtime secret replacement with its operator."
        )
        if not args.apply:
            print("Preview only. Add --apply and --output PATH to save the new password privately.")
            return
        if not args.output or args.output.exists():
            raise ConfigError("Choose a new --output file for the replacement password")
        path = f"/projects/{urllib.parse.quote(project_id, safe='')}/branches/{urllib.parse.quote(grant['branch_id'], safe='')}/roles/{urllib.parse.quote(grant['role'], safe='')}/reset_password"
        result = request(token, path, method="POST", data={})
        private(args.output, result["role"]["password"] + "\n")
        print(
            f"Replacement saved to {args.output}. Wait for Neon operations to complete, replace the private Bors connection credential, and verify its health. No automatic retry occurs after an uncertain response."
        )
        return
    if project_id:
        print(f"Existing approved Neon project {project_id}; use inspect. No second project created.")
        return
    name = grant.get("name", project.key.replace("__", "-") + "-bors")
    org = grant.get("org_id")
    if not org:
        raise ConfigError("Operator registry entry requires org_id")
    listing = request(token, "/projects?" + urllib.parse.urlencode({"org_id": org, "search": name, "limit": 100}))
    existing = [p for p in listing["projects"] if p["name"] == name and p.get("org_id") == org]
    if existing:
        grant["project_id"] = existing[0]["id"]
        if args.apply:
            private(args.registry, json.dumps(grants, indent=2) + "\n", replace=True)
        print(
            f"Existing Neon project {existing[0]['id']}; record it and retrieve its direct connection URI from the console. No duplicate created."
        )
        return
    settings = {
        "name": name,
        "org_id": org,
        "region_id": grant.get("region", "aws-us-east-1"),
        "pg_version": grant.get("pg_version", 18),
        "history_retention_seconds": grant.get("history_retention_seconds", 604800),
        "default_endpoint_settings": {
            "autoscaling_limit_min_cu": grant.get("min_cu", 0.25),
            "autoscaling_limit_max_cu": grant.get("max_cu", 0.25),
            "suspend_timeout_seconds": grant.get("suspend_timeout_seconds", -1),
        },
    }
    print(json.dumps(settings, indent=2))
    if not args.apply:
        print(
            "Preview only. Confirm the paid account/resource settings, then add --apply --output PRIVATE_DATABASE_URL_FILE."
        )
        return
    if not args.output or args.output.exists():
        raise ConfigError("Choose a new private --output file for the database URI")
    result = request(token, "/projects", method="POST", data={"project": settings})
    grant["project_id"] = result["project"]["id"]
    if result.get("branch"):
        grant["branch_id"] = result["branch"]["id"]
    private(args.registry, json.dumps(grants, indent=2) + "\n", replace=True)
    connection = next(
        (row for row in result.get("connection_uris", []) if "-pooler." not in row["connection_uri"]), None
    )
    if not connection:
        raise ConfigError(
            f"Project {grant['project_id']} created and recorded; retrieve its direct URI from the console. Do not create another project."
        )
    private(args.output, connection["connection_uri"] + "\n")
    print(
        f"Created {grant['project_id']}; direct URI saved privately to {args.output}. Wait for Neon operations to finish before connecting. Supply this file to the downstream privately; keep the Neon management token with the operator."
    )
