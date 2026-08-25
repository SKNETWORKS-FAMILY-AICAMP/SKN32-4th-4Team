import json
from pathlib import Path

from scripts.eval.run_assess_golden import run_cases


def test_골든20건을_빠짐없이_구조화출력한다():
    path = Path(__file__).parent / "golden/precheck_ai2_v1.yaml"
    golden = json.loads(path.read_text(encoding="utf-8"))
    rows = run_cases(golden)
    assert len(rows) == 20
    assert {row["id"] for row in rows} == {row["id"] for row in golden}
    assert all(row["reason"] for row in rows)
