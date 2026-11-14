#!/usr/bin/env python3
"""Write verifiable SHA256SUMS-<distro> and PACKAGE-MANIFEST.toml outputs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tomllib


def sha256_file(path: Path) -> str:
    """Return lowercase hexadecimal SHA-256 digest of a regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def toml_str(value: str) -> str:
    """Serialize a plain string safely as a basic TOML string."""
    return json.dumps(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--files", required=True, nargs="+")
    parser.add_argument("--container-image", default="")
    parser.add_argument("--packaging-commit", default="")
    parser.add_argument("--workflow-run", default="")
    args = parser.parse_args()

    out_dir = args.output_dir.resolve()
    sums_lines: list[str] = []
    artifact_entries: list[tuple[str, str]] = []
    for name in args.files:
        path = (out_dir / name).resolve()
        if path.parent != out_dir or not path.is_file():
            raise SystemExit(f"Expected direct artifact file in {out_dir}: {name}")
        digest = sha256_file(path)
        sums_lines.append(f"{digest}  {name}\n")
        artifact_entries.append((name, digest))

    sums_path = out_dir / f"SHA256SUMS-{args.distribution}"
    sums_path.write_text("".join(sums_lines), encoding="utf-8")

    primary_name, primary_sha = artifact_entries[0]
    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "schema_version = 1",
        f"distribution = {toml_str(args.distribution)}",
        'channel = "development"',
        "accepted = false",
        f"product = {toml_str(args.product)}",
        f"repository = {toml_str(f'https://github.com/komawm/{args.product}')}",
        f"tag = {toml_str(args.tag)}",
        f"version = {toml_str(args.version)}",
        f"commit = {toml_str(args.commit)}",
        f"source_archive = {toml_str(f'https://github.com/komawm/{args.product}/archive/{args.commit}.tar.gz')}",
        f"source_sha256 = {toml_str(args.source_sha256)}",
        f"pkg_filename = {toml_str(primary_name)}",
        f"pkg_sha256 = {toml_str(primary_sha)}",
        f"artifact_files = [{', '.join(toml_str(name) for name, _ in artifact_entries)}]",
        f"built_at = {toml_str(built_at)}",
    ]
    if args.container_image:
        lines.append(f"container_image = {toml_str(args.container_image)}")
        if "@" in args.container_image:
            lines.append(f"container_digest = {toml_str(args.container_image.split('@', 1)[1])}")
    if args.packaging_commit:
        lines.append(f"packaging_commit = {toml_str(args.packaging_commit)}")
    if args.workflow_run:
        lines.append(f"workflow_run = {toml_str(args.workflow_run)}")

    lines.append("")
    lines.append("[artifacts_sha256]")
    for name, digest in artifact_entries:
        lines.append(f"{toml_str(name)} = {toml_str(digest)}")

    manifest_text = "\n".join(lines) + "\n"
    tomllib.loads(manifest_text)
    manifest_path = out_dir / "PACKAGE-MANIFEST.toml"
    manifest_path.write_text(manifest_text, encoding="utf-8")
    print(manifest_text)
    print("".join(sums_lines), end="")


if __name__ == "__main__":
    main()
