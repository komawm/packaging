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
    config = tomllib.loads((root / "packaging.toml").read_text())
    if any(config.get(key) is not False for key in ("accepted", "remote_changes_enabled", "publish_enabled")):
        raise SystemExit("Initial packaging must remain unaccepted and publishing disabled")
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


if __name__ == "__main__":
    main()
