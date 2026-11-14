#!/usr/bin/env python3
"""Verify built channel artifacts and stage release upload assets cleanly."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import tomllib

from write_manifest import sha256_file


def verify_checksums_file(sums_path: Path) -> dict[str, str]:
    """Parse and verify a SHA256SUMS-<distro> file inside its directory."""
    expected: dict[str, str] = {}
    for line_no, raw_line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line:
            continue
        parts = raw_line.split("  ", 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise SystemExit(f"Invalid checksum line {line_no} in {sums_path}")
        digest, name = parts
        target = (sums_path.parent / name).resolve()
        if target.parent != sums_path.parent or not target.is_file():
            raise SystemExit(f"Checksum target missing in {sums_path.parent}: {name}")
        actual = sha256_file(target)
        if actual != digest:
            raise SystemExit(f"Checksum mismatch for {name}: expected {digest}, got {actual}")
        expected[name] = digest
    if not expected:
        raise SystemExit(f"Checksum file is empty: {sums_path}")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", required=True, type=Path)
    parser.add_argument("--stage-dir", required=True, type=Path)
    parser.add_argument("--product", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()

    dist_dir = args.dist_dir.resolve()
    stage_dir = args.stage_dir.resolve()
    stage_dir.mkdir(parents=True, exist_ok=True)

    artifact_dirs = sorted(path for path in dist_dir.iterdir() if path.is_dir())
    if not artifact_dirs:
        raise SystemExit(f"No channel artifact directories found in {dist_dir}")

    staged_files: list[str] = []
    for channel_dir in artifact_dirs:
        sums_files = list(channel_dir.glob("SHA256SUMS-*"))
        if len(sums_files) != 1:
            raise SystemExit(f"Expected exactly one SHA256SUMS-* in {channel_dir}, found {len(sums_files)}")
        sums_path = sums_files[0]
        verified_sums = verify_checksums_file(sums_path)

        manifest_path = channel_dir / "PACKAGE-MANIFEST.toml"
        if not manifest_path.is_file():
            raise SystemExit(f"Missing PACKAGE-MANIFEST.toml in {channel_dir}")
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("accepted") is not False or manifest.get("channel") != "development":
            raise SystemExit(f"Unexpected manifest acceptance state in {channel_dir}")
        if manifest.get("product") != args.product or manifest.get("tag") != args.tag or manifest.get("commit") != args.commit:
            raise SystemExit(f"Manifest identity mismatch in {channel_dir}")

        distro = manifest.get("distribution")
        if not isinstance(distro, str) or not distro:
            raise SystemExit(f"Missing distribution name in {manifest_path}")
        if sums_path.name != f"SHA256SUMS-{distro}":
            raise SystemExit(f"Checksum filename {sums_path.name} does not match distribution {distro}")

        artifact_files = manifest.get("artifact_files")
        if not isinstance(artifact_files, list) or set(artifact_files) != set(verified_sums):
            raise SystemExit(f"Manifest artifact_files do not match {sums_path.name} in {channel_dir}")

        for filename in artifact_files:
            src = (channel_dir / filename).resolve()
            if src.parent != channel_dir or not src.is_file():
                raise SystemExit(f"Invalid artifact path {filename} in {channel_dir}")
            dest = stage_dir / filename
            shutil.copy2(src, dest)
            staged_files.append(filename)

        staged_sums = stage_dir / sums_path.name
        shutil.copy2(sums_path, staged_sums)
        staged_files.append(sums_path.name)

        staged_manifest = stage_dir / f"PACKAGE-MANIFEST-{distro}.toml"
        shutil.copy2(manifest_path, staged_manifest)
        staged_files.append(staged_manifest.name)

    for item in sorted(staged_files):
        print(item)


if __name__ == "__main__":
    main()
