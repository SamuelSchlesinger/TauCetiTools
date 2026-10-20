"""Pinned project templates and shared script execution."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .project import CANONICAL, Project, bootstrap

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"


def substitutions(project: Project) -> list[tuple[str, str]]:
    """Render only trusted tool templates, never contributor/agent content."""
    root = project.roots[0]
    if len(project.roots) != 1:
        raise ValueError("This release's build harness supports one Lean library root; split the library or keep its existing custom build workflow")
    pairs = [
        ("TauCetiProject/TauCetiRoadmap", project.repos["roadmap"]),
        ("TauCetiProject/TauCetiReview", project.tool("review").get("repo", "TauCetiProject/TauCetiReview")),
        ("TauCetiProject/TauCetiProgress", project.tool("progress").get("repo", "TauCetiProject/TauCetiProgress")),
        ("TauCetiProject/TauCetiData", project.repos.get("data", "")),
        ("TauCetiProject/TauCetiCI", project.repos.get("ci", "")),
        ("TauCetiProject/tauceti-claims", project.repos.get("claims", "")),
        (CANONICAL, project.content),
        ("https://bors.taucetiproject.org", project.service("bors").get("url", "")),
        ("https://taucetiproject.github.io/TauCeti/docs", project.raw.get("publication", {}).get("docs_url", "")),
        ("TauCetiRoadmap", project.roadmap_dir),
        ("TauCeti.lean", root.replace(".", "/") + ".lean"),
        ("TauCeti/", root.replace(".", "/") + "/"),
        ("TauCeti\\.", root.replace(".", r"\.") + r"\."),
        ("TauCeti.*", root + ".*"),
        ("`TauCeti", "`" + root),
        ('"TauCeti"', json.dumps(root)),
        ("'TauCeti'", repr(root)),
        ("origin/main", "origin/" + project.branch),
    ]
    # Single substitution pass: names appearing inside a replacement are data.
    return pairs


def render(text: str, project: Project) -> str:
    import re

    pairs = dict(substitutions(project))
    pattern = re.compile("|".join(re.escape(key) for key in sorted(pairs, key=len, reverse=True)))
    return pattern.sub(lambda m: pairs[m.group()], text)


def materialize(project: Project, destination: Path, *, policy_dir: Path | None = None):
    destination.mkdir(parents=True, exist_ok=True)
    for source in (ASSETS / "scripts").rglob("*"):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        target = destination / source.relative_to(ASSETS / "scripts")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            content = render(source.read_text(), project)
        except UnicodeError:
            shutil.copyfile(source, target)
        else:
            target.write_text(content)
        target.chmod(source.stat().st_mode & 0o777)
    if policy_dir and policy_dir.is_dir():
        for source in policy_dir.iterdir():
            if source.suffix in (".txt", ".json") and source.is_file():
                shutil.copyfile(source, destination / source.name)


def run_script(name: str, args: list[str], project: Project | None = None) -> int:
    if not name or ".." in Path(name).parts or Path(name).is_absolute():
        raise ValueError("Script name must be a relative shared script path")
    source = ASSETS / "scripts" / name
    if not source.is_file():
        raise ValueError(f"No shared script named {name}")
    if project is None:
        project = bootstrap([])
    if project is None:
        # Legacy Tau Ceti operation needs no new configuration.
        executable = source
    else:
        destination = Path(tempfile.mkdtemp(prefix="tauceti-scripts-"))
        materialize(project, destination, policy_dir=Path.cwd() / "scripts")
        executable = destination / name
    command = ["python3", str(executable)] if executable.suffix == ".py" else ["bash", str(executable)]
    return subprocess.call([*command, *args])


def write_project(project: Project, checkout: Path) -> list[str]:
    """Install generated callers and project policy, preserving existing policy files."""
    written = []
    for source in (ASSETS / "project").rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(ASSETS / "project")
        if relative.parts[0] == "scripts" and source.suffix in (".txt", ".json") and (checkout / relative).exists():
            continue
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text = render(source.read_text(), project)
        tools = project.tool("tools")
        if tools.get("ref"):
            text = text.replace("__TOOLS_REPO__", tools["repo"]).replace("__TOOLS_REF__", tools["ref"])
        review = project.tool("review")
        if review.get("ref"):
            import re

            text = re.sub(r"(TauCetiReview/\.github/workflows/[^@\s]+@)[0-9a-f]{40}", lambda m, ref=review["ref"]: m[1] + ref, text)
        target.write_text(text)
        target.chmod(source.stat().st_mode & 0o777)
        written.append(str(relative))
    return written


def configuration_pr(project: Project, *, apply=False):
    pin = project.tool("tools")
    if not pin.get("ref"):
        raise ValueError("Select [tools.tools] repo and ref before installing workflows")
    print(f"Install pinned Tools helpers and workflow callers in {project.content}:{project.branch}; open a human-reviewed PR.")
    if not apply:
        print("No files or repositories changed. Add --apply to prepare the PR.")
        return
    directory = Path(tempfile.mkdtemp(prefix="tauceti-setup-"))
    subprocess.run(["gh", "repo", "clone", project.content, str(directory), "--", "--depth", "1", "--branch", project.branch], check=True)
    branch = "setup/tauceti-tools"
    subprocess.run(["git", "-C", str(directory), "switch", "-c", branch], check=True)
    write_project(project, directory)
    if project.source and not project.source.startswith("https:"):
        shutil.copyfile(project.source, directory / "tauceti.toml")
    subprocess.run(["git", "-C", str(directory), "add", ".github", "scripts", "tauceti.toml"], check=True)
    if subprocess.run(["git", "-C", str(directory), "diff", "--cached", "--quiet"]).returncode == 0:
        print("Already configured; no PR needed.")
        return
    subprocess.run(["git", "-C", str(directory), "commit", "-m", "Configure shared Tau Ceti project tooling"], check=True)
    # gh pr create can choose the operator's fork when upstream write access is absent.
    subprocess.run(["git", "-C", str(directory), "push", "-u", "origin", branch], check=True)
    body = directory / ".git/tauceti-pr-body"
    body.write_text("Configure the project with pinned shared TauCetiTools helpers and workflows.\n\nRoadmap: none\n")
    subprocess.run(["gh", "pr", "create", "--repo", project.content, "--base", project.branch, "--head", branch, "--title", "Configure shared Tau Ceti tools", "--body-file", str(body)], cwd=directory, check=True)
