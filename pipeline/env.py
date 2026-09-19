"""Environment preflight: locate poppler / tesseract (esp. on Windows) and fail LOUDLY if missing.

Without this, a missing `pdftotext` would look exactly like a corrupt PDF and every PDF in the inbox
would quietly land in the review queue as 'unreadable'.
Override locations with env vars POPPLER_PATH (folder with pdftotext) and TESSERACT_CMD (full path).
"""
from __future__ import annotations

import glob
import os
import shutil

_WIN_DIRS = [
    r"C:\Program Files\Tesseract-OCR", r"C:\Program Files (x86)\Tesseract-OCR",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR",
    r"C:\poppler*\Library\bin", r"C:\poppler*\bin", r"C:\Program Files\poppler*\Library\bin",
    r"C:\Program Files\poppler*\bin", r"%USERPROFILE%\poppler*\Library\bin", r"%USERPROFILE%\Downloads\poppler*\Library\bin",
    r"%USERPROFILE%\scoop\apps\poppler\current\Library\bin", r"C:\ProgramData\chocolatey\bin",
]


def setup() -> dict[str, str | None]:
    extra = []
    if os.environ.get("POPPLER_PATH"):
        extra.append(os.environ["POPPLER_PATH"])
    if os.name == "nt":
        for pat in _WIN_DIRS:
            extra += glob.glob(os.path.expandvars(pat))
    for d in extra:
        if os.path.isdir(d) and d not in os.environ["PATH"]:
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
                          unzip, then add its Library\\bin folder to PATH
                          (or set POPPLER_PATH=C:\\path\\to\\poppler\\Library\\bin)
            then RESTART VS Code so the terminal picks up PATH.
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
