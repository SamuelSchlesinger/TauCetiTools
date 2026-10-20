"""Repository diagnostics and repeatable repairs. Checking never writes remotely."""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .project import DOCS, Project


@dataclass
class Finding:
    id: str
    status: str
    summary: str
    expected: str
    role: str = "Repository administrator"
    commands: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    verify: str = ""
    section: str = "repositories"
    repair: str = ""

    def json(self):
        result = asdict(self)
        result["manual"] = DOCS + "#" + self.section
        return result


class GitHub:
    def __init__(self):
        self.cache = {}

    def read(self, path: str):
        if path not in self.cache:
            try:
                proc = subprocess.run(["gh", "api", "--paginate", "--slurp", path], capture_output=True, text=True)
            except FileNotFoundError:
                self.cache[path] = (None, "gh is not installed")
            else:
                if proc.returncode:
                    # No response bodies: an endpoint can include credentials or private data.
                    code = next((x for x in ("401", "403", "404", "429", "500", "502", "503") if x in proc.stderr), "unknown")
                    self.cache[path] = (None, f"GitHub HTTP {code}; inaccessible does not establish absence")
                else:
                    try:
                        pages = json.loads(proc.stdout)
                        self.cache[path] = (pages[0] if len(pages) == 1 else pages, "")
                    except (ValueError, IndexError):
                        self.cache[path] = (None, "GitHub returned an unreadable response")
        return self.cache[path]


def _pages(value):
    return value if isinstance(value, list) else [value]


def _items(value, key):
    if value is None:
        return []
    return [item for page in _pages(value) for item in (page.get(key, []) if isinstance(page, dict) else page)]


def _cmd(*args):
    return shlex.join(str(x) for x in args)


def diagnose(project: Project, *, extras=False, github=None) -> list[Finding]:
    gh = github or GitHub()
    findings = []
    conf = project.source
    again = _cmd("tauceti-tools", "doctor", "--conf", conf)
    repo_info = {}
    wanted = {"content": project.content, "roadmap": project.repos["roadmap"]}
    if extras:
        wanted.update({k: v for k, v in project.repos.items() if k in ("data", "ci", "claims")})
    for role, repo in wanted.items():
        data, error = gh.read(f"repos/{repo}")
        repo_info[role] = data
        commands = [_cmd("gh", "auth", "status"), _cmd("gh", "repo", "view", repo)]
        if error:
            findings.append(Finding(f"repositories.{role}", "unknown", f"{repo}: {error}", "Repository exists and is accessible", commands=commands + [_cmd("gh", "repo", "create", repo, "--public", "--add-readme")], urls=[f"https://github.com/{repo}", "https://github.com/new"], steps=["Sign in as an organization owner or repository administrator.", "First check repository access with gh repo view. Create the repository only if the owner confirms it is absent.", f"For creation choose Owner: {repo.split('/')[0]}; Repository name: {repo.split('/')[1]}; Visibility: Public; initialize a README."], verify=_cmd("gh", "repo", "view", repo, "--json", "nameWithOwner,defaultBranchRef")))
            continue
        findings.append(Finding(f"repositories.{role}", "ready", repo, "Repository accessible"))
        if data.get("archived") or data.get("disabled"):
            findings.append(Finding(f"repositories.{role}.writable", "failed", "Repository archived or disabled", "Active repository", commands=[_cmd("gh", "repo", "unarchive", repo)], urls=[f"https://github.com/{repo}/settings"], steps=["Under General → Danger Zone, select Unarchive this repository. A disabled repository requires its owner to contact GitHub support."], verify=again))
        if role == "content" and data.get("default_branch") != project.branch:
            findings.append(Finding("project.branch", "failed", f"GitHub default branch is {data.get('default_branch')}", f"Configuration branch is {project.branch}", commands=[_cmd("gh", "repo", "view", repo, "--json", "defaultBranchRef")], urls=[f"https://github.com/{repo}/settings"], steps=[f"Set project.branch in {conf} to {data.get('default_branch')!r}, or intentionally change General → Default branch to the configured branch. Do not target another PR's branch."], verify=again))

    repo = project.content
    if repo_info.get("content"):
        value, error = gh.read(f"repos/{repo}/actions/permissions")
        status = "unknown" if error else "ready" if value.get("enabled") else "failed"
        findings.append(Finding("actions.enabled", status, error or f"Actions enabled: {value.get('enabled')}", "GitHub Actions enabled", commands=[_cmd("gh", "api", "--method", "PUT", f"repos/{repo}/actions/permissions", "-F", "enabled=true")], urls=[f"https://github.com/{repo}/settings/actions"], steps=["Open Settings → Actions → General. Enable Actions and allow the pinned actions and reusable workflows named in the generated workflow files. An organization policy may need its owner to allow these repositories."], verify=_cmd("gh", "api", f"repos/{repo}/actions/permissions"), section="actions", repair="actions"))

        for path in ("tauceti.toml", ".github/CODEOWNERS", ".github/workflows/pr-build.yml", ".github/workflows/auto-merge.yml"):
            value, error = gh.read(f"repos/{repo}/contents/{path}?ref={project.branch}")
            status = "unknown" if error else "ready"
            findings.append(Finding("files." + path, status, error or path, "Trusted default-branch configuration and workflow", commands=[_cmd("tauceti-tools", "doctor", "--conf", conf, "--fix", "workflows", "--apply")], urls=[f"https://github.com/{repo}/tree/{project.branch}/.github"], steps=["Install the pinned project templates in a local checkout, inspect the diff, and open the configuration PR. Merge through the normal human review process."], verify=again, section="workflows", repair="workflows"))

        rules, error = gh.read(f"repos/{repo}/rules/branches/{project.branch}")
        active_rules = [r for page in _pages(rules or []) for r in (page if isinstance(page, list) else [page])]
        types = {r.get("type") for r in active_rules if isinstance(r, dict)}
        needed = {"pull_request", "required_status_checks"}
        if project.raw.get("merge", {}).get("backend", "github-queue") == "github-queue":
            needed.add("merge_queue")
        missing = sorted(needed - types)
        findings.append(Finding("rules.content", "unknown" if error else "failed" if missing else "ready", error or ("Missing rules: " + ", ".join(missing) if missing else "Required rule types present; inspect configured checks"), "PR review and required build, bump-guard, scope checks; merge queue when selected", commands=[_cmd("gh", "api", f"repos/{repo}/rules/branches/{project.branch}")], urls=[f"https://github.com/{repo}/settings/rules/new?target=branch"], steps=[f"Name: Tau Ceti content. Enforcement: Active. Target branch: {project.branch}.", "Enable Require a pull request before merging; require code-owner review for human-owned configuration. Add required status checks build, bump-guard and scope from their expected GitHub Actions producer.", "For github-queue enable Require merge queue. Keep human-owned changes subject to code-owner review. Set the automation App's bypass, if needed, to pull requests only; never grant content authors unconditional bypass."], verify=again, section="branch-rules"))

        metadata = []
        errors = []
        for endpoint in ("secrets", "organization-secrets"):
            value, error = gh.read(f"repos/{repo}/actions/{endpoint}")
            metadata.extend(_items(value, "secrets"))
            if error:
                errors.append(error)
        names = {row["name"] for row in metadata}
        for name in ("APP_ID", "APP_PRIVATE_KEY"):
            present = name in names
            secret_file = Path.home() / ".config/tauceti/projects" / project.key / "automation" / name
            findings.append(Finding("secrets." + name, "ready" if present else "unknown" if errors else "failed", f"{name}: present, not functionally verified" if present else "; ".join(errors) or f"{name} unavailable", "Automation App credential available to trusted workflows", commands=[f"{_cmd('gh', 'secret', 'set', name, '--repo', repo)} < {shlex.quote(str(secret_file))}"], urls=[f"https://github.com/{repo}/settings/secrets/actions", f"https://github.com/organizations/{repo.split('/')[0]}/settings/apps"], steps=["Register the project-owned automation App using the App instructions in the manual.", f"Save the {'numeric App ID' if name == 'APP_ID' else 'downloaded PEM private key'} in {secret_file}; chmod 600 that file.", f"In Settings → Secrets and variables → Actions → New repository secret, Name: {name}; Secret: the contents of that file.", "Organization secrets must include this repository in their access policy. A same-named environment secret can override the repository value."], verify=again, section="apps-and-credentials"))

        for tool in ("tools", "review", "progress"):
            pin = project.tool(tool)
            required = tool != "progress" or project.feature("progress")
            if not required:
                continue
            findings.append(Finding("pins." + tool, "ready" if pin.get("ref") else "failed", pin.get("ref", "No version selected"), "A full commit SHA in the common configuration", commands=[_cmd("gh", "api", f"repos/{pin.get('repo', 'TauCetiProject/TauCeti' + tool.title())}/commits/HEAD", "--jq", ".sha")], steps=[f"Put the selected repository and full SHA in [tools.{tool}], then regenerate the workflow callers with the workflows repair. Review and generator versions must agree with their corresponding merge workflows."], verify=again, section="versions"))

        runs, error = gh.read(f"repos/{repo}/actions/runs?branch={project.branch}&per_page=20")
        workflow_runs = _items(runs, "workflow_runs")
        for consumer in ("pr-build", "Auto-merge"):
            candidates = [r for r in workflow_runs if r.get("name") == consumer]
            latest = candidates[0] if candidates else None
            findings.append(Finding("evidence." + consumer.lower(), "pending", error or (f"Latest run {latest['id']}: {latest.get('conclusion') or latest.get('status')}; inspect the consuming job" if latest else "No consuming workflow run observed"), "Relevant credential-consuming step succeeds without skipping or fallback", commands=([_cmd("gh", "run", "view", str(latest["id"]), "--repo", repo, "--log")] if latest else [_cmd("gh", "workflow", "list", "--repo", repo)]), urls=[latest["html_url"] if latest else f"https://github.com/{repo}/actions"], steps=["Use the existing workflow's normal trigger. pr-build runs on a content pull request; Auto-merge runs after a build or review update.", "A successful overall run is not proof that an optional credential was used. Inspect the App-token and consuming steps; skipped/fallback steps leave the credential unverified."], verify=again, section="verification"))

    if extras:
        findings.extend(extra_findings(project, gh, again))
    return findings


def extra_findings(project, gh, again):
    results = []
    owner = project.content.split("/")[0]
    for name in ("github_state", "bors", "cache"):
        service = project.service(name)
        mode = service.get("mode", "off")
        results.append(Finding("services." + name, "disabled" if mode == "off" else "pending", f"{name}: {mode}", "Selected optional integration", commands=[_cmd("tauceti-tools", "deploy", name.replace("_", "-"), "--conf", project.source)], urls=([service["url"]] if service.get("url") else []), steps=(["This integration is intentionally disabled. Change its mode in the configuration to set it up."] if mode == "off" else ["Follow the deployment section for this selected provider; the deploy command writes local templates and never switches merge authority."]), verify=again + " --extras", section=name.replace("_", "-")))
        if mode == "hosted":
            title = f"Infrastructure request: {project.content} ({name})"
            url = "https://github.com/TauCetiProject/TauCetiTools/issues/new?" + __import__("urllib.parse", fromlist=["urlencode"]).urlencode({"title": title, "body": f"Repository: {project.content}\nService: {name}\nConfiguration: {project.source}\nRequested operator: {owner}\nPlease approve this repository and provide scoped setup instructions. No credentials are included."})
            results.append(Finding(f"services.{name}.enrollment", "pending", "Hosted access needs operator approval", "An explicit operator grant for this repository", role="Tau Ceti infrastructure operator", urls=[url], steps=["Open the prefilled infrastructure request, confirm the repository and configuration URL, and submit it.", "For Bors, hosted means a separate Neon project only. Operate the Bors runtime in your own account.", "The operator records approval and supplies scoped credentials privately; do not put credentials in the issue."], verify=again + " --extras", section=name.replace("_", "-")))
    for name in ("archives", "ci_archive", "claims", "docs", "progress", "zulip", "fleet", "security_audit"):
        enabled = project.feature(name)
        results.append(Finding("features." + name, "pending" if enabled else "disabled", f"{name}: {'enabled' if enabled else 'off'}", "Configured optional feature", commands=[_cmd("tauceti-tools", "doctor", "--conf", project.source, "--fix", "workflows", "--apply")], urls=[f"https://github.com/{project.content}/settings"], steps=[f"Follow the {name.replace('_', ' ')} section in the manual. Install only its declared workflows and credentials.", "For claims repositories disable Actions; for announcements supply an explicit destination before enabling publication."], verify=again + " --extras", section="extras"))
    return results


def repair(project: Project, check: str, *, apply=False):
    if check == "actions":
        command = ["gh", "api", "--method", "PUT", f"repos/{project.content}/actions/permissions", "-F", "enabled=true"]
        print(_cmd(*command))
        if apply:
            subprocess.run(command, check=True)
        return
    if check == "workflows" or check.startswith("files."):
        from .templates import configuration_pr

        configuration_pr(project, apply=apply)
        return
    raise ValueError(f"No automatic repair for {check}. Use its printed manual commands and UI instructions.")


def render(findings, *, json_output=False):
    if json_output:
        print(json.dumps([item.json() for item in findings], indent=2))
        return
    for item in findings:
        print(f"[{item.status.upper()}] {item.id}: {item.summary}")
        if item.status in ("ready", "disabled"):
            continue
        print(f"  Expected: {item.expected}\n  Who: {item.role}")
        for command in item.commands:
            print(f"  $ {command}")
        for url in item.urls:
            print(f"  Open: {url}")
        for index, step in enumerate(item.steps, 1):
            print(f"  {index}. {step}")
        if item.verify:
            print(f"  Verify: {item.verify}")
        print(f"  Manual: {DOCS}#{item.section}")
