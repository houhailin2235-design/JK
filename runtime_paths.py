"""Local paths stay unchanged; containers may mount a persistent state directory."""
import os
from pathlib import Path

STATE_ROOT=Path(os.environ.get('RADAR_STATE_DIR') or Path(__file__).resolve().parent)
STATE_ROOT.mkdir(parents=True,exist_ok=True)
