"""Offline synthetic adapter for the subprocess integration test only."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'src')]
from tests.test_pipeline import Worker

request = json.load(sys.stdin)
json.dump(Worker()([], request), sys.stdout)
