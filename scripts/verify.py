#!/usr/bin/env python3
"""Verify packaging metadata and templates without building or publishing."""

import ast
from pathlib import Path
import re
import tomllib


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix == ".toml":
            tomllib.loads(path.read_text())
        elif path.suffix == ".py":
            ast.parse(path.read_text(), filename=str(path))
        if path.name in {"README.md", "AGENTS.md"}:
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if not target.startswith(("https://", "mailto:", "#")):
                    raise SystemExit(f"Noncanonical link in {path.relative_to(root)}: {target}")
    scan_for_secret_values(root)
    config = tomllib.loads((root / "packaging.toml").read_text())
    if config.get("accepted") is not False or config["release"].get("first_accepted_version") != "0.1.0":
        raise SystemExit("Packaging must remain unaccepted until the 0.1.0 acceptance gate")
    if config.get("automatic_publish") is not False:
        raise SystemExit("Automatic publishing must stay disabled; publishing is manual dispatch only")
    if config.get("publish_enabled") is True:
        verify_development_prerelease(root, config)
    elif config.get("publish_enabled") is not False:
        raise SystemExit("publish_enabled must be a boolean")
    if config["infrastructure_license"] != "MIT" or config["product_license"] != "GPL-3.0-or-later":
        raise SystemExit("Packaging and product license metadata must remain separate")
    license = (root / "LICENSE").read_text()
    if "Permission is hereby granted, free of charge" not in license or len(license) < 900 or list(root.glob("LICENSE*")) != [root / "LICENSE"]:
        raise SystemExit("Keep one complete MIT LICENSE for packaging infrastructure")
    if config["release"]["targets"] != {"koma": "komawm/koma", "jiku": "komawm/jiku"}:
        raise SystemExit("Release artifacts belong to their owning product repository")
    template = (root / config["archlinux"]["template"]).read_text()
    if set(re.findall(r"@[A-Z0-9_]+@", template)) != {"@VERSION@", "@SOURCE_COMMIT@", "@SOURCE_SHA256@"}:
        raise SystemExit("Template must require an explicit version, commit, and real checksum")
    if "SKIP" in template or "--locked" not in template:
        raise SystemExit("Template must verify source and use locked Cargo dependencies")
    if 'install -Dm644 LICENSE "$pkgdir/usr/share/licenses/koma/LICENSE"' not in template:
        raise SystemExit("Template must install the product's root LICENSE")
    print("Packaging metadata/template validation passed; no package was built or accepted")


SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
SHA_PIN = re.compile(r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40}(?:\s+#.*)?$")


def scan_for_secret_values(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".worktrees" in path.parts:
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise SystemExit(f"Possible secret value in {path.relative_to(root)}; store credentials only as GitHub secrets")


def job_block(workflow: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", workflow, re.M | re.S)
    if not match:
        raise SystemExit(f"Workflow is missing job {job!r}")
    return match.group(1)


def run_script_lines(workflow: str) -> list[str]:
    lines, in_run, indent = [], False, 0
    for line in workflow.splitlines():
        stripped = line.lstrip()
        current = len(line) - len(stripped)
        if in_run and stripped and current <= indent:
            in_run = False
        if in_run:
            lines.append(line)
        elif re.match(r"(?:-\s+)?run:\s*", stripped):
            in_run, indent = True, current
            lines.append(line)
    return lines


def verify_development_prerelease(root: Path, config: dict) -> None:
    pre = config.get("development_prerelease")
    expected = {
        "channel": "development",
        "accepted": False,
        "publish_mode": "workflow_dispatch-with-release-environment",
        "publish_input_default": False,
        "environment": "release",
        "required_secret": "PRODUCT_RELEASE_TOKEN",
    }
    if not isinstance(pre, dict) or any(pre.get(key) != value for key, value in expected.items()):
        raise SystemExit(f"[development_prerelease] must declare {expected}")
    if not re.fullmatch(r"0\.0\.\d+", pre.get("version", "")):
        raise SystemExit("Development prereleases must use a 0.0.x version")
    if not set(pre.get("products", [])) <= set(config["release"]["targets"]):
        raise SystemExit("Development prerelease products must have a release target")
    workflow_path = root / pre["workflow"]
    if not workflow_path.is_file():
        raise SystemExit(f"Publishing is enabled but {pre['workflow']} is missing")
    workflow = workflow_path.read_text()
    on_block = re.search(r"^on:\n(.*?)(?=^\S)", workflow, re.M | re.S)
    triggers = re.findall(r"^  ([a-z_]+):", on_block.group(1), re.M) if on_block else []
    if triggers != ["workflow_dispatch"]:
        raise SystemExit(f"CD workflow must be workflow_dispatch only, found {triggers}")
    if not re.search(r"^permissions:\n  contents: read\n(?!  )", workflow, re.M):
        raise SystemExit("CD workflow top-level permissions must be exactly contents: read")
    if re.search(r"^\s+[a-z-]+: write\b", workflow, re.M):
        raise SystemExit("CD workflow must not grant write permissions to GITHUB_TOKEN")
    if not re.search(r"publish:\n(?:\s+[a-z].*\n)*?\s+type: boolean\n(?:\s+[a-z].*\n)*?\s+default: false", workflow):
        raise SystemExit("CD workflow publish input must be a boolean defaulting to false")
    for line in workflow.splitlines():
        if re.match(r"^\s*(?:-\s+)?uses:", line) and not SHA_PIN.match(line):
            raise SystemExit(f"Action must be pinned by full commit SHA: {line.strip()}")
    for line in run_script_lines(workflow):
        if "${{" in line:
            raise SystemExit(f"Pass expressions to run scripts through env, not inline: {line.strip()}")
    build = job_block(workflow, "build")
    image = re.search(r"^\s+image: (\S+)$", build, re.M)
    if not image or not re.fullmatch(r"archlinux:base-devel@sha256:[0-9a-f]{64}", image.group(1)):
        raise SystemExit("Build container must be archlinux:base-devel pinned by digest")
    if f"CONTAINER_IMAGE: {image.group(1)}" not in build:
        raise SystemExit("Build CONTAINER_IMAGE must match the pinned container image")
    if "secrets." in build:
        raise SystemExit("The build job must not receive secrets")
    publish = job_block(workflow, "publish")
    required = (
        "    if: ${{ inputs.publish }}\n",
        "    needs: build\n",
        f"    environment: {pre['environment']}\n",
        f"GH_TOKEN: ${{{{ secrets.{pre['required_secret']} }}}}",
        "--clobber",
    )
    for fragment in required:
        if fragment not in publish:
            raise SystemExit(f"Publish job must contain {fragment.strip()!r}")
    if workflow.count("secrets.") != 1:
        raise SystemExit("Only the publish job may reference exactly one secret")


if __name__ == "__main__":
    main()
