#!/usr/bin/env python3
"""Verify packaging metadata, multi-distro templates, nFPM configs, and CD workflows without PyYAML or publishing."""

from __future__ import annotations

import ast
import base64
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
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
SHA_PIN = re.compile(
    r"^\s*(?:-\s+)?uses:\s+(?:[\w.-]+/[\w./-]+@[0-9a-f]{40}|docker://[\w./:-]+@sha256:[0-9a-f]{64})(?:\s+#.*)?$"
)
DIGEST_IMAGE = re.compile(r"^[a-z0-9.-]+(?:/[a-z0-9.-]+)*:[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")
FORBIDDEN_EPHEMERAL_RUNNERS = re.compile(r"(?<![A-Za-z0-9_-])(?:bunx|npx|pipx\s+run|uvx)(?![A-Za-z0-9_-])")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    verify_repository_files(root)
    verify_vm_harnesses(root)
    verify_apt_files(root)
    scan_for_secret_values(root)
    config = tomllib.loads((root / "packaging.toml").read_text(encoding="utf-8"))
    verify_top_level_policy(root, config)
    verify_all_distro_templates(root, config)
    verify_prebuilt_templates(root, config)
    verify_nfpm_specs(root, config)
    verify_dependabot(root)
    if config["publish_enabled"] is True:
        verify_workflows(root, config)
    elif config["publish_enabled"] is not False:
        raise SystemExit("publish_enabled must be a boolean")
    print("Packaging metadata, templates, nFPM specs, and workflow gates passed; no package was built or accepted")


def validate_yaml_syntax(text: str, rel_path: Path) -> None:
    """Perform stdlib-only structural YAML sanity checks (no tabs, valid block scalar/mapping indentation)."""
    block_indent: int | None = None
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        leading = len(raw_line) - len(raw_line.lstrip(" \t"))
        prefix = raw_line[:leading]
        if "\t" in prefix:
            raise SystemExit(f"YAML indentation must not use tabs in {rel_path}:{lineno}")
        if block_indent is not None:
            if leading > block_indent:
                continue
            block_indent = None
        stripped = raw_line.lstrip(" ")
        if stripped.startswith("#") or stripped in {"---", "..."}:
            continue
        if stripped.endswith("|") or stripped.endswith("|-") or stripped.endswith(">") or stripped.endswith(">-"):
            block_indent = leading
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item.endswith("|") or item.endswith("|-") or item.endswith(">") or item.endswith(">-"):
                block_indent = leading + 2
            continue
        if stripped == "-":
            continue
        if ":" not in stripped:
            raise SystemExit(f"Malformed YAML line in {rel_path}:{lineno}: {raw_line}")


def parse_nfpm_yaml(text: str, rel_path: Path) -> tuple[dict[str, str], list[str]]:
    """Extract top-level scalar fields and contents[].dst paths from an nFPM v2 YAML spec using stdlib."""
    validate_yaml_syntax(text, rel_path)
    scalars: dict[str, str] = {}
    dsts: list[str] = []
    in_contents = False
    block_indent: int | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None

        if indent == 0:
            in_contents = stripped == "contents:"
            if not in_contents and ":" in stripped:
                key, val = stripped.split(":", 1)
                val = val.strip()
                if val in {"|", "|-", ">", ">-"}:
                    block_indent = 0
                elif val:
                    scalars[key.strip()] = val.strip("\"'")
        elif in_contents:
            dst_match = re.match(r"^(?:-\s+)?dst:\s*(\S+)", stripped)
            if dst_match:
                dsts.append(dst_match.group(1).strip("\"'"))
    return scalars, dsts


def verify_repository_files(root: Path) -> None:
    """Validate TOML syntax, Python AST, stdlib YAML sanity, and canonical links in README/AGENTS files."""
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or ".worktrees" in path.parts:
            continue
        rel = path.relative_to(root)
        if path.suffix == ".toml":
            tomllib.loads(path.read_text(encoding="utf-8"))
        elif path.suffix in {".yml", ".yaml"}:
            validate_yaml_syntax(path.read_text(encoding="utf-8"), rel)
        elif path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if path.name in {"README.md", "AGENTS.md"}:
            for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if not target.startswith(("https://", "mailto:", "#")):
                    raise SystemExit(f"Noncanonical link in {rel}: {target}")
        if path.suffix in {".yml", ".yaml", ".sh"} or path.parent.name == "scripts":
            if path.suffix in {".yml", ".yaml", ".sh"}:
                content = path.read_text(encoding="utf-8")
                if FORBIDDEN_EPHEMERAL_RUNNERS.search(content):
                    raise SystemExit(f"Forbidden ephemeral runner (bunx/npx/pipx run/uvx) in {rel}")


def verify_vm_harnesses(root: Path) -> None:
    """Validate VM helper permissions, syntax, --help output, and non-hardcoded ISO paths."""
    vm_dir = root / "vm"
    expected_exec = (
        "arch-vm.sh",
        "freebsd-vm.sh",
        "guest-install.sh",
        "koma-smoke.sh",
        "wayland-probe.py",
    )
    for name in expected_exec:
        path = vm_dir / name
        if not path.is_file():
            raise SystemExit(f"Missing VM helper script: vm/{name}")
        if not (path.stat().st_mode & stat.S_IXUSR):
            raise SystemExit(f"VM helper script must be executable: vm/{name}")
        text = path.read_text(encoding="utf-8")
        if re.search(r"/mnt/(?:music|data)|/Workspace/", text):
            raise SystemExit(f"Hardcoded workstation path in vm/{name}; use ISO_PATH or env vars")
        if path.suffix == ".sh":
            run_checked(["bash", "-n", str(path)], f"bash -n vm/{name}")

    for cli_name in ("arch-vm.sh", "freebsd-vm.sh", "koma-smoke.sh", "wayland-probe.py"):
        run_checked([str(vm_dir / cli_name), "--help"], f"vm/{cli_name} --help")

    arch_text = (vm_dir / "arch-vm.sh").read_text(encoding="utf-8")
    fbsd_text = (vm_dir / "freebsd-vm.sh").read_text(encoding="utf-8")
    if "ISO_PATH" not in arch_text or "ISO_PATH" not in fbsd_text:
        raise SystemExit("Both arch-vm.sh and freebsd-vm.sh must discover ISOs via ISO_PATH")
    if "VM_SSH_PORT:-2223" not in fbsd_text or "VM_NAME:-freebsd-koma" not in fbsd_text:
        raise SystemExit("freebsd-vm.sh must keep distinct defaults (freebsd-koma, port 2223)")


def verify_apt_files(root: Path) -> None:
    """Validate R2-hosted deb822 APT repository source and setup script."""
    sources_path = root / "apt/komawm.sources"
    setup_path = root / "apt/setup.sh"
    if not sources_path.is_file():
        raise SystemExit("Missing apt/komawm.sources")
    sources_text = sources_path.read_text(encoding="utf-8")
    for expected in ("Types: deb", "URIs: https://cdn.komawm.org/apt", "Suites: stable", "Architectures: amd64 arm64"):
        if expected not in sources_text:
            raise SystemExit(f"apt/komawm.sources missing required line: {expected}")
    if not setup_path.is_file() or not (setup_path.stat().st_mode & stat.S_IXUSR):
        raise SystemExit("apt/setup.sh must exist and be executable")
    run_checked(["bash", "-n", str(setup_path)], "bash -n apt/setup.sh")


def verify_top_level_policy(root: Path, config: dict) -> None:
    """Verify release gating, automatic publish lock, CDN metadata, and license separation."""
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
    cdn = config.get("cdn", {})
    if cdn.get("provider") != "cloudflare-r2" or cdn.get("bucket") != "komawm" or cdn.get("origin") != "https://cdn.komawm.org":
        raise SystemExit("[cdn] must target cloudflare-r2 bucket komawm at https://cdn.komawm.org")


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
        "openbsd": (
            ("share/licenses/koma/LICENSE", "GPLv3+"),
            "OpenBSD template must declare GPLv3+ and install LICENSE",
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
            validate_rendered_distro(distro, dest)

        # Also validate Jiku Arch PKGBUILD template and render_arch.py --product jiku.
        jiku_rel = config["archlinux"].get("jiku_template")
        if not isinstance(jiku_rel, str):
            raise SystemExit("[archlinux] must declare jiku_template")
        jiku_tpl = root / jiku_rel
        if not jiku_tpl.is_file():
            raise SystemExit(f"Missing Jiku Arch template: {jiku_rel}")
        jiku_text = jiku_tpl.read_text(encoding="utf-8")
        if "SKIP" in jiku_text or "--locked" not in jiku_text:
            raise SystemExit("Jiku Arch template must verify source and use --locked Cargo dependencies")
        if set(re.findall(r"@[A-Z0-9_]+@", jiku_text)) != ALLOWED_PLACEHOLDERS:
            raise SystemExit(f"Jiku Arch template must bind {sorted(ALLOWED_PLACEHOLDERS)}")
        if 'install -Dm644 LICENSE "$pkgdir/usr/share/licenses/jiku/LICENSE"' not in jiku_text:
            raise SystemExit("Jiku Arch template must install LICENSE under /usr/share/licenses/jiku/")
        if "GPL-3.0-or-later" not in jiku_text:
            raise SystemExit("Jiku Arch template must declare GPL-3.0-or-later")
        jiku_out = tmp_dir / "PKGBUILD.jiku"
        run_checked(
            [
                sys.executable,
                str(root / "scripts/render_arch.py"),
                "--product",
                "jiku",
                "--source-commit",
                DUMMY_COMMIT,
                "--source-sha256",
                DUMMY_SHA256,
                "--version",
                "0.0.1",
                "--output",
                str(jiku_out),
            ],
            "render_arch.py --product jiku",
        )
        run_checked(["bash", "-n", str(jiku_out)], "Arch rendered Jiku PKGBUILD syntax check")


def verify_prebuilt_templates(root: Path, config: dict) -> None:
    """Validate prebuilt binary AUR (koma-bin/jiku-bin) and Linuxbrew templates, renderers, and .SRCINFO."""
    bin_placeholders = {"@VERSION@", "@TAG@", "@CDN_ORIGIN@", "@SHA256_X86_64@", "@SHA256_AARCH64@"}
    specs = (
        ("koma", "archlinux", "koma_bin_template", "/usr/share/licenses/koma/LICENSE", "https://komawm.org", "Koma"),
        ("jiku", "archlinux", "jiku_bin_template", "/usr/share/licenses/jiku/LICENSE", "https://komawm.org/jiku/", "Jiku"),
        ("koma", "homebrew", "koma_bin_template", '(share/"licenses/koma").install "LICENSE"', "https://komawm.org", "Koma"),
        ("jiku", "homebrew", "jiku_bin_template", '(share/"licenses/jiku").install "LICENSE"', "https://komawm.org/jiku/", "Jiku"),
    )
    for product, section, key, license_needle, _url, _cls in specs:
        rel = config[section].get(key)
        if not isinstance(rel, str):
            raise SystemExit(f"[{section}] must declare {key}")
        tpl_path = root / rel
        if not tpl_path.is_file():
            raise SystemExit(f"Missing prebuilt template: {rel}")
        text = tpl_path.read_text(encoding="utf-8")
        if "SKIP" in text:
            raise SystemExit(f"Prebuilt template must not use SKIP checksums: {rel}")
        found = set(re.findall(r"@[A-Z0-9_]+@", text))
        if found != bin_placeholders:
            raise SystemExit(f"Prebuilt template {rel} must bind {sorted(bin_placeholders)}, found {sorted(found)}")
        if "GPL-3.0-or-later" not in text or license_needle not in text:
            raise SystemExit(f"Prebuilt template {rel} must preserve GPL-3.0-or-later and install LICENSE")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        for product, formula_class in (("koma", "Koma"), ("jiku", "Jiku")):
            aur_dir = tmp_dir / f"aur-{product}"
            run_checked(
                [
                    sys.executable,
                    str(root / "scripts/render_aur_bin.py"),
                    "--product",
                    product,
                    "--version",
                    "0.0.1",
                    "--tag",
                    "v0.0.1",
                    "--sha256-x86_64",
                    DUMMY_SHA256,
                    "--sha256-aarch64",
                    DUMMY_SHA256,
                    "--out-dir",
                    str(aur_dir),
                ],
                f"render_aur_bin.py --product {product}",
            )
            pkgbuild_path = aur_dir / "PKGBUILD"
            srcinfo_path = aur_dir / ".SRCINFO"
            for rendered_file in (pkgbuild_path, srcinfo_path):
                rendered_text = rendered_file.read_text(encoding="utf-8")
                if re.search(r"@[A-Z0-9_]+@", rendered_text):
                    raise SystemExit(f"Unexpanded placeholder in {rendered_file.name} for {product}-bin")
            run_checked(["bash", "-n", str(pkgbuild_path)], f"Arch {product}-bin PKGBUILD syntax check")
            if shutil.which("makepkg"):
                proc = subprocess.run(
                    ["makepkg", "--printsrcinfo"],
                    cwd=aur_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                expected_lines = [
                    line.rstrip() for line in proc.stdout.splitlines() if line.strip() and not line.lstrip().startswith("#")
                ]
                actual_lines = [
                    line.rstrip()
                    for line in srcinfo_path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                ]
                if actual_lines != expected_lines:
                    raise SystemExit(
                        f"Generated .SRCINFO for {product}-bin does not match makepkg --printsrcinfo:\n"
                        f"Expected: {expected_lines}\nActual:   {actual_lines}"
                    )

            rb_path = tmp_dir / f"{product}.rb"
            run_checked(
                [
                    sys.executable,
                    str(root / "scripts/render_homebrew_bin.py"),
                    "--product",
                    product,
                    "--version",
                    "0.0.1",
                    "--tag",
                    "v0.0.1",
                    "--sha256-x86_64",
                    DUMMY_SHA256,
                    "--sha256-aarch64",
                    DUMMY_SHA256,
                    "--output",
                    str(rb_path),
                ],
                f"render_homebrew_bin.py --product {product}",
            )
            rb_text = rb_path.read_text(encoding="utf-8")
            if re.search(r"@[A-Z0-9_]+@", rb_text):
                raise SystemExit(f"Unexpanded placeholder in {rb_path.name}")
            if f"class {formula_class} < Formula" not in rb_text or "depends_on :linux" not in rb_text:
                raise SystemExit(f"Linuxbrew formula {rb_path.name} missing expected class or depends_on :linux")
            if shutil.which("ruby"):
                run_checked(["ruby", "-c", str(rb_path)], f"Linuxbrew {product}.rb syntax check")

        if shutil.which("ssh-keygen"):
            ssh_dir = tmp_dir / "ssh-key-test"
            ssh_dir.mkdir()
            key_path = ssh_dir / "id_test"
            run_checked(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "verify-test", "-f", str(key_path)],
                "generate throwaway ed25519 key",
            )
            orig_key = key_path.read_text(encoding="utf-8")
            variants = {
                "trailing-space": "\n".join(line + "  " for line in orig_key.splitlines()),
                "space-collapsed": orig_key.replace("\n", " "),
                "literal-newline": orig_key.replace("\n", "\\n"),
                "extra-blanks": "\n\n" + orig_key + "\n\n",
                "base64-wrapped": base64.b64encode(orig_key.encode("utf-8")).decode("ascii"),
            }
            for name, payload in variants.items():
                out_key = ssh_dir / f"out_{name}"
                env = dict(os.environ, TEST_SSH_KEY=payload)
                proc = subprocess.run(
                    [sys.executable, str(root / "scripts/write_ssh_key.py"), "--env", "TEST_SSH_KEY", "--output", str(out_key)],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if proc.returncode != 0:
                    raise SystemExit(f"write_ssh_key.py failed on {name}: {proc.stderr}")
                if stat.S_IMODE(out_key.stat().st_mode) != 0o600:
                    raise SystemExit(f"write_ssh_key.py wrong file mode on {name}")
            pub_payload = (ssh_dir / "id_test.pub").read_text(encoding="utf-8")
            bad_out = ssh_dir / "bad_pub"
            bad_proc = subprocess.run(
                [sys.executable, str(root / "scripts/write_ssh_key.py"), "--env", "TEST_SSH_KEY", "--output", str(bad_out)],
                env=dict(os.environ, TEST_SSH_KEY=pub_payload),
                capture_output=True,
                text=True,
                check=False,
            )
            if bad_proc.returncode == 0 or bad_out.exists() or "PUBLIC key" not in bad_proc.stderr:
                raise SystemExit(f"write_ssh_key.py did not reject .pub cleanly: {bad_proc.stderr}")


def verify_nfpm_specs(root: Path, config: dict) -> None:
    """Validate nFPM v2 YAML specifications for both koma and jiku."""
    nfpm = config.get("nfpm")
    if not isinstance(nfpm, dict):
        raise SystemExit("packaging.toml missing [nfpm] section")
    for product, key in (("koma", "koma_config"), ("jiku", "jiku_config")):
        rel = nfpm.get(key)
        if not isinstance(rel, str):
            raise SystemExit(f"[nfpm] missing {key}")
        spec_path = root / rel
        if not spec_path.is_file():
            raise SystemExit(f"Missing nFPM spec: {rel}")
        scalars, dsts = parse_nfpm_yaml(spec_path.read_text(encoding="utf-8"), Path(rel))
        if scalars.get("name") != product or scalars.get("license") != "GPL-3.0-or-later":
            raise SystemExit(f"nFPM config {rel} must set name={product} and license=GPL-3.0-or-later")
        expected_license_dst = f"/usr/share/licenses/{product}/LICENSE"
        if expected_license_dst not in dsts:
            raise SystemExit(f"nFPM config {rel} must install {expected_license_dst}")


def verify_dependabot(root: Path) -> None:
    """Ensure .github/dependabot.yml uses monthly grouped GitHub Actions updates."""
    dep = root / ".github/dependabot.yml"
    if not dep.is_file():
        raise SystemExit("Missing .github/dependabot.yml")
    text = dep.read_text(encoding="utf-8")
    if "interval: monthly" not in text or "package-ecosystem: github-actions" not in text:
        raise SystemExit(".github/dependabot.yml must configure monthly github-actions updates")


def validate_rendered_distro(distro: str, dest: Path) -> None:
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
    elif distro == "openbsd":
        verify_make_recipes(dest / "Makefile", ("do-build:", "do-install:"))
        plist = (dest / "pkg/PLIST").read_text(encoding="utf-8")
        for binary in ("bin/koma", "bin/komactl", "bin/koma-mcp", "bin/koma-overview", "bin/koma-session"):
            if binary not in plist:
                raise SystemExit(f"openbsd/pkg/PLIST missing {binary}")
    elif distro == "nix":
        flake = (dest / "flake.nix").read_text(encoding="utf-8")
        for system in ("x86_64-linux", "aarch64-linux"):
            if system not in flake:
                raise SystemExit(f"Rendered nix/flake.nix missing {system}")


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


def verify_workflow_universal(workflow_path: Path) -> str:
    """Enforce universal permissions, SHA pinning, secret isolation, and container digest pinning."""
    workflow = workflow_path.read_text(encoding="utf-8")
    if not re.search(r"^permissions:\n  contents: read\n(?!  )", workflow, re.M):
        raise SystemExit(f"{workflow_path.name} top-level permissions must be exactly contents: read")
    if re.search(r"^\s+[a-z-]+: write\b", workflow, re.M):
        raise SystemExit(f"{workflow_path.name} must not grant write permissions to GITHUB_TOKEN")
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
    return workflow


def verify_cd_workflow_common(workflow_path: Path, allowed_secrets: set[str], single_secret_clobber: str | None) -> str:
    """Enforce universal security invariants on every GitHub Actions CD workflow."""
    workflow = verify_workflow_universal(workflow_path)
    on_block = re.search(r"^on:\n(.*?)(?=^\S)", workflow, re.M | re.S)
    triggers = re.findall(r"^  ([a-z_]+):", on_block.group(1), re.M) if on_block else []
    if triggers != ["workflow_dispatch"]:
        raise SystemExit(f"{workflow_path.name} must be workflow_dispatch only, found {triggers}")
    if not re.search(r"publish:\n(?:\s+[a-z].*\n)*?\s+type: boolean\n(?:\s+[a-z].*\n)*?\s+default: false", workflow):
        raise SystemExit(f"{workflow_path.name} publish input must be a boolean defaulting to false")

    publish = job_block(workflow, "publish")
    if "    environment: release\n" not in publish:
        raise SystemExit(f"{workflow_path.name} publish job must run under environment: release")
    if not re.search(r"^    if: .*inputs\.publish", publish, re.M):
        raise SystemExit(f"{workflow_path.name} publish job must be gated on inputs.publish")

    used_secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", workflow))
    unknown_secrets = used_secrets - allowed_secrets
    if unknown_secrets:
        raise SystemExit(f"{workflow_path.name} references undeclared secrets: {sorted(unknown_secrets)}")

    if single_secret_clobber is not None:
        if f"GH_TOKEN: ${{{{ secrets.{single_secret_clobber} }}}}" not in publish or "--clobber" not in publish:
            raise SystemExit(f"{workflow_path.name} publish job must use secrets.{single_secret_clobber} and --clobber")
        if workflow.count("secrets.") != 1:
            raise SystemExit(f"{workflow_path.name} may reference secrets only once in the publish job")
    return workflow


def verify_prerelease_metadata(section: dict, config: dict, root: Path, key: str = "workflow") -> Path:
    """Validate common prerelease metadata keys and return the referenced workflow path."""
    expected = {
        "publish_mode": "workflow_dispatch-with-release-environment",
        "publish_input_default": False,
        "environment": "release",
        "required_secret": "PRODUCT_RELEASE_TOKEN",
    }
    if not isinstance(section, dict) or any(section.get(k) != v for k, v in expected.items()):
        raise SystemExit(f"Prerelease section must declare {expected}")
    workflow_rel = section.get(key)
    if not isinstance(workflow_rel, str):
        raise SystemExit(f"Prerelease section must declare {key}")
    workflow_path = root / workflow_rel
    if not workflow_path.is_file():
        raise SystemExit(f"Publishing is enabled but {workflow_rel} is missing")
    if "products" in section and not set(section["products"]) <= set(config["release"]["targets"]):
        raise SystemExit("Development prerelease products must have a release target")
    return workflow_path


def verify_workflows(root: Path, config: dict) -> None:
    """Validate CI and all CD workflows declared in packaging.toml and present in .github/workflows/."""
    pre = config.get("development_prerelease")
    if not isinstance(pre, dict) or pre.get("channel") != "development" or pre.get("accepted") is not False:
        raise SystemExit("[development_prerelease] must declare channel='development' and accepted=false")
    if not re.fullmatch(r"0\.0\.\d+", pre.get("version", "")):
        raise SystemExit("Development prereleases must use a 0.0.x version")

    env_secrets = set(pre.get("environment_secrets", []))
    if pre.get("required_secret") not in env_secrets:
        raise SystemExit("[development_prerelease] environment_secrets must include required_secret")

    ci_wf_path = root / ".github/workflows/ci.yml"
    if not ci_wf_path.is_file():
        raise SystemExit("Missing .github/workflows/ci.yml")
    ci_wf = verify_workflow_universal(ci_wf_path)
    if "secrets." in ci_wf:
        raise SystemExit("ci.yml must never reference secrets")

    arch_wf_path = verify_prerelease_metadata(pre, config, root, key="workflow")
    arch_wf = verify_cd_workflow_common(arch_wf_path, env_secrets, pre["required_secret"])
    arch_build = job_block(arch_wf, "build")
    image = re.search(r"^\s+image: (\S+)$", arch_build, re.M)
    if not image or not re.fullmatch(r"archlinux:base-devel@sha256:[0-9a-f]{64}", image.group(1)):
        raise SystemExit("Arch build container must be archlinux:base-devel pinned by digest")
    if f"CONTAINER_IMAGE: {image.group(1)}" not in arch_build:
        raise SystemExit("Arch build CONTAINER_IMAGE must match the pinned container image")

    cd_release_wf_path = verify_prerelease_metadata(pre, config, root, key="multi_distro_workflow")
    cd_release_wf = verify_cd_workflow_common(cd_release_wf_path, env_secrets, single_secret_clobber=None)
    cd_arch_build = job_block(cd_release_wf, "build-archlinux")
    if not re.search(r"^\s+image: archlinux:base-devel@sha256:[0-9a-f]{64}$", cd_arch_build, re.M):
        raise SystemExit("cd-release.yml build-archlinux container must be archlinux:base-devel pinned by digest")
    if "https://cdn.komawm.org" not in cd_release_wf or "./node_modules/.bin/wrangler" not in cd_release_wf:
        raise SystemExit("cd-release.yml must upload to https://cdn.komawm.org via ./node_modules/.bin/wrangler")

    matrix = pre.get("matrix")
    if not isinstance(matrix, dict) or matrix.get("channels") != [
        "debian",
        "rpm",
        "alpine",
        "nix",
        "freebsd",
        "openbsd",
        "homebrew",
    ]:
        raise SystemExit("[development_prerelease.matrix] must declare the multi-distro channels")
    matrix_wf_path = verify_prerelease_metadata(matrix, config, root, key="workflow")
    matrix_wf = verify_cd_workflow_common(matrix_wf_path, env_secrets, matrix["required_secret"])
    for container_job, expected_prefix in (("build-rpm", "fedora:"), ("build-alpine", "alpine:")):
        block = job_block(matrix_wf, container_job)
        if f"CONTAINER_IMAGE: {expected_prefix}" not in block or "docker run" not in block:
            raise SystemExit(f"Matrix job {container_job} must run in a digest-pinned {expected_prefix} container")

    declared = {
        ci_wf_path.resolve(),
        arch_wf_path.resolve(),
        cd_release_wf_path.resolve(),
        matrix_wf_path.resolve(),
    }
    on_disk = sorted((root / ".github/workflows").glob("*.yml"))
    for wf_path in on_disk:
        if wf_path.resolve() not in declared:
            raise SystemExit(f"Undeclared workflow in .github/workflows: {wf_path.name}")


if __name__ == "__main__":
    main()
