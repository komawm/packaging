#!/usr/bin/env python3
"""Verify packaging metadata, multi-distro templates, and CD workflows without building or publishing."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib

from render_template import DISTRO_TEMPLATES, render_distro


ALLOWED_PLACEHOLDERS = {"@VERSION@", "@SOURCE_COMMIT@", "@SOURCE_SHA256@"}
DUMMY_COMMIT = "0123456789abcdef0123456789abcdef01234567"
DUMMY_SHA256 = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
EXPECTED_BINARIES = ["koma", "komactl", "koma-mcp", "koma-overview"]

SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
SHA_PIN = re.compile(r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40}(?:\s+#.*)?$")
DIGEST_IMAGE = re.compile(r"^[a-z0-9.-]+(?:/[a-z0-9.-]+)*:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    verify_repository_files(root)
    scan_for_secret_values(root)
    config = tomllib.loads((root / "packaging.toml").read_text(encoding="utf-8"))
    verify_top_level_policy(root, config)
    verify_all_distro_templates(root, config)
    if config["publish_enabled"] is True:
        verify_workflows(root, config)
    elif config["publish_enabled"] is not False:
        raise SystemExit("publish_enabled must be a boolean")
    print("Packaging metadata/template validation passed; no package was built or accepted")


def verify_repository_files(root: Path) -> None:
    """Validate TOML syntax, Python AST, and canonical link rules in README/AGENTS files."""
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix == ".toml":
            tomllib.loads(path.read_text(encoding="utf-8"))
        elif path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name in {"README.md", "AGENTS.md"}:
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if not target.startswith(("https://", "mailto:", "#")):
                    raise SystemExit(f"Noncanonical link in {path.relative_to(root)}: {target}")


def verify_top_level_policy(root: Path, config: dict) -> None:
    """Verify release gating, automatic publish lock, and license separation."""
    if config.get("accepted") is not False or config["release"].get("first_accepted_version") != "0.1.0":
        raise SystemExit("Packaging must remain unaccepted until the 0.1.0 acceptance gate")
    if config.get("automatic_publish") is not False:
        raise SystemExit("Automatic publishing must stay disabled; publishing is manual dispatch only")
    if config["infrastructure_license"] != "MIT" or config["product_license"] != "GPL-3.0-or-later":
        raise SystemExit("Packaging and product license metadata must remain separate")
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    if (
        "Permission is hereby granted, free of charge" not in license_text
        or len(license_text) < 900
        or list(root.glob("LICENSE*")) != [root / "LICENSE"]
    ):
        raise SystemExit("Keep one complete MIT LICENSE for packaging infrastructure")
    if config["release"]["targets"] != {"koma": "komawm/koma", "jiku": "komawm/jiku"}:
        raise SystemExit("Release artifacts belong to their owning product repository")


def verify_distro_metadata(root: Path, config: dict, distro: str) -> list[Path]:
    """Check packaging.toml metadata consistency and return all template/static paths for a distro."""
    section = config.get(distro)
    if not isinstance(section, dict):
        raise SystemExit(f"packaging.toml is missing [{distro}] metadata")
    if section.get("source_repository") != "https://github.com/komawm/koma":
        raise SystemExit(f"[{distro}] source_repository must target komawm/koma")
    if section.get("source_revision_required") != "full-40-character-commit":
        raise SystemExit(f"[{distro}] must require a full-40-character-commit")
    if section.get("source_checksum_required") != "sha256":
        raise SystemExit(f"[{distro}] must require sha256 source verification")
    if section.get("packaged_binaries") != EXPECTED_BINARIES:
        raise SystemExit(f"[{distro}] packaged_binaries must match {EXPECTED_BINARIES}")

    spec = DISTRO_TEMPLATES[distro]
    expected_templates = [rel for rel, _, _ in spec.templates]
    expected_static = [rel for rel, _, _ in spec.static_files]
    if spec.single_file:
        if [section.get("template")] != expected_templates:
            raise SystemExit(f"[{distro}] template must be {expected_templates[0]}")
    else:
        if section.get("templates") != expected_templates:
            raise SystemExit(f"[{distro}] templates must be {expected_templates}")
        if section.get("static_files", []) != expected_static:
            raise SystemExit(f"[{distro}] static_files must be {expected_static}")

    template_paths = [root / rel for rel in expected_templates]
    static_paths = [root / rel for rel in expected_static]
    for path in [*template_paths, *static_paths]:
        if not path.is_file():
            raise SystemExit(f"Missing {distro} packaging file: {path.relative_to(root)}")
    return template_paths


def verify_all_distro_templates(root: Path, config: dict) -> None:
    """Validate placeholder contracts, licensing, locked builds, and smoke rendering per distro."""
    license_checks: dict[str, tuple[tuple[str, ...], str]] = {
        "archlinux": (
            ('install -Dm644 LICENSE "$pkgdir/usr/share/licenses/koma/LICENSE"', "GPL-3.0-or-later"),
            "Arch template must preserve GPL-3.0-or-later and install LICENSE",
        ),
        "debian": (
            ("debian/koma/usr/share/doc/koma/LICENSE", "GPL-3.0-or-later"),
            "Debian template must install LICENSE and declare GPL-3.0-or-later in copyright",
        ),
        "rpm": (
            ("%license LICENSE", "License:        GPL-3.0-or-later"),
            "RPM template must declare GPL-3.0-or-later and package LICENSE",
        ),
        "alpine": (
            ('install -Dm644 LICENSE "$pkgdir/usr/share/licenses/koma/LICENSE"', 'license="GPL-3.0-or-later"'),
            "Alpine template must declare GPL-3.0-or-later and install LICENSE",
        ),
        "nix": (
            ('install -Dm644 LICENSE "$out/share/licenses/koma/LICENSE"', "gpl3Plus"),
            "Nix template must declare gpl3Plus and install LICENSE",
        ),
        "freebsd": (
            ("share/licenses/koma/LICENSE", "GPLv3+"),
            "FreeBSD template must declare GPLv3+ and install LICENSE",
        ),
        "homebrew": (
            ('license "GPL-3.0-or-later"', '.install "LICENSE"'),
            "Homebrew template must declare GPL-3.0-or-later and install LICENSE",
        ),
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for distro, spec in DISTRO_TEMPLATES.items():
            template_paths = verify_distro_metadata(root, config, distro)
            combined_parts: list[str] = []
            seen_placeholders: set[str] = set()
            for path in template_paths:
                text = path.read_text(encoding="utf-8")
                if "SKIP" in text:
                    raise SystemExit(f"Template must not use SKIP checksums: {path.relative_to(root)}")
                found = set(re.findall(r"@[A-Z0-9_]+@", text))
                unknown = found - ALLOWED_PLACEHOLDERS
                if unknown:
                    raise SystemExit(f"Unexpected placeholders {sorted(unknown)} in {path.relative_to(root)}")
                seen_placeholders |= found
                combined_parts.append(text)
            for rel, _, _ in spec.static_files:
                static_text = (root / rel).read_text(encoding="utf-8")
                if re.search(r"@[A-Z0-9_]+@", static_text):
                    raise SystemExit(f"Static file must not contain placeholders: {rel}")
                combined_parts.append(static_text)

            if seen_placeholders != ALLOWED_PLACEHOLDERS:
                raise SystemExit(
                    f"{distro} templates must bind {sorted(ALLOWED_PLACEHOLDERS)}, found {sorted(seen_placeholders)}"
                )
            combined_text = "\n".join(combined_parts)
            if "--locked" not in combined_text:
                raise SystemExit(f"{distro} templates must use --locked Cargo dependencies")
            needles, message = license_checks[distro]
            if any(needle not in combined_text for needle in needles):
                raise SystemExit(message)

            dest = tmp_dir / (f"{distro}-out" if not spec.single_file else spec.templates[0][1])
            written = render_distro(distro, DUMMY_COMMIT, DUMMY_SHA256, "0.0.1", dest, root=root)
            for rendered_path in written:
                rendered_text = rendered_path.read_text(encoding="utf-8")
                if re.search(r"@[A-Z0-9_]+@", rendered_text):
                    raise SystemExit(f"Unexpanded placeholder in rendered {distro} file: {rendered_path.name}")
            validate_rendered_distro(distro, dest, written)


def validate_rendered_distro(distro: str, dest: Path, written: list[Path]) -> None:
    """Run deterministic local syntax/structure checks on rendered templates."""
    if distro == "archlinux":
        run_checked(["bash", "-n", str(dest)], "Arch rendered PKGBUILD syntax check")
    elif distro == "alpine":
        run_checked(["sh", "-n", str(dest)], "Alpine rendered APKBUILD syntax check")
    elif distro == "homebrew":
        if shutil.which("ruby"):
            run_checked(["ruby", "-c", str(dest)], "Homebrew rendered Ruby syntax check")
    elif distro == "debian":
        rules = dest / "rules"
        if not (rules.stat().st_mode & stat.S_IXUSR):
            raise SystemExit("Rendered debian/rules must be executable")
        verify_make_recipes(rules, ("override_dh_auto_build:", "override_dh_auto_install:"))
        changelog = (dest / "changelog").read_text(encoding="utf-8")
        if not re.match(r"^koma \(0\.0\.1-1\) ", changelog):
            raise SystemExit("Rendered debian/changelog has invalid header")
    elif distro == "rpm":
        spec = dest.read_text(encoding="utf-8")
        for section in ("%description", "%prep", "%build", "%install", "%files", "%changelog"):
            if section not in spec:
                raise SystemExit(f"Rendered RPM spec missing {section}")
    elif distro == "freebsd":
        verify_make_recipes(dest / "Makefile", ("do-install:",))
        plist = (dest / "pkg-plist").read_text(encoding="utf-8")
        for binary in ("bin/koma", "bin/komactl", "bin/koma-mcp", "bin/koma-overview"):
            if binary not in plist:
                raise SystemExit(f"freebsd/pkg-plist missing {binary}")


def verify_make_recipes(makefile: Path, required_targets: tuple[str, ...]) -> None:
    """Verify required Makefile targets exist and recipe lines use literal tabs."""
    text = makefile.read_text(encoding="utf-8")
    for target in required_targets:
        match = re.search(rf"^{re.escape(target)}\n((?:\t.*\n?)+)", text, re.M)
        if not match:
            raise SystemExit(f"{makefile.name} target {target} must have tab-indented recipe lines")


def run_checked(cmd: list[str], label: str) -> None:
    """Run a local syntax-check tool and fail cleanly on non-zero exit."""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed:\n{result.stderr or result.stdout}")


def scan_for_secret_values(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".worktrees" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                raise SystemExit(
                    f"Possible secret value in {path.relative_to(root)}; store credentials only as GitHub secrets"
                )


def job_block(workflow: str, job: str) -> str:
    match = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", workflow, re.M | re.S)
    if not match:
        raise SystemExit(f"Workflow is missing job {job!r}")
    return match.group(1)


def all_jobs(workflow: str) -> dict[str, str]:
    """Return mapping of top-level job id -> job YAML block."""
    jobs_match = re.search(r"^jobs:\n(.*)\Z", workflow, re.M | re.S)
    if not jobs_match:
        return {}
    body = jobs_match.group(1)
    result: dict[str, str] = {}
    for match in re.finditer(r"^  ([A-Za-z0-9_-]+):\n(.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)", body, re.M | re.S):
        result[match.group(1)] = match.group(2)
    return result


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


def verify_workflow_common(workflow_path: Path, expected_secret: str) -> str:
    """Enforce universal security invariants on every GitHub Actions CD workflow."""
    workflow = workflow_path.read_text(encoding="utf-8")
    on_block = re.search(r"^on:\n(.*?)(?=^\S)", workflow, re.M | re.S)
    triggers = re.findall(r"^  ([a-z_]+):", on_block.group(1), re.M) if on_block else []
    if triggers != ["workflow_dispatch"]:
        raise SystemExit(f"{workflow_path.name} must be workflow_dispatch only, found {triggers}")
    if not re.search(r"^permissions:\n  contents: read\n(?!  )", workflow, re.M):
        raise SystemExit(f"{workflow_path.name} top-level permissions must be exactly contents: read")
    if re.search(r"^\s+[a-z-]+: write\b", workflow, re.M):
        raise SystemExit(f"{workflow_path.name} must not grant write permissions to GITHUB_TOKEN")
    if not re.search(r"publish:\n(?:\s+[a-z].*\n)*?\s+type: boolean\n(?:\s+[a-z].*\n)*?\s+default: false", workflow):
        raise SystemExit(f"{workflow_path.name} publish input must be a boolean defaulting to false")
    for line in workflow.splitlines():
        if re.match(r"^\s*(?:-\s+)?uses:", line) and not SHA_PIN.match(line):
            raise SystemExit(f"Action must be pinned by full commit SHA in {workflow_path.name}: {line.strip()}")
    for line in run_script_lines(workflow):
        if "${{" in line:
            raise SystemExit(
                f"Pass expressions to run scripts through env in {workflow_path.name}: {line.strip()}"
            )

    jobs = all_jobs(workflow)
    for job_name, block in jobs.items():
        if job_name != "publish" and "secrets." in block:
            raise SystemExit(f"Non-publish job {job_name!r} in {workflow_path.name} must not reference secrets")
        for image_match in re.finditer(r"^\s+(?:image|CONTAINER_IMAGE):\s*(\S+)$", block, re.M):
            image_ref = image_match.group(1)
            if not DIGEST_IMAGE.fullmatch(image_ref):
                raise SystemExit(
                    f"Container image in {workflow_path.name} job {job_name!r} must be pinned by sha256 digest: {image_ref}"
                )

    publish = job_block(workflow, "publish")
    if f"    environment: release\n" not in publish:
        raise SystemExit(f"{workflow_path.name} publish job must run under environment: release")
    if not re.search(r"^    if: .*inputs\.publish", publish, re.M):
        raise SystemExit(f"{workflow_path.name} publish job must be gated on inputs.publish")
    if f"GH_TOKEN: ${{{{ secrets.{expected_secret} }}}}" not in publish or "--clobber" not in publish:
        raise SystemExit(f"{workflow_path.name} publish job must use secrets.{expected_secret} and --clobber")
    if workflow.count("secrets.") != 1:
        raise SystemExit(f"{workflow_path.name} may reference secrets only once in the publish job")
    return workflow


def verify_prerelease_metadata(section: dict, config: dict, root: Path) -> Path:
    """Validate common prerelease metadata keys and return the referenced workflow path."""
    expected = {
        "publish_mode": "workflow_dispatch-with-release-environment",
        "publish_input_default": False,
        "environment": "release",
        "required_secret": "PRODUCT_RELEASE_TOKEN",
    }
    if not isinstance(section, dict) or any(section.get(k) != v for k, v in expected.items()):
        raise SystemExit(f"Prerelease section must declare {expected}")
    workflow_rel = section.get("workflow")
    if not isinstance(workflow_rel, str):
        raise SystemExit("Prerelease section must declare workflow")
    workflow_path = root / workflow_rel
    if not workflow_path.is_file():
        raise SystemExit(f"Publishing is enabled but {workflow_rel} is missing")
    if "products" in section and not set(section["products"]) <= set(config["release"]["targets"]):
        raise SystemExit("Development prerelease products must have a release target")
    return workflow_path


def verify_workflows(root: Path, config: dict) -> None:
    """Validate all CD workflows declared in packaging.toml and present in .github/workflows/."""
    pre = config.get("development_prerelease")
    if not isinstance(pre, dict) or pre.get("channel") != "development" or pre.get("accepted") is not False:
        raise SystemExit("[development_prerelease] must declare channel='development' and accepted=false")
    if not re.fullmatch(r"0\.0\.\d+", pre.get("version", "")):
        raise SystemExit("Development prereleases must use a 0.0.x version")

    arch_wf_path = verify_prerelease_metadata(pre, config, root)
    arch_wf = verify_workflow_common(arch_wf_path, pre["required_secret"])
    arch_build = job_block(arch_wf, "build")
    image = re.search(r"^\s+image: (\S+)$", arch_build, re.M)
    if not image or not re.fullmatch(r"archlinux:base-devel@sha256:[0-9a-f]{64}", image.group(1)):
        raise SystemExit("Arch build container must be archlinux:base-devel pinned by digest")
    if f"CONTAINER_IMAGE: {image.group(1)}" not in arch_build:
        raise SystemExit("Arch build CONTAINER_IMAGE must match the pinned container image")

    matrix = pre.get("matrix")
    if not isinstance(matrix, dict) or matrix.get("channels") != ["debian", "rpm", "alpine", "nix", "freebsd", "homebrew"]:
        raise SystemExit("[development_prerelease.matrix] must declare the multi-distro channels")
    matrix_wf_path = verify_prerelease_metadata(matrix, config, root)
    matrix_wf = verify_workflow_common(matrix_wf_path, matrix["required_secret"])
    for container_job, expected_prefix in (("build-rpm", "fedora:"), ("build-alpine", "alpine:")):
        block = job_block(matrix_wf, container_job)
        if f"CONTAINER_IMAGE: {expected_prefix}" not in block or "docker run" not in block:
            raise SystemExit(f"Matrix job {container_job} must run in a digest-pinned {expected_prefix} container")

    declared = {arch_wf_path.resolve(), matrix_wf_path.resolve()}
    on_disk = sorted((root / ".github/workflows").glob("*.yml"))
    for wf_path in on_disk:
        if wf_path.resolve() not in declared:
            raise SystemExit(f"Undeclared workflow in .github/workflows: {wf_path.name}")


if __name__ == "__main__":
    main()
