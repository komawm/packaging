#!/usr/bin/env python3
"""Normalize pasted OpenSSH/PEM private keys from an env var and validate safely."""

from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

HEADER_RE = re.compile(
    r"(-----BEGIN [A-Z0-9 ]+-----)(.*?)(-----END [A-Z0-9 ]+-----)",
    re.S,
)


def normalize_private_key(raw: str) -> tuple[str, str]:
    """Normalize line wrapping/escapes of a pasted private key without inspecting secrets."""
    s = raw.replace("\r", "").replace("\\n", "\n").strip().strip('"').strip("'")
    if "-----BEGIN " not in s and not s.startswith(("ssh-", "ecdsa-")):
        try:
            decoded = base64.b64decode(re.sub(r"\s+", "", s), validate=True).decode("utf-8")
            if "-----BEGIN " in decoded:
                s = decoded.replace("\r", "").strip()
        except Exception:
            pass

    match = HEADER_RE.search(s)
    if match:
        hdr, body, ftr = match.group(1), match.group(2), match.group(3)
        if ":" not in body:
            b64 = re.sub(r"\s+", "", body)
            width = 70 if "OPENSSH" in hdr else 64
            wrapped = "\n".join(b64[i : i + width] for i in range(0, len(b64), width))
            return f"{hdr}\n{wrapped}\n{ftr}\n", s

    lines = [line.strip() for line in s.splitlines() if line.strip()]
    return ("\n".join(lines) + "\n") if lines else "", s


def classify_invalid_key(env_name: str, normalized_src: str, stderr_text: str) -> str:
    """Return a strictly non-secret structural error classification."""
    if normalized_src.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-")):
        return f"{env_name} contains an OpenSSH PUBLIC key (.pub), not a private key"
    if "PGP PRIVATE KEY" in normalized_src:
        return f"{env_name} contains a PGP block instead of an OpenSSH private key"
    if "incorrect passphrase" in stderr_text.lower():
        return f"{env_name} is passphrase-protected; CI requires an unencrypted SSH private key"
    if "-----BEGIN " not in normalized_src or "-----END " not in normalized_src:
        return f"{env_name} is missing BEGIN/END private key headers"
    return f"{env_name} failed ssh-keygen validation ({stderr_text.strip()})"


def write_secure_file(path: Path, content: str) -> None:
    """Write file atomically with strict 0o600 permissions."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        handle.write(content)


def main() -> int:
    parser = argparse.ArgumentParser(description="Write and validate a private SSH key from env")
    parser.add_argument("--env", required=True, help="Environment variable containing private key")
    parser.add_argument("--output", required=True, help="Destination private key file path")
    args = parser.parse_args()

    raw = os.environ.get(args.env, "")
    if not raw.strip():
        print(f"Error: environment variable {args.env} is empty", file=sys.stderr)
        return 1

    normalized_out, inspected = normalize_private_key(raw)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_secure_file(out_path, normalized_out)

    res = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(out_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        out_path.unlink(missing_ok=True)
        print(f"Error: {classify_invalid_key(args.env, inspected, res.stderr)}", file=sys.stderr)
        return 1

    fp = subprocess.run(
        ["ssh-keygen", "-lf", "-"],
        input=res.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    if fp.returncode == 0 and fp.stdout.strip():
        parts = fp.stdout.strip().split()
        summary = f"{parts[0]} {parts[1]} {parts[-1]}" if len(parts) >= 3 else fp.stdout.strip()
        print(f"Validated SSH key: {summary}")
    else:
        print("Validated SSH key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
