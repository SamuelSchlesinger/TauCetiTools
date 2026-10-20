"""Deployment preparation and explicit App roles; no implicit cloud provisioning."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from .project import DOCS, ConfigError, Project

PERMISSIONS = {
    "automation": {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "checks": "write",
        "statuses": "write",
        "actions": "write",
        "actions_variables": "read",
        "workflows": "write",
    },
    "claims": {"administration": "write", "contents": "write"},
    "collector": {"contents": "read", "actions": "read", "pull_requests": "read"},
    "github-state": {
        "contents": "read",
        "issues": "read",
        "pull_requests": "read",
        "checks": "read",
        "statuses": "read",
        "actions": "read",
    },
    "bors": {
        "contents": "write",
        "pull_requests": "write",
        "issues": "write",
        "checks": "read",
        "statuses": "write",
        "actions": "read",
        "actions_variables": "read",
    },
}
EVENTS = {
    "automation": [],
    "claims": [],
    "collector": [],
    "github-state": [
        "pull_request",
        "pull_request_review",
        "pull_request_review_comment",
        "issues",
        "issue_comment",
        "check_run",
        "check_suite",
        "status",
        "workflow_run",
        "workflow_job",
        "push",
    ],
    "bors": ["pull_request", "issue_comment", "pull_request_review", "check_run", "check_suite", "status", "push"],
}


def app_manifest(project: Project, role: str) -> dict:
    service = project.service(role.replace("-", "_"))
    url = service.get("url", f"https://github.com/{project.content}")
    webhook = role in ("bors", "github-state")
    if webhook and not service.get("url"):
        raise ConfigError(
            f"Set services.{role.replace('-', '_')}.url to the actual deployment URL before registering its App"
        )
    result = {
        "name": f"{project.content.split('/')[1]}-{role}",
        "url": url,
        "public": True,
        "description": f"{role} for {project.content}; installation does not grant hosted infrastructure access",
        "default_permissions": PERMISSIONS[role],
        "default_events": EVENTS[role],
        "hook_attributes": {
            "url": url + ("/webhook/github" if role == "bors" else "/webhook/github"),
            "active": webhook,
        },
    }
    if role == "bors":
        result["callback_urls"] = [url + "/auth/github/callback"]
    return result


def write_deployment(project: Project, service: str, output: Path):
    config = project.service(service.replace("-", "_"))
    if config.get("mode", "off") == "off":
        raise ConfigError(f"{service} is off; select hosted or custom in the canonical configuration first")
    output.mkdir(parents=True, exist_ok=True)
    if service == "bors" and config.get("deployment", "docker") == "docker":
        image = config.get("image")
        if not image or "@sha256:" not in image:
            raise ConfigError("Set services.bors.image to a released container image pinned by sha256 digest")
        url = config.get("url", "")
        from urllib.parse import urlsplit

        host = urlsplit(url).hostname
        if not host:
            raise ConfigError("Set services.bors.url to the public HTTPS deployment URL")
        compose = f'''# Bors is operated by this project. Hosted mode provides only its database.
services:
  bors:
    image: "{image}"
    restart: unless-stopped
    env_file: [./bors.private.env]
    environment:
      PORT: "4000"
      PUBLIC_HOST: "{host}"
      PUBLIC_PROTOCOL: https
      PUBLIC_PORT: "443"
      FORCE_SSL: "false"
      DATABASE_USE_SSL: "true"
      DATABASE_AUTO_MIGRATE: "true"
      BORS_PROJECT: "{project.content}"
      BORS_BASE_BRANCH: "{project.branch}"
      BORS_STAGE_DISPATCH_PROJECT: "{project.content}"
      BORS_ASYNC_ORCHESTRATION: "true"
      BORS_ASYNC_WORKERS: "1"
      BORS_SPECULATIVE_TRAINS: "false"
    ports: ["127.0.0.1:4000:4000"]
'''
        heartbeat = """#!/usr/bin/env python3
import os
import urllib.request
from pathlib import Path

values = dict(line.split("=", 1) for line in Path(os.environ.get("TAUCETI_BORS_ENV_FILE", "/etc/tauceti-bors/bors.private.env")).read_text().splitlines() if line.strip() and not line.startswith("#") and "=" in line)
secret = values["GITHUB_WEBHOOK_SECRET"].strip().strip('"').strip("'")
request = urllib.request.Request("http://127.0.0.1:4000/internal/merge-reconcile", data=b"", headers={"x-bors-internal-secret": secret}, method="POST")
try:
    with urllib.request.urlopen(request, timeout=55) as response:
        if response.status != 200:
            raise SystemExit("Bors heartbeat failed")
except OSError:
    raise SystemExit("Bors heartbeat unavailable; inspect the local container logs") from None
"""
        files = {
            "compose.yaml": compose,
            "Caddyfile": f"{host} {{\n    @internal path /internal/*\n    respond @internal 404\n    reverse_proxy 127.0.0.1:4000\n}}\n",
            "tauceti-bors-heartbeat": heartbeat,
            "heartbeat.cron": "* * * * * TAUCETI_BORS_ENV_FILE="
            + shlex.quote(str(output.resolve() / "bors.private.env"))
            + " "
            + shlex.quote(str(output.resolve() / "tauceti-bors-heartbeat"))
            + "\n",
            "bors.private.env.example": "# Copy to bors.private.env, chmod 600, and enter actual values privately.\nDATABASE_URL=\nGITHUB_INTEGRATION_ID=\nGITHUB_INTEGRATION_PEM=\nGITHUB_WEBHOOK_SECRET=\nGITHUB_CLIENT_ID=\nGITHUB_CLIENT_SECRET=\nSECRET_KEY_BASE=\nTAUCETI_REVIEW_APP_ID=\nBORS_REVIEW_LOGIN=\n",
        }
    elif service == "bors":
        pin = project.tool("bors")
        if not pin.get("ref"):
            raise ConfigError(
                "Select [tools.bors] repo = 'TauCetiProject/bors-ng' and ref = a full commit SHA before preparing Cloudflare"
            )
        from urllib.parse import urlsplit

        host = urlsplit(config.get("url", "")).hostname
        account = config.get("account_id")
        if not host or not account:
            raise ConfigError("Set services.bors.url and account_id for the downstream Cloudflare account")
        name = project.key.replace("__", "-") + "-bors"
        wrangler = {
            "name": name,
            "account_id": account,
            "main": "bors/src/index.js",
            "compatibility_date": "2026-09-25",
            "routes": [{"pattern": host, "custom_domain": True}],
            "containers": [
                {
                    "class_name": "BorsContainer",
                    "image": "./bors/Dockerfile",
                    "max_instances": 1,
                    "instance_type": "standard-2",
                }
            ],
            "durable_objects": {"bindings": [{"name": "BORS", "class_name": "BorsContainer"}]},
            "migrations": [{"tag": "v1", "new_sqlite_classes": ["BorsContainer"]}],
            "triggers": {"crons": ["* * * * *"]},
            "queues": {
                "producers": [{"binding": "WEBHOOKS", "queue": name + "-webhooks"}],
                "consumers": [
                    {
                        "queue": name + "-webhooks",
                        "max_batch_size": 10,
                        "max_batch_timeout": 1,
                        "max_concurrency": 1,
                        "max_retries": 20,
                        "dead_letter_queue": name + "-webhooks-dlq",
                    }
                ],
            },
            "r2_buckets": [{"binding": "WEBHOOK_BODIES", "bucket_name": name + "-webhooks"}],
            "vars": {
                "BORS_PROJECT": project.content,
                "BORS_BASE_BRANCH": project.branch,
                "PUBLIC_HOST": host,
                "BORS_SPECULATIVE_TRAINS": "false",
                "BORS_STAGE_DISPATCH_PROJECT": project.content,
                **{
                    key: str(config[field])
                    for field, key in (
                        ("review_app_id", "TAUCETI_REVIEW_APP_ID"),
                        ("review_login", "BORS_REVIEW_LOGIN"),
                    )
                    if field in config
                },
            },
        }
        readme = f"""# Downstream Bors on Cloudflare

This project operates the runtime; hosted mode provides only Neon.
Run these commands in this directory, with Docker running:

```sh
gh repo clone {pin["repo"]} bors
git -C bors fetch origin {pin["ref"]}
git -C bors checkout --detach {pin["ref"]}
npm --prefix bors ci
bors/node_modules/.bin/wrangler login
bors/node_modules/.bin/wrangler queues create {name}-webhooks-dlq
bors/node_modules/.bin/wrangler queues create {name}-webhooks
bors/node_modules/.bin/wrangler r2 bucket create {name}-webhooks
bors/node_modules/.bin/wrangler deploy --config wrangler.downstream.jsonc
```

Upload secrets from private files with `python3 bors/deploy/cloudflare/upload_secrets.py --help`;
pass `--name {name}` and `--config wrangler.downstream.jsonc` as well as each private file.
Set the configured review App ID/login in vars before enabling merges.
Exact-run cancellation and speculative trains remain Tau Ceti-only.
The complete form fields and secret sources are in {DOCS}#bors.
If a resource already exists, reuse it; do not delete queues or archives during an upgrade.
"""
        files = {"wrangler.downstream.jsonc": json.dumps(wrangler, indent=2) + "\n", "README.md": readme}
    elif service == "github-state":
        files = {
            "project.json": json.dumps(
                {"repository": project.content, "url": config.get("url"), "mode": config["mode"]}, indent=2
            )
            + "\n",
            "README.md": f"# GitHub state deployment\n\nFollow {DOCS}#github-state.\n\nHosted: request enrollment; do not deploy or create another App.\nCustom: use services/github-state/scripts/setup.py from the pinned Tools checkout with --repository {project.content}.\n",
        }
    else:
        files = {
            "README.md": f"# {service} deployment\n\nProject: {project.content}\nMode: {config['mode']}\n\nFollow {DOCS}#{service}. No provider resources have been created.\n"
        }
    for name, text in files.items():
        target = output / name
        if target.exists():
            raise ConfigError(f"Refusing to overwrite {target}; compare it with a fresh --output directory")
        target.write_text(text)
        if name == "tauceti-bors-heartbeat":
            target.chmod(0o755)
    print(f"Prepared {service} deployment in {output}; no cloud resources or merge settings changed.")
    print(f"Manual: {DOCS}#{service}")
