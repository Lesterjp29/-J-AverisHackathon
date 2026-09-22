"""Regression checks for the repository's relocated data and entry points."""
import os
import subprocess
import sys
from pathlib import Path

from pipeline.loader import Inbox
from pipeline.paths import ROOT, SAMPLE_DATA, STRESS_DATA, DEMO_DATA, SAMPLE_REPORTS


def test_relocated_bundles_and_reports_are_readable():
    inbox = Inbox(str(SAMPLE_DATA))
    assert len(inbox.emails()) == 520
    assert len(inbox.sample_submission()) == 520
    assert inbox.read_bytes("attachments/email_001_SI.txt")
    assert len(Inbox(str(STRESS_DATA)).emails()) == 10
    assert (DEMO_DATA / "2_upload_ok" / "SI.pdf").is_file()
    assert (SAMPLE_REPORTS / "report.json").is_file()


def test_default_loader_works_outside_repository(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(ROOT), "DOCUVERIFY_DISABLE_AI": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.loader"], cwd=tmp_path,
        env=environment, capture_output=True, text=True, check=True,
    )
    assert "520 emails" in result.stdout
