"""Repository paths shared by the dashboard, CLI, and optional API."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA = ROOT / "data" / "sample"
STRESS_DATA = ROOT / "data" / "stress"
DEMO_DATA = ROOT / "data" / "demo"
OUTPUTS = ROOT / "outputs"
DEFAULT_OUTPUT = OUTPUTS / "sample"
SAMPLE_REPORTS = ROOT / "examples" / "reports" / "sample"
RESOLUTIONS_FILE = OUTPUTS / "resolutions.json"
