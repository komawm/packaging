#!/usr/bin/env python3
"""Smoke-test ``scripts/stage_npm.py`` against synthetic release artifacts.

Runs in CI without a Rust toolchain, a network connection, or credentials. It
proves the stager builds the three package trees, wires the platform
`optionalDependencies`, copies the bundled skill, and fails closed on a checksum
mismatch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAGER = ROOT / "scripts" / "stage_npm.py"
VERSION = "0.0.1"
ARCHES = {
    "amd64": "x86_64-unknown-linux-gnu",
    "arm64": "aarch64-unknown-linux-gnu",
}
GPL_HEADER = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
MIT_HEADER = "MIT License\n\nCopyright (c) 2026 Xuepoo\n"


def build_fixture(base: Path) -> tuple[Path, Path]:
    artifacts = base / "artifacts"
    source = base / "source"
    artifacts.mkdir(parents=True)
    (source / "skills" / "jiku-desktop-orchestration").mkdir(parents=True)
    (source / "LICENSE").write_text(GPL_HEADER, encoding="utf-8")
    (source / "README.md").write_text("# fixture\n", encoding="utf-8")
    (source / "skills" / "jiku-desktop-orchestration" / "LICENSE").write_text(
        MIT_HEADER, encoding="utf-8"
    )
    (source / "skills" / "jiku-desktop-orchestration" / "SKILL.md").write_text(
        "---\nname: jiku-desktop-orchestration\n---\n# fixture\n", encoding="utf-8"
    )

    for nfpm_arch, rust_target in ARCHES.items():
        stage = base / f"stage-{nfpm_arch}" / f"jiku-{VERSION}-{rust_target}"
        (stage / "bin").mkdir(parents=True)
        for name in ("jiku", "jikud"):
            binary = stage / "bin" / name
            binary.write_bytes(b"\x7fELF fixture\n")
            binary.chmod(0o755)
        bundle = artifacts / f"jiku-{VERSION}-{rust_target}.tar.gz"
        with tarfile.open(bundle, "w:gz") as archive:
            archive.add(stage, arcname=stage.name)
        digest = subprocess.run(
            ["sha256sum", bundle.name],
            cwd=artifacts,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        # The release build runs `sha256sum ./*`, so checksum entries carry a "./" prefix.
        (artifacts / f"SHA256SUMS-{nfpm_arch}").write_text(
            f"{digest}  ./{bundle.name}\n", encoding="utf-8"
        )
    return artifacts, source


def stage(base: Path, artifacts: Path, source: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(STAGER),
            "--version",
            VERSION,
            "--artifacts",
            str(artifacts),
            "--source-root",
            str(source),
            "--out",
            str(base / "out"),
            "--scope",
            "@koma-wm",
        ],
        capture_output=True,
        text=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        artifacts, source = build_fixture(base)
        result = stage(base, artifacts, source)
        if result.returncode != 0:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
            raise SystemExit("stage_npm.py failed on valid artifacts")

        out = base / "out" / "@koma-wm"
        launcher = json.loads((out / "jiku" / "package.json").read_text())
        x64 = json.loads((out / "jiku-linux-x64" / "package.json").read_text())
        arm64 = json.loads((out / "jiku-linux-arm64" / "package.json").read_text())

        assert launcher["name"] == "@koma-wm/jiku", launcher["name"]
        assert launcher["bin"] == {"jiku": "bin/jiku.js", "jikud": "bin/jikud.js"}
        assert launcher["optionalDependencies"] == {
            "@koma-wm/jiku-linux-x64": VERSION,
            "@koma-wm/jiku-linux-arm64": VERSION,
        }, launcher["optionalDependencies"]
        assert launcher["publishConfig"]["provenance"] is True
        assert (
            out / "jiku" / "skills" / "jiku-desktop-orchestration" / "LICENSE"
        ).is_file()
        assert "skills" in launcher["files"]
        assert (out / "jiku" / "bin" / "jiku.js").stat().st_mode & 0o111

        assert x64["os"] == ["linux"] and x64["cpu"] == ["x64"]
        assert arm64["cpu"] == ["arm64"]
        assert "bin" not in x64, "platform packages must not install a PATH shim"
        for arch in ("jiku-linux-x64", "jiku-linux-arm64"):
            for name in ("jiku", "jikud"):
                binary = out / arch / "bin" / name
                assert binary.is_file(), f"{arch}/bin/{name} missing"
                assert os.access(binary, os.X_OK), (
                    f"{arch}/bin/{name} is not executable"
                )
            assert (out / arch / "LICENSE").read_text().startswith("GNU GENERAL")

        # A tag without the skill catalog still ships a usable launcher.
        shutil.rmtree(source / "skills")
        without_skill = stage(base / "noskill", artifacts, source)
        if without_skill.returncode != 0:
            print(without_skill.stderr, file=sys.stderr)
            raise SystemExit("stage_npm.py rejected a tag without the skill catalog")
        plain = json.loads(
            (
                base / "noskill" / "out" / "@koma-wm" / "jiku" / "package.json"
            ).read_text()
        )
        assert "skills" not in plain["files"], plain["files"]
        assert not (base / "noskill" / "out" / "@koma-wm" / "jiku" / "skills").exists()

        # A present-but-broken skill directory must abort, not ship half a skill.
        (source / "skills" / "jiku-desktop-orchestration").mkdir(parents=True)
        (source / "skills" / "jiku-desktop-orchestration" / "LICENSE").write_text(
            "GPL-3.0-or-later\n", encoding="utf-8"
        )
        broken_skill = stage(base / "badskill", artifacts, source)
        if broken_skill.returncode == 0:
            raise SystemExit("stage_npm.py accepted a non-MIT skill license")
        shutil.rmtree(source / "skills")

        # A skill directory without an entrypoint is a hard failure, not a warning.
        skill_dir = source / "skills" / "jiku-desktop-orchestration"
        skill_dir.mkdir(parents=True)
        (skill_dir / "LICENSE").write_text(MIT_HEADER, encoding="utf-8")
        missing_entry = stage(base / "noentry", artifacts, source)
        if missing_entry.returncode == 0:
            raise SystemExit("stage_npm.py accepted a skill directory without SKILL.md")
        if "SKILL.md is missing" not in missing_entry.stderr:
            raise SystemExit(
                "stage_npm.py failed for the wrong reason: " + missing_entry.stderr.strip()
            )

        # A corrupted archive must abort before any package is written.
        sums = artifacts / "SHA256SUMS-amd64"
        sums.write_text("0" * 64 + "  jiku-0.0.1-x86_64-unknown-linux-gnu.tar.gz\n")
        broken = stage(base, artifacts, source)
        if broken.returncode == 0:
            raise SystemExit("stage_npm.py accepted a checksum mismatch")

    print(
        "npm staging smoke test passed "
        "(3 packages, optional skill, checksum failure aborts)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
