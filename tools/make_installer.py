"""Build the one-file installer: .github/workflows/rrdash.yml (and a copy, RRDASH_INSTALL.txt).

    python tools/make_installer.py

The workflow template (tools/rrdash.template.yml) gets every other project file embedded
at the bottom as a compressed, base64-encoded bundle. Pasting that single file into a new
GitHub repository and running its "install" task recreates the whole project there.
(GitHub doesn't let a workflow write other workflow files, which is why everything the
project runs lives in this one workflow.)
"""
from __future__ import annotations

import base64
import gzip
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".github", "__pycache__", ".pytest_cache", "data", "demo_data", "_site", "dist", ".venv", "venv"}
SKIP_FILES = {".DS_Store"}
INDENT = " " * 10  # the run: block's indentation in the template


def project_files() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_dir() or any(part in SKIP_DIRS for part in rel.parts) or p.name in SKIP_FILES or p.suffix == ".pyc":
            continue
        out.append(rel)
    return out


def bundle() -> tuple[str, int]:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for rel in project_files():
            data = (ROOT / rel).read_bytes()
            info = tarfile.TarInfo(rel.as_posix())
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            tar.addfile(info, io.BytesIO(data))
    gz = gzip.compress(raw.getvalue(), compresslevel=9, mtime=0)
    b64 = base64.encodebytes(gz).decode("ascii")  # 76-character lines
    return b64, len(project_files())


def main() -> None:
    template = (ROOT / "tools" / "rrdash.template.yml").read_text(encoding="utf-8")
    b64, n = bundle()
    body = "\n".join(INDENT + line for line in b64.strip().splitlines())
    text = template.replace("__BUNDLE__", body)
    out = ROOT / ".github" / "workflows" / "rrdash.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "RRDASH_INSTALL.txt").write_text(text, encoding="utf-8")
    print(f"Embedded {n} files; installer is {len(text.encode()) / 1024:.0f} KB "
          f"(GitHub's limit for a workflow file is 500 KB).")


if __name__ == "__main__":
    main()
