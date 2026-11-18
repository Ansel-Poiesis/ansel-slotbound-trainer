"""Package an already-built, verified executable with its notices and SHA-256."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "4.4.1"


def main():
    executable = ROOT / "dist/AnselSlotboundTrainer.exe"
    if not executable.is_file():
        raise SystemExit("Build SlotboundModifier.spec first")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Commit the intended source before packaging")
    out = ROOT / "release"
    out.mkdir(exist_ok=True)
    files = [executable, ROOT / "README.md", ROOT / "CHANGELOG.md", ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md"]
    files += sorted((ROOT / "THIRD_PARTY_LICENSES").glob("*.txt"))
    manifest = {"version": VERSION, "source_commit": commit, "platform": "windows-x64",
                "executable_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
                "game_files_included": False}
    provenance = out / "provenance.json"
    provenance.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    archive = out / ("Ansel-Slotbound-Trainer-" + VERSION + "-windows-x64.zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in files:
            bundle.write(path, path.relative_to(ROOT).as_posix().removeprefix("dist/"))
        bundle.write(provenance, "provenance.json")
    sums = "\n".join(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name
                     for path in (archive, provenance, executable)) + "\n"
    (out / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    print(archive)
    print(sums)


if __name__ == "__main__":
    main()
