#!/usr/bin/env python3
"""Local review UI for the shipping-document pipeline.  Standard library only.

    python app.py                       # data in the current folder, results in ./out
    python app.py --source C:\\path\\to\\bundle --out out
    python app.py --source http://localhost:8080      # dataset served by the organisers' docker image

Then open http://localhost:8501 (it opens automatically).

The UI never re-implements any logic: every button calls the same pipeline code that
`python -m pipeline.run` uses, so a decision made here gives the same result as the CLI.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from pipeline import classify as classify_mod            # noqa: E402
from pipeline.classify import BL_COMPARISON              # noqa: E402
from pipeline.env import setup as env_setup              # noqa: E402
from pipeline.extract import FIELDS                      # noqa: E402
from pipeline.readers import read_any                    # noqa: E402
from pipeline.run import build_vocab, process, review_item, to_submission, write_outputs  # noqa: E402

EXPORTS = {
    "report.md": "text/markdown; charset=utf-8",
    "report.json": "application/json; charset=utf-8",
    "review_queue.json": "application/json; charset=utf-8",
    "submission.json": "application/json; charset=utf-8",
    "resolutions.json": "application/json; charset=utf-8",
}


class App:
    def __init__(self, source: str, out: str, ask_send_as: str):
        self.source = source
        self.out = Path(out)
        self.lock = threading.RLock()
        self.progress = {"running": False, "done": 0, "total": 0, "message": "", "error": None}
        self.results: dict[str, dict] = {}
        self.resolutions: dict[str, dict] = {}
        self.emails: dict[str, dict] = {}
        self.inbox = None
        self.vocab = None
        self.env = env_setup()
        self.connect_error: str | None = None
        self.set_label(ask_send_as)
        self.connect()
        self.load_existing()

    # ------------------------------------------------------------ setup
    def set_label(self, label: str):
        self.ask_send_as = label if label in ("GENERAL", "BL_COMPARISON") else "GENERAL"
        classify_mod.ASK_SEND_DRAFT_LABEL = self.ask_send_as

    def connect(self):
        self.connect_error = None
        try:
            if not self.source.startswith("http"):
                sys.path.insert(1, str(Path(self.source).resolve()))
            from loader import Inbox
            self.inbox = Inbox(self.source)
            self.emails = {e["email_id"]: e for e in self.inbox.emails()}
            if not self.emails:
                self.connect_error = self._diagnose()
        except Exception as e:                                # shown in the UI, never a stack trace
            self.connect_error = f"Cannot open data source '{self.source}': {e}"

    def _diagnose(self) -> str:
        """Say exactly where we looked and, if we can find it, where the data actually is."""
        root = Path(self.source).resolve()
        inbox = root / "inbox"

        def has_data(d: Path) -> bool:
            return (d / "inbox").is_dir() and any((d / "inbox").glob("email_*.json"))

        if inbox.is_dir():
            n = len(list(inbox.glob("email_*.json")))
            return f"Found {inbox} but it contains {n} email_*.json files. Copy the bundle's inbox/ files into it."
        msg = f"There is no inbox/ folder inside {root}."
        try:
            near = [root.parent] + [d for d in root.iterdir() if d.is_dir()] + \
                   [d for d in root.parent.iterdir() if d.is_dir() and d != root]
        except OSError:
            near = []
        found = next((d for d in near if has_data(d)), None)
        if found:
            return msg + f" I found your data in {found} - set Data source to that folder."
        if os.environ.get("SDOC_DOCKER"):
            return msg + (" Copy the bundle's inbox/ and attachments/ folders into the 'data' folder next to "
                          "docker-compose.yml, then refresh this page. (Quick check and Scan work without them.)")
        return msg + (" Copy the bundle's inbox/, attachments/ and sample_submission.json into this folder, "
                      "or set Data source to the folder that already has them.")

    def load_existing(self):
        rp, xp = self.out / "report.json", self.out / "resolutions.json"
        if xp.exists():
            self.resolutions = json.loads(xp.read_text(encoding="utf-8"))
        if rp.exists():
            for r in json.loads(rp.read_text(encoding="utf-8")):
                self.results[r["email_id"]] = r

    def get_vocab(self):
        """Known company / port names, used only to de-noise OCR readings. Built once, then cached on disk."""
        if self.vocab is None:
            from pipeline.compare import Vocab
            vp = self.out / "vocab.json"
            try:
                d = json.loads(vp.read_text(encoding="utf-8"))
                if d.get("emails") == len(self.emails):
                    self.vocab = Vocab(d["names"], d["places"])
            except Exception:
                pass
            if self.vocab is None:
                self.vocab = build_vocab(self.inbox)
                try:
                    self.out.mkdir(parents=True, exist_ok=True)
                    vp.write_text(json.dumps({"emails": len(self.emails), "names": self.vocab.names,
                                              "places": self.vocab.places}), encoding="utf-8")
                except OSError:
                    pass
        return self.vocab

    # ------------------------------------------------------------ pipeline
    def ordered(self) -> list[dict]:
        with self.lock:
            return [self.results[i] for i in self.emails if i in self.results]

    def persist(self):
        write_outputs(self.ordered(), self.out)
        (self.out / "resolutions.json").write_text(json.dumps(self.resolutions, indent=1, ensure_ascii=False),
                                                   encoding="utf-8")

    def run_all(self, ask_send_as: str | None):
        with self.lock:
            if self.progress["running"]:
                return
            if ask_send_as:
                self.set_label(ask_send_as)
            self.progress = {"running": True, "done": 0, "total": len(self.emails),
                             "message": "Learning known company and port names...", "error": None}
        threading.Thread(target=self._run_all, daemon=True).start()

    def _run_all(self):
        try:
            vocab = self.get_vocab()
            fresh = {}
            for n, (eid, email) in enumerate(self.emails.items(), 1):
                fresh[eid] = process(email, self.inbox, self.resolutions, vocab)
                self.progress.update(done=n, message=f"Processing {eid}")
            with self.lock:
                self.results = fresh
                self.persist()
            self.progress.update(running=False, message="Done")
        except Exception as e:
            self.progress.update(running=False, error=f"{type(e).__name__}: {e}", message="Failed")
            traceback.print_exc()

    def reprocess(self, eid: str) -> dict:
        r = process(self.emails[eid], self.inbox, self.resolutions, self.get_vocab())
        with self.lock:
            self.results[eid] = r
            self.persist()
        return r

    # ------------------------------------------------------------ human review
    def resolve(self, p: dict) -> dict:
        eid = p.get("email_id")
        if eid not in self.emails:
            raise ValueError("unknown email")
        corr = p.get("corrections") or {}
        corr = {side: {k: str(v).strip() for k, v in (corr.get(side) or {}).items()
                       if k in FIELDS and str(v).strip()} for side in ("si", "bl")}
        res = {"reviewer": (p.get("reviewer") or "reviewer").strip()[:60],
               "note": (p.get("note") or "").strip()[:500],
               "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        decision = p.get("decision")
        if decision in ("OK", "MISMATCH"):
            res["decision"] = decision
            fields = [f for f in (p.get("defect_fields") or []) if f in FIELDS]
            if decision == "MISMATCH" and not fields:
                raise ValueError("Choose at least one mismatched field.")
            res["defect_fields"] = fields if decision == "MISMATCH" else []
        elif corr["si"] or corr["bl"]:
            res["corrections"] = corr
        else:
            raise ValueError("Nothing to save: pick a decision or enter corrected values.")
        with self.lock:
            self.resolutions[eid] = res
        return self.reprocess(eid)

    def unresolve(self, eid: str) -> dict:
        with self.lock:
            self.resolutions.pop(eid, None)
        return self.reprocess(eid)

    # ------------------------------------------------------------ views
    def counts(self) -> dict:
        rs = self.ordered()
        comps = [r for r in rs if r["category"] == BL_COMPARISON]
        cat, status, reasons, defects = {}, {}, {}, {}
        for r in rs:
            cat[r["category"]] = cat.get(r["category"], 0) + 1
        for r in comps:
            status[r["status"]] = status.get(r["status"], 0) + 1
            if r["status"] == "NEEDS_REVIEW":
                reasons[r.get("review_reason")] = reasons.get(r.get("review_reason"), 0) + 1
            for f in r.get("defect_fields", []):
                defects[f] = defects.get(f, 0) + 1
        return {"emails": len(rs), "comparisons": len(comps), "categories": cat, "status": status,
                "review_reasons": reasons, "defect_fields": defects,
                "failed": sum(1 for r in rs if r.get("processing") == "FAILED"),
                "resolved": sum(1 for r in comps if r["email_id"] in self.resolutions and r.get("status") != "NEEDS_REVIEW")}

    def state(self) -> dict:
        return {"source": self.source if self.source.startswith("http") else str(Path(self.source).resolve()),
                "out": str(self.out), "ask_send_as": self.ask_send_as,
                "env": {k: bool(v) for k, v in self.env.items()}, "progress": self.progress,
                "connect_error": self.connect_error, "total_in_source": len(self.emails),
                "has_results": bool(self.results), "counts": self.counts(), "fields": FIELDS,
                "is_http": self.source.startswith("http")}

    def rows(self) -> list[dict]:
        out = []
        for r in self.ordered():
            e = self.emails.get(r["email_id"], {})
            out.append({"email_id": r["email_id"], "subject": r["subject"], "category": r["category"],
                        "confidence": r.get("confidence"), "status": r.get("status"),
                        "review_reason": r.get("review_reason"), "defect_fields": r.get("defect_fields", []),
                        "resolved": r["email_id"] in self.resolutions and r.get("status") != "NEEDS_REVIEW", "processing": r.get("processing"),
                        "n_attachments": len(e.get("attachments", []))})
        return out

    def review(self) -> dict:
        queue, resolved = [], []
        for r in self.ordered():
            if r["category"] != BL_COMPARISON:
                continue
            eid = r["email_id"]
            if r.get("status") == "NEEDS_REVIEW":          # a saved partial fix stays in the queue, pre-filled
                queue.append(self._email_view(r))
            elif eid in self.resolutions:
                resolved.append({**self._email_view(r), "resolution": self.resolutions[eid]})
        return {"queue": queue, "resolved": resolved}

    def _email_view(self, r: dict) -> dict:
        e = self.emails.get(r["email_id"], {})
        item = review_item(r)
        return {**item, "result": r, "body": e.get("body", ""), "attachments": e.get("attachments", []),
                "resolution": self.resolutions.get(r["email_id"])}

    def email(self, eid: str) -> dict:
        e = self.emails[eid]
        r = self.results.get(eid)
        return {"email": {k: v for k, v in e.items()}, "result": r, "resolution": self.resolutions.get(eid)}

    def doc(self, eid: str, i: int) -> dict:
        path = self.emails[eid]["attachments"][i]
        d = read_any(path, self.inbox.read_bytes(path))
        lines = d.lines[:250]
        return {"path": path, "name": Path(path).name, "format": d.fmt, "ocr": d.ocr, "error": d.error,
                "lines": lines, "page_image": path.lower().endswith(".pdf") and not (d.error and "corrupt" in d.error)}

    def page_png(self, eid: str, i: int, n: int, dpi: int = 110) -> bytes | None:
        path = self.emails[eid]["attachments"][i]
        data = self.inbox.read_bytes(path)
        fd, pdf = tempfile.mkstemp(suffix=".pdf")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        prefix = tempfile.mktemp()
        try:
            subprocess.run(["pdftoppm", "-r", str(max(60, min(dpi, 220))), "-f", str(n), "-l", str(n), "-png", "-singlefile", pdf, prefix],
                           check=True, capture_output=True, timeout=60)
            with open(prefix + ".png", "rb") as f:
                return f.read()
        except Exception:
            return None
        finally:
            for p in (pdf, prefix + ".png"):
                if os.path.exists(p):
                    os.unlink(p)

    def mismatches(self) -> list[dict]:
        out = []
        for r in self.ordered():
            if r["category"] == BL_COMPARISON and r.get("status") == "MISMATCH":
                for f in r["fields"]:
                    if f["verdict"] == "mismatch":
                        out.append({"email_id": r["email_id"], "subject": r["subject"], "field": f["field"],
                                    "si": f["si"], "bl": f["bl"], "reason": f["reason"],
                                    "resolved_by": r.get("resolved_by")})
        return out

    def matches(self) -> list[str]:
        return [r["email_id"] for r in self.ordered()
                if r["category"] == BL_COMPARISON and r.get("status") == "OK"]

    def mismatch_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["email_id", "subject", "field", "SI", "BL", "reason", "resolved_by"])
        for r in self.ordered():
            if r["category"] == BL_COMPARISON and r.get("status") == "MISMATCH":
                for f in r["fields"]:
                    if f["verdict"] == "mismatch":
                        w.writerow([r["email_id"], r["subject"], f["field"], f["si"], f["bl"], f["reason"],
                                    r.get("resolved_by", "")])
        return buf.getvalue()


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    app: App

    def log_message(self, fmt, *args):                    # keep the console quiet except for errors
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _err(self, msg: str, code=400):
        self._json({"error": msg}, code)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        a = self.app
        try:
            if u.path in ("/", "/index.html"):
                html = (ROOT / "ui" / "index.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._json(a.state())
            if u.path == "/api/results":
                return self._json(a.rows())
            if u.path == "/api/review":
                return self._json(a.review())
            if u.path == "/api/mismatches":
                return self._json({"rows": a.mismatches(), "ok": a.matches()})
            if u.path == "/api/email":
                return self._json(a.email(q["id"]))
            if u.path == "/api/doc":
                return self._json(a.doc(q["id"], int(q.get("i", 0))))
            if u.path == "/api/page":
                png = a.page_png(q["id"], int(q.get("i", 0)), int(q.get("n", 1)), int(q.get("r", 110)))
                return self._send(200, png, "image/png") if png else self._err("cannot render page", 404)
            if u.path == "/api/export":
                name = q.get("name", "")
                if name == "mismatches.csv":
                    body, ctype = a.mismatch_csv().encode("utf-8-sig"), "text/csv; charset=utf-8"
                elif name in EXPORTS:
                    p = a.out / name
                    if not p.exists():
                        return self._err(f"{name} does not exist yet - run the pipeline first", 404)
                    body, ctype = p.read_bytes(), EXPORTS[name]
                else:
                    return self._err("unknown export", 404)
                return self._send(200, body, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
            return self._err("not found", 404)
        except (KeyError, IndexError):
            self._err("unknown email or attachment", 404)
        except Exception as e:
            traceback.print_exc()
            self._err(f"{type(e).__name__}: {e}", 500)

    def do_POST(self):
        u = urlparse(self.path)
        a = self.app
        try:
            n = int(self.headers.get("Content-Length") or 0)
            p = json.loads(self.rfile.read(n) or b"{}")
            if u.path == "/api/run":
                if a.connect_error:
                    return self._err(a.connect_error)
                a.run_all(p.get("ask_send_as"))
                return self._json({"started": True})
            if u.path == "/api/resolve":
                return self._json({"result": a.resolve(p)})
            if u.path == "/api/unresolve":
                return self._json({"result": a.unresolve(p["email_id"])})
            if u.path == "/api/retry":
                return self._json({"result": a.reprocess(p["email_id"])})
            if u.path == "/api/submit":
                if not a.source.startswith("http"):
                    return self._err("Scoring needs the organisers' server. Start it with docker compose and "
                                     "run: python app.py --source http://localhost:8080")
                sub = {r["email_id"]: to_submission(r) for r in a.ordered()}
                return self._json({"scoreboard": a.inbox.submit(sub)})
            return self._err("not found", 404)
        except ValueError as e:
            self._err(str(e), 400)
        except (KeyError, IndexError):
            self._err("unknown email or missing field", 400)
        except Exception as e:
            traceback.print_exc()
            self._err(f"{type(e).__name__}: {e}", 500)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default=".", help="data folder (with inbox/ and attachments/) or http://localhost:8080")
    ap.add_argument("--out", default="out")
    ap.add_argument("--port", type=int, default=8501)
    ap.add_argument("--ask-send-as", choices=["GENERAL", "BL_COMPARISON"], default="GENERAL")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    Handler.app = App(a.source, a.out, a.ask_send_as)
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    except OSError:
        raise SystemExit(f"Port {a.port} is already in use (is the UI already running in another terminal?). "
                         f"Try: python app.py --port {a.port + 1}")
    url = f"http://localhost:{a.port}"
    print(f"Review UI: {url}   (Ctrl+C to stop)")
    if Handler.app.connect_error:
        print("WARNING:", Handler.app.connect_error)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
