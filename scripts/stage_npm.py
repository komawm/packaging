#!/usr/bin/env python3
"""Stage the @koma-wm/jiku npm packages from verified release artifacts.

Layout produced under ``--out``::

    <scope>/jiku/                    launcher: bin shims + bundled agent skill
    <scope>/jiku-linux-x64/          real binaries for x86_64 (glibc)
    <scope>/jiku-linux-arm64/        real binaries for aarch64 (glibc)

Package metadata is generated here, never committed, so the version always comes
from the release tag and the binaries always come from the artifacts that the
build jobs already produced and checksummed.

The script performs no publishing. It exits non-zero on any checksum mismatch,
missing artifact, or missing license/skill file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

ARCHES = {
    "amd64": {"cpu": "x64", "rust_target": "x86_64-unknown-linux-gnu"},
    "arm64": {"cpu": "arm64", "rust_target": "aarch64-unknown-linux-gnu"},
}
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
BINARIES = ("jiku", "jikud")


def fail(message: str) -> None:
    print(f"::error::{message}", file=sys.stderr)
    raise SystemExit(1)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sums(path: Path) -> dict[str, str]:
    sums: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            fail(f"malformed checksum line in {path}: {line!r}")
        digest, name = parts
        # `sha256sum ./*` writes "./name", and `-b` writes "*name".
        sums[name.lstrip("*").rsplit("/", 1)[-1]] = digest
    return sums


def find_one(pattern: str, root: Path) -> Path:
    matches = sorted(root.rglob(pattern))
    if not matches:
        fail(f"missing artifact matching {pattern} under {root}")
    if len(matches) > 1:
        fail(f"ambiguous artifacts matching {pattern}: {[str(m) for m in matches]}")
    return matches[0]


def extract_binaries(bundle: Path, checksums: dict[str, str], dest: Path) -> None:
    expected = checksums.get(bundle.name)
    if expected is None:
        fail(f"{bundle.name} is not listed in the checksum file")
    actual = sha256_file(bundle)
    if actual != expected:
        fail(f"{bundle.name} checksum mismatch: expected {expected}, got {actual}")

    dest.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    with tarfile.open(bundle, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            name = Path(member.name).name
            if name not in BINARIES or Path(member.name).parent.name != "bin":
                continue
            source = archive.extractfile(member)
            if source is None:
                fail(f"cannot read {member.name} from {bundle.name}")
            target = dest / name
            target.write_bytes(source.read())
            target.chmod(0o755)
            written.add(name)
    missing = sorted(set(BINARIES) - written)
    if missing:
        fail(f"{bundle.name} is missing binaries: {missing}")


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def stage_platform(
    arch: str,
    version: str,
    scope: str,
    artifacts: Path,
    out: Path,
    repository: dict,
    license_path: Path,
    readme_path: Path,
) -> Path:
    spec = ARCHES[arch]
    sums_path = find_one(f"SHA256SUMS-{arch}", artifacts)
    checksums = parse_sums(sums_path)
    bundle = find_one(f"jiku-{version}-{spec['rust_target']}.tar.gz", artifacts)

    package_dir = out / f"{scope}/jiku-linux-{spec['cpu']}"
    binary_dir = package_dir / "bin"
    extract_binaries(bundle, checksums, binary_dir)
    shutil.copy2(license_path, package_dir / "LICENSE")
    shutil.copy2(readme_path, package_dir / "README.md")

    write_json(
        package_dir / "package.json",
        {
            "name": f"{scope}/jiku-linux-{spec['cpu']}",
            "version": version,
            "description": (
                f"jiku and jikud binaries for Koma desktop orchestration "
                f"(linux-{spec['cpu']}, glibc)."
            ),
            "license": "GPL-3.0-or-later",
            "os": ["linux"],
            "cpu": [spec["cpu"]],
            "libc": ["glibc"],
            "files": ["bin/jiku", "bin/jikud", "LICENSE", "README.md"],
            "engines": {"node": ">=18.17"},
            "repository": repository,
            "homepage": "https://github.com/komawm/jiku",
            "bugs": {"url": "https://github.com/komawm/jiku/issues"},
        },
    )
    return package_dir


def stage_launcher(
    version: str,
    scope: str,
    out: Path,
    repository: dict,
    template_dir: Path,
    source_root: Path,
) -> Path:
    package_dir = out / f"{scope}/jiku"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "bin").mkdir(exist_ok=True)

    for shim in ("jiku.js", "jikud.js"):
        shutil.copy2(template_dir / "bin" / shim, package_dir / "bin" / shim)
        (package_dir / "bin" / shim).chmod(0o755)
    shutil.copy2(template_dir / "launch.js", package_dir / "launch.js")
    shutil.copy2(template_dir / "README.md", package_dir / "README.md")
    shutil.copy2(source_root / "LICENSE", package_dir / "LICENSE")

    # The bundled skill is optional: tags cut before the skill catalog existed
    # (for example v0.0.1) still ship a usable CLI. A present-but-broken skill
    # directory is an error, because half a skill is worse than none.
    skills_source = source_root / "skills"
    skill_dir = skills_source / "jiku-desktop-orchestration"
    bundle_skill = skill_dir.is_dir()
    files = ["bin", "launch.js", "LICENSE", "README.md"]
    if bundle_skill:
        skill_license = skill_dir / "LICENSE"
        if not skill_license.is_file() or not skill_license.read_text(
            encoding="utf-8"
        ).startswith("MIT License"):
            fail("skills/jiku-desktop-orchestration/LICENSE must ship MIT text")
        shutil.copytree(skills_source, package_dir / "skills", dirs_exist_ok=True)
        files.insert(2, "skills")
    else:
        print(
            f"::warning::{skill_dir} is absent at this revision; "
            "the launcher will not bundle an agent skill",
            file=sys.stderr,
        )

    write_json(
        package_dir / "package.json",
        {
            "name": f"{scope}/jiku",
            "version": version,
            "description": (
                "Desktop orchestration daemon (jikud) and CLI/MCP controller "
                "(jiku) for the Koma Wayland compositor."
            ),
            "license": "GPL-3.0-or-later",
            "bin": {"jiku": "bin/jiku.js", "jikud": "bin/jikud.js"},
            "files": files,
            "optionalDependencies": {
                f"{scope}/jiku-linux-x64": version,
                f"{scope}/jiku-linux-arm64": version,
            },
            "engines": {"node": ">=18.17"},
            "repository": repository,
            "homepage": "https://github.com/komawm/jiku",
            "bugs": {"url": "https://github.com/komawm/jiku/issues"},
            "keywords": ["koma", "jiku", "wayland", "desktop", "mcp", "agent"],
            "publishConfig": {
                "access": "public",
                "provenance": True,
            },
        },
    )
    return package_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--scope", default="@koma-wm")
    parser.add_argument(
        "--repository-url", default="https://github.com/komawm/packaging.git"
    )
    parser.add_argument("--repository-directory", default="npm/jiku")
    parser.add_argument(
        "--template-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "npm" / "jiku",
    )
    args = parser.parse_args()

    if not SEMVER.match(args.version):
        fail(f"invalid semver version: {args.version}")

    repository = {
        "type": "git",
        "url": args.repository_url,
        "directory": args.repository_directory,
    }
    license_path = args.source_root / "LICENSE"
    if not license_path.is_file():
        fail(f"missing upstream LICENSE at {license_path}")

    args.out.mkdir(parents=True, exist_ok=True)
    order = [
        stage_platform(
            arch,
            args.version,
            args.scope,
            args.artifacts,
            args.out,
            repository,
            license_path,
            args.source_root / "README.md",
        )
        for arch in ARCHES
    ]
    order.append(
        stage_launcher(
            args.version,
            args.scope,
            args.out,
            repository,
            args.template_dir,
            args.source_root,
        )
    )

    print(
        json.dumps(
            {
                "version": args.version,
                "publish_order": [str(path) for path in order],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
