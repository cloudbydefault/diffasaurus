from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    release_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "release").resolve()
    artifacts = sorted(
        (
            path
            for path in release_dir.iterdir()
            if path.is_file()
            and path.name != "SHA256SUMS.txt"
            and path.suffix.lower() in {".dmg", ".zip"}
        ),
        key=lambda path: path.name.lower(),
    )
    if not artifacts:
        raise SystemExit(f"No release artifacts found in {release_dir}")
    lines = [f"{sha256(path)}  {path.name}" for path in artifacts]
    destination = release_dir / "SHA256SUMS.txt"
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
