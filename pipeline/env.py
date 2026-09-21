"""Environment preflight: locate poppler / tesseract (esp. on Windows) and fail LOUDLY if missing.

Without this, a missing `pdftotext` would look exactly like a corrupt PDF and every PDF in the inbox
would quietly land in the review queue as 'unreadable'.

Where poppler is looked for, in order:
  1. env var POPPLER_PATH
  2. a one-line text file `poppler_path.txt` (project folder or current folder) - written by the Streamlit sidebar
  3. common install folders, and anywhere within 3 levels of your Downloads / Documents / Desktop / home
     (e.g. Downloads\\Release-26.09.0-0\\poppler-26.09.0\\Library\\bin)
Override tesseract with TESSERACT_CMD.
"""
from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

_FIXED_WIN_DIRS = [
    r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR",
    r"C:\poppler*\Library\bin", r"C:\poppler*\*\Library\bin", r"C:\poppler*\bin",
    r"C:\Program Files\poppler*\Library\bin", r"C:\Program Files\poppler*\bin",
    r"%USERPROFILE%\scoop\apps\poppler\current\Library\bin", r"C:\ProgramData\chocolatey\bin",
]
# relative to the user's home; '/' is the separator here and is converted for the OS
_HOME_PATTERNS = [
    "Downloads/*/*/Library/bin", "Downloads/*/Library/bin", "Downloads/*/*/bin", "Downloads/*/bin",
    "Downloads/poppler*/Library/bin", "Downloads/poppler*/bin",
    "OneDrive/Downloads/*/*/Library/bin", "OneDrive/Downloads/*/Library/bin",
    "Documents/*/*/Library/bin", "Documents/*/Library/bin", "Desktop/*/*/Library/bin", "Desktop/*/Library/bin",
    "poppler*/Library/bin", "poppler*/bin", "*/poppler*/Library/bin",
]
_EXE = ("pdftotext.exe", "pdftotext")


def _has_poppler(d: str) -> bool:
    return any(os.path.exists(os.path.join(d, n)) for n in _EXE)


def find_poppler_dirs(home: str) -> list[str]:
    """Folders under `home` (bounded depth, so a huge node_modules is never crawled) that hold pdftotext."""
    found: list[str] = []
    for pat in _HOME_PATTERNS:
        for d in glob.glob(os.path.join(home, *pat.split("/"))):
            if os.path.isdir(d) and _has_poppler(d) and d not in found:
                found.append(d)
    return found


def _saved_path() -> str | None:
    for base in (Path(__file__).resolve().parent.parent, Path.cwd()):
        f = base / "poppler_path.txt"
        if f.is_file():
            t = f.read_text(encoding="utf-8").strip().strip('"')
            if t:
                return t
    return None


def save_poppler_path(folder: str) -> str | None:
    """Validate and remember a poppler folder. Returns an error message, or None on success."""
    folder = folder.strip().strip('"')
    if not folder or not os.path.isdir(folder):
        return "That folder does not exist."
    if not _has_poppler(folder):
        hits = [d for d in glob.glob(os.path.join(folder, "**", "pdftotext*"), recursive=True)][:1]
        if hits:
            return f"pdftotext is not directly in that folder. Try: {os.path.dirname(hits[0])}"
        return "pdftotext(.exe) is not in that folder. Point at the folder that contains pdftotext.exe (usually ...\\Library\\bin)."
    (Path(__file__).resolve().parent.parent / "poppler_path.txt").write_text(folder, encoding="utf-8")
    os.environ["POPPLER_PATH"] = folder
    return None


def setup() -> dict[str, str | None]:
    extra: list[str] = []
    if os.environ.get("POPPLER_PATH"):
        extra.append(os.environ["POPPLER_PATH"])
    if _saved_path():
        extra.append(_saved_path())
    if os.name == "nt":
        for pat in _FIXED_WIN_DIRS:
            extra += glob.glob(os.path.expandvars(pat))
    if not shutil.which("pdftotext"):
        extra += find_poppler_dirs(os.path.expanduser("~"))
    for d in extra:
        if d and os.path.isdir(d) and d not in os.environ["PATH"].split(os.pathsep):
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]
    found = {"pdftotext": shutil.which("pdftotext"), "pdftoppm": shutil.which("pdftoppm"),
             "tesseract": os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")}
    try:
        import pytesseract
        if found["tesseract"]:
            pytesseract.pytesseract.tesseract_cmd = found["tesseract"]
    except ImportError:
        pass
    return found


INSTALL_HELP = """\
Missing system tools: {missing}

  Windows : Tesseract  -> https://github.com/UB-Mannheim/tesseract/wiki  (tick 'add to PATH')
            poppler    -> https://github.com/oschwartz10612/poppler-windows/releases
                          unzip it anywhere under Downloads/Documents/Desktop and it is found automatically,
                          or set POPPLER_PATH=C:\\path\\to\\poppler\\Library\\bin, or use the Streamlit sidebar.
  Ubuntu/WSL/Colab : sudo apt-get install -y poppler-utils tesseract-ocr
  macOS   : brew install poppler tesseract
"""


def preflight(allow_missing: bool = False) -> None:
    found = setup()
    missing = [k for k in ("pdftotext", "pdftoppm", "tesseract") if not found[k]]
    if not missing:
        return
    print(INSTALL_HELP.format(missing=", ".join(missing)))
    needs_poppler = any(m in ("pdftotext", "pdftoppm") for m in missing)
    if needs_poppler and not allow_missing:
        raise SystemExit("Stopping: 28 PDF attachments cannot be read without poppler. "
                         "(Re-run with --allow-missing-tools to route them to review instead.)")
    print("WARNING: continuing; affected attachments will be reported as unreadable.\n")


if __name__ == "__main__":
    f = setup()
    for k, v in f.items():
        print(f"{k:10s} {v or 'NOT FOUND'}")
