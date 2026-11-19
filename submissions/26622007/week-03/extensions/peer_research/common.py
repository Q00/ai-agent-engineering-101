"""Paths and shared course utilities; no credentials are loaded on import."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PEER = ROOT.parent / "peer_dag"
BASE = ROOT.parents[1]
for directory in (PEER, BASE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from core import Limits, Outcome, Runtime, Task, decode, fingerprint, parse_artifact
from models import PHASES, PROTOCOL
from contract_net import SKILLS
from openrouter_client import CallError, ConfigurationError, ENDPOINT, read_key, redact
