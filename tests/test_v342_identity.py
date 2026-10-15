from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from v342_identity_selftest import run


def test_identity_and_legal_regressions():
    run()
