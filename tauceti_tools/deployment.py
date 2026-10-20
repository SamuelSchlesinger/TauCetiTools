"""Deployment preparation and explicit App roles; no implicit cloud provisioning."""

from __future__ import annotations

import json
from pathlib import Path

from .project import DOCS, ConfigError, Project

PERMISSIONS = {
    "automation": {"contents": "write", "pull_requests": "write", "issues": "write", "checks": "write", "statuses": "write", "actions": "write"},
    "claims": {"administration": "write", "contents": "write"},
    "collector": {"contents": "read", "actions": "read", "pull_requests": "read"},
    "github-state": {"contents": "read", "issues": "read", "pull_requests": "read", "checks": "read", "statuses": "read", "actions": "read"},
    "bors": {"contents": "write", "pull_requests": "write", "issues": "write", "checks": "read", "statuses": "write", "actions": "read", "actions_variables": "read"},
}
EVENTS = {
    "automation": [],
    "claims": [],
    "collector": [],
    "github-state": ["pull_request", "pull_request_review", "pull_request_review_comment", "issues", "issue_comment", "check_run", "check_suite", "status", "workflow_run", "workflow_job", "push"],
    "bors": ["pull_request", "issue_comment", "pull_request_review", "check_run", "check_suite", "status", "push", "installation", "installation_repositories"],
}


def app_manifest(project: Project, role: str) -> dict:
    service = project.service(role.replace("-", "_"))
    url = service.get("url", f"https://github.com/{project.content}")
    webhook = role in ("bors", "github-state")
    if webhook and not service.get("url"):
        raise ConfigError(f"Set services.{role.replace('-', '_')}.url to the actual deployment URL before registering its App")
    result = {
        "name": f"{project.content.split('/')[1]}-{role}",
        "url": url,
        "public": True,
        "description": f"{role} for {project.content}; installation does not grant hosted infrastructure access",
        "default_permissions": PERMISSIONS[role],
        "default_events": EVENTS[role],
        "hook_attributes": {"url": url + ("/webhook/github" if role == "bors" else "/webhook"), "active": webhook},
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
    ports: ["127.0.0.1:4000:4000"]
'''
        files = {"compose.yaml": compose, "Caddyfile": f"{host} {{\n    @internal path /internal/*\n    respond @internal 404\n    reverse_proxy 127.0.0.1:4000\n}}\n", "heartbeat.cron": "* * * * * /usr/local/bin/tauceti-bors-heartbeat\n", "bors.private.env.example": "# Copy to bors.private.env, chmod 600, and enter actual values privately.\nDATABASE_URL=\nGITHUB_INTEGRATION_ID=\nGITHUB_INTEGRATION_PEM=\nGITHUB_WEBHOOK_SECRET=\nGITHUB_CLIENT_ID=\nGITHUB_CLIENT_SECRET=\nSECRET_KEY_BASE=\nTAUCETI_REVIEW_APP_ID=\n"}
    elif service == "github-state":
        files = {"project.json": json.dumps({"repository": project.content, "url": config.get("url"), "mode": config["mode"]}, indent=2) + "\n", "README.md": f"# GitHub state deployment\n\nFollow {DOCS}#github-state.\n\nHosted: request enrollment; do not deploy or create another App.\nCustom: use services/github-state/scripts/setup.py from the pinned Tools checkout with --repository {project.content}.\n"}
    else:
        files = {"README.md": f"# {service} deployment\n\nProject: {project.content}\nMode: {config['mode']}\n\nFollow {DOCS}#{service}. No provider resources have been created.\n"}
    for name, text in files.items():
        target = output / name
        if target.exists():
            raise ConfigError(f"Refusing to overwrite {target}; compare it with a fresh --output directory")
        target.write_text(text)
    print(f"Prepared {service} deployment in {output}; no cloud resources or merge settings changed.")
    print(f"Manual: {DOCS}#{service}")
