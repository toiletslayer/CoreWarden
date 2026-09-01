"""Create a minimal deterministic ZIP from the PyInstaller onedir bundle."""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path, PurePosixPath

RELEASE_NAME = "CoreWarden-Windows-x64.zip"
ARCHIVE_ROOT = PurePosixPath("CoreWarden")
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
EXCLUDED_DIRECTORIES = {"__pycache__", ".pytest_cache", ".ruff_cache", "htmlcov"}
EXCLUDED_NAMES = {".coverage", ".env"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".pem", ".key"}


def _excluded(relative: Path) -> bool:
    names = {part.lower() for part in relative.parts}
    filename = relative.name.lower()
    return (
        bool(names & EXCLUDED_DIRECTORIES)
        or filename in EXCLUDED_NAMES
        or filename.startswith(".env.")
        or relative.suffix.lower() in EXCLUDED_SUFFIXES
        or "evidence" in filename
    )


def _write_entry(archive: zipfile.ZipFile, name: PurePosixPath, data: bytes) -> None:
    info = zipfile.ZipInfo(str(name), ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_release(
    bundle: Path,
    output: Path,
    quickstart: Path,
    license_path: Path,
    notices_path: Path,
) -> None:
    """Package the runnable bundle plus public redistribution documents."""
    executable = bundle / "CoreWarden.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"Expected packaged executable was not found: {executable}")
    documents = (quickstart, license_path, notices_path)
    missing_documents = [str(path) for path in documents if not path.is_file()]
    if missing_documents:
        raise FileNotFoundError(
            "Required release document was not found: " + ", ".join(missing_documents)
        )

    files = [path for path in bundle.rglob("*") if path.is_file()]
    included = [(path, path.relative_to(bundle)) for path in files]
    included = [(path, relative) for path, relative in included if not _excluded(relative)]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    with zipfile.ZipFile(output, "w") as archive:
        for path, relative in sorted(included, key=lambda item: item[1].as_posix().lower()):
            _write_entry(
                archive, ARCHIVE_ROOT / PurePosixPath(relative.as_posix()), path.read_bytes()
            )
        for document in documents:
            _write_entry(archive, ARCHIVE_ROOT / document.name, document.read_bytes())


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, default=project_root / "dist" / "CoreWarden")
    parser.add_argument("--output", type=Path, default=project_root / "release" / RELEASE_NAME)
    parser.add_argument("--quickstart", type=Path, default=project_root / "JUDGE-QUICKSTART.txt")
    parser.add_argument(
        "--license", dest="license_path", type=Path, default=project_root / "LICENSE"
    )
    parser.add_argument(
        "--notices",
        dest="notices_path",
        type=Path,
        default=project_root / "THIRD-PARTY-NOTICES.md",
    )
    args = parser.parse_args()
    build_release(
        args.bundle,
        args.output,
        args.quickstart,
        args.license_path,
        args.notices_path,
    )


if __name__ == "__main__":
    main()
