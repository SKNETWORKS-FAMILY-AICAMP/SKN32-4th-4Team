from __future__ import annotations

import copy
import io
import json
import pathlib
import shutil
from types import SimpleNamespace

import pytest

from scripts.eval import audit_brief as audit

#: ★★**여기 있던 `PYTEST_DEBUG_TEMPROOT` 우회를 걷어냈다(2026-08-04).**
#:
#:   원래 이유: "시스템 pytest 임시 루트가 이전 세션 소유라 열거 자체가 거부된다."
#:   그건 `.pytest_tmp_probe_*` 잠김 사고 때의 이야기였고, **지금은 아니다** —
#:   실측: `%TEMP%/pytest-of-playdata2` 열거 정상(22개 항목).
#:
#:   그런데 그 우회에 두 가지 문제가 있었다.
#:
#:     ① **전역이다.** import 시점에 `os.environ` 을 건드려 이 파일뿐 아니라
#:        **스위트 전체의 `tmp_path`** 를 저장소 안으로 돌렸다.
#:     ② **동시 실행이 서로를 지웠다.** 세션 종료 fixture 가 그 루트를
#:        통째로 `rmtree` 하는데, 루트가 두 pytest 프로세스에 **공유**된다.
#:        먼저 끝난 쪽이 지우면 남은 쪽의 `pytest-N` 이 사라진다.
#:
#:   실측 2026-08-04 — 병행 트랙과 동시에 돌리자 `FileNotFoundError [WinError 3]`
#:   로 **11건이 setup 단계에서** 죽었다. 그중 대부분은 이 파일과 무관한 시험이다
#:   (`test_migrate_*`·`test_readiness_*`·`test_prepare_*`).
#:
#:   ★고장 난 환경을 코드로 우회하면, 환경이 나은 뒤에도 그 우회가 남아 새 고장이 된다.
#:   기본 `tmp_path`(프로세스마다 고유한 `pytest-N`)를 그대로 쓴다.


def _task(n: int) -> dict:
    ranks = [1] * n
    return {
        "n": n,
        "mrr@10": 1.0,
        "recall@1": 1.0,
        "recall@5": 1.0,
        "recall@10": 1.0,
        "ranks": ranks,
    }


def _row(model: str, dtype: str) -> dict:
    return {
        "model": model,
        "dtype": dtype,
        "blind_eps": 1e-9,
        "proviso_blind_count": 0,
        "proviso_probes": 60,
        "probe_norm_min": 1.0,
        "probe_norm_max": 1.0,
        "probes_dtype_matches_original": True,
        "probes_gpu_matches_original": True,
        "title": _task(145),
        "tail": _task(60),
        "exclusion": _task(16),
        "mrr@10": 1.0,
        "recall@1": 1.0,
        "recall@5": 1.0,
        "recall@10": 1.0,
        "truncated_ratio": 0.0,
    }


def _valid_rows() -> list[dict]:
    return (
        [_row(f"float-model-{i}", "float16") for i in range(17)]
        + [_row(f"4bit-model-{i}", "4bit") for i in range(4)]
    )


def _run_results(rows: list[dict]) -> audit.Report:
    report = audit.Report()
    audit.check_results(report, copy.deepcopy(rows))
    return report


def _messages(report: audit.Report, kind: str) -> list[str]:
    return [message for message in report.fail if message.startswith(f"[{kind}]")]


def test_result_schema_accepts_complete_rows_and_rejects_missing_task_and_ranks() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail

    del rows[0]["title"]
    report = _run_results(rows)
    assert any("title" in message for message in _messages(report, "결과스키마"))
    assert any("title.ranks" in message for message in _messages(report, "결과스키마"))


def test_existing_task_rejects_ranks_only_missing() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    del rows[0]["tail"]["ranks"]
    assert any("tail" in message and "ranks" in message
               for message in _messages(_run_results(rows), "결과스키마"))


def test_qwen_4bit_is_not_a_schema_exception() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[-1]["model"] = "Qwen/Qwen3-Embedding-4B"
    for task in ("title", "tail", "exclusion"):
        del rows[-1][task]
    failures = _messages(_run_results(rows), "결과스키마")
    for task in ("title", "tail", "exclusion"):
        assert any(task in message for message in failures)
        assert any(f"{task}.ranks" in message for message in failures)


def test_broken_json_is_reported_without_stopping_later_files(tmp_path, monkeypatch) -> None:
    out = tmp_path / "results"
    out.mkdir()
    for i, row in enumerate(_valid_rows()):
        (out / f"{i:02}.json").write_text(json.dumps(row), encoding="utf-8")
    monkeypatch.setattr(audit, "_OUT", out)
    normal = audit.Report()
    rows = audit._load_results(normal)
    audit.check_results(normal, rows)
    assert not normal.fail

    (out / "broken.json").write_text("{not-json", encoding="utf-8")
    mutant = audit.Report()
    loaded = audit._load_results(mutant)
    audit.check_results(mutant, loaded)
    assert len(loaded) == 21
    assert any("broken.json" in message for message in _messages(mutant, "결과JSON"))
    assert any(message.startswith("결과 21건") for message in mutant.ok)


def test_blind_eps_must_equal_one_e_minus_nine() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[0]["blind_eps"] = 1e-6
    assert _messages(_run_results(rows), "blind_eps")

    rows = _valid_rows()
    rows[0]["blind_eps"] = None
    assert _messages(_run_results(rows), "blind_eps")


def test_top_level_title_metrics_must_match_nested_title() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[0]["title"]["mrr@10"] = 0.5
    assert _messages(_run_results(rows), "지표정합")


def test_model_dtype_pair_must_be_unique() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[1]["model"] = rows[0]["model"]
    assert _messages(_run_results(rows), "중복")


@pytest.mark.parametrize(("task", "expected"), [("title", 145), ("tail", 60), ("exclusion", 16)])
def test_each_task_requires_the_fixed_sample_size(task: str, expected: int) -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[0][task] = _task(expected - 1)
    assert _messages(_run_results(rows), "표본수")


def test_metrics_must_be_between_zero_and_one() -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[0]["tail"]["recall@5"] = 1.01
    assert _messages(_run_results(rows), "지표범위")


@pytest.mark.parametrize("metric", ["mrr@10", "recall@10"])
def test_saved_mrr_and_hit_at_ten_must_match_ranks(metric: str) -> None:
    rows = _valid_rows()
    assert not _run_results(rows).fail
    rows[0]["exclusion"][metric] = 0.5
    assert _messages(_run_results(rows), "지표재계산")


def _freeze_fixture(tmp_path: pathlib.Path, monkeypatch) -> tuple[dict[str, pathlib.Path], pathlib.Path]:
    doc = tmp_path / "brief.md"
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    (result_dir / "one.json").write_text("{}", encoding="utf-8")
    paths = {
        "doc": doc,
        "audit_brief.py": tmp_path / "audit_brief.py",
        "bench_embedders.py": tmp_path / "bench_embedders.py",
        "merge_probe_remeasure.py": tmp_path / "merge_probe_remeasure.py",
        "build_retrieval_set.py": tmp_path / "build_retrieval_set.py",
        "embed_bench.json": tmp_path / "embed_bench.json",
        "remote_bench.sh": tmp_path / "remote_bench.sh",
    }
    for name, path in paths.items():
        path.write_bytes(f"bytes:{name}".encode())
    lock = tmp_path / "lock.json"
    monkeypatch.setattr(audit, "_DOC", doc)
    monkeypatch.setattr(audit, "_SET", paths["embed_bench.json"])
    monkeypatch.setattr(audit, "_OUT", result_dir)
    monkeypatch.setattr(audit, "_LOCK", lock)
    monkeypatch.setattr(audit, "_FROZEN_FILES", paths)
    return paths, lock


@pytest.mark.parametrize(
    "target",
    ["audit_brief.py", "build_retrieval_set.py", "embed_bench.json", "remote_bench.sh"],
)
def test_default_freeze_comparison_covers_each_expanded_target(tmp_path, monkeypatch, target) -> None:
    paths, lock = _freeze_fixture(tmp_path, monkeypatch)
    frozen = audit._digests()
    lock.write_text(json.dumps(frozen), encoding="utf-8")
    normal = audit.Report()
    audit.check_frozen(normal, audit._digests())
    assert not normal.fail

    paths[target].write_bytes(paths[target].read_bytes() + b"!")
    #: ★기본은 **경고**다. 고치는 도중에는 어긋나는 것이 정상이라,
    #:   실패로 만들면 진짜 결함이 그 안에 묻힌다(2026-08-03 실제로 그랬다).
    lenient = audit.Report()
    audit.check_frozen(lenient, audit._digests())
    assert not lenient.fail
    assert any(target in m for m in lenient.warn)
    #: ★"이 상태를 검수받겠다"고 선언할 때만 실패다.
    mutant = audit.Report()
    audit.check_frozen(mutant, audit._digests(), strict=True)
    assert any(target in message for message in _messages(mutant, "동결본"))


def test_invalid_freeze_json_is_a_reported_failure(tmp_path, monkeypatch) -> None:
    _, lock = _freeze_fixture(tmp_path, monkeypatch)
    lock.write_text("[broken", encoding="utf-8")
    lenient = audit.Report()
    audit.check_frozen(lenient, audit._digests())
    assert not lenient.fail and lenient.warn
    report = audit.Report()
    audit.check_frozen(report, audit._digests(), strict=True)
    assert _messages(report, "동결본")


def test_result_freeze_digest_includes_file_name(tmp_path, monkeypatch) -> None:
    _freeze_fixture(tmp_path, monkeypatch)
    before = audit._digests()["results"]
    source = audit._OUT / "one.json"
    source.rename(audit._OUT / "renamed.json")
    after = audit._digests()["results"]
    assert after != before


def _generated_doc(outputs: dict[str, bytes]) -> bytes:
    chunks = []
    for name in ("fp16-report", "4bit-report", "prefixes", "repro"):
        chunks.append(
            b"<!-- BEGIN GENERATED: " + name.encode() + b" -->\r\n"
            + outputs[name]
            + b"<!-- END GENERATED: " + name.encode() + b" -->\r\n"
        )
    return b"".join(chunks)


def test_generated_regions_use_exact_stdout_bytes(monkeypatch) -> None:
    outputs = {name: f"{name}\r\n".encode() for name in
               ("fp16-report", "4bit-report", "prefixes", "repro")}

    def run(command, **_kwargs):
        args = command[3:]
        name = (
            "fp16-report" if args == ["--report", "--md"] else
            "4bit-report" if "4bit" in args else
            "prefixes" if "--prefixes" in args else "repro"
        )
        return SimpleNamespace(returncode=0, stdout=outputs[name], stderr=b"")

    monkeypatch.setattr(audit.subprocess, "run", run)
    doc = _generated_doc(outputs)
    normal = audit.Report()
    audit.check_generated(normal, doc)
    assert not normal.fail

    mutant = audit.Report()
    audit.check_generated(mutant, doc.replace(b"fp16-report\r\n", b"fp16-report\n", 1))
    assert _messages(mutant, "생성영역")


def test_missing_generated_regions_are_failures(monkeypatch) -> None:
    missing_all = audit.Report()
    audit.check_generated(missing_all, b"no generated markers\n")
    assert _messages(missing_all, "생성영역")

    outputs = {name: f"{name}\n".encode() for name in
               ("fp16-report", "4bit-report", "prefixes", "repro")}

    def run(command, **_kwargs):
        args = command[3:]
        name = (
            "fp16-report" if args == ["--report", "--md"] else
            "4bit-report" if "4bit" in args else
            "prefixes" if "--prefixes" in args else "repro"
        )
        return SimpleNamespace(returncode=0, stdout=outputs[name], stderr=b"")

    monkeypatch.setattr(audit.subprocess, "run", run)
    without_prefixes = _generated_doc(outputs).replace(
        b"<!-- BEGIN GENERATED: prefixes -->\r\nprefixes\n"
        b"<!-- END GENERATED: prefixes -->\r\n",
        b"",
    )
    missing_one = audit.Report()
    audit.check_generated(missing_one, without_prefixes)
    assert any("prefixes" in message for message in _messages(missing_one, "생성영역"))


def test_generated_command_exit_code_and_stderr_are_failures(monkeypatch) -> None:
    outputs = {name: f"{name}\n".encode() for name in
               ("fp16-report", "4bit-report", "prefixes", "repro")}
    calls = 0

    def run(_command, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(returncode=7, stdout=outputs["fp16-report"], stderr=b"boom\n")
        name = ("4bit-report", "prefixes", "repro")[calls - 2]
        return SimpleNamespace(returncode=0, stdout=outputs[name], stderr=b"")

    monkeypatch.setattr(audit.subprocess, "run", run)
    doc = _generated_doc({name: value.replace(b"\n", b"\r\n") for name, value in outputs.items()})
    # stdout도 문서와 같게 하여 이 변조가 종료 코드/stderr 검사만 겨냥하게 한다.
    outputs = {name: value.replace(b"\n", b"\r\n") for name, value in outputs.items()}
    report = audit.Report()
    audit.check_generated(report, doc)
    failures = _messages(report, "생성명령")
    assert any("종료 코드 7" in message for message in failures)
    assert any("boom" in message for message in failures)


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected"),
    [(7, b"", "종료 코드 7"), (0, b"stderr-only\n", "stderr-only")],
)
def test_generated_exit_code_and_stderr_are_checked_independently(
    monkeypatch, returncode: int, stderr: bytes, expected: str
) -> None:
    outputs = {name: f"{name}\n".encode() for name in
               ("fp16-report", "4bit-report", "prefixes", "repro")}
    calls = 0

    def run(_command, **_kwargs):
        nonlocal calls
        calls += 1
        name = ("fp16-report", "4bit-report", "prefixes", "repro")[calls - 1]
        if calls == 1:
            return SimpleNamespace(
                returncode=returncode, stdout=outputs[name], stderr=stderr
            )
        return SimpleNamespace(returncode=0, stdout=outputs[name], stderr=b"")

    monkeypatch.setattr(audit.subprocess, "run", run)
    report = audit.Report()
    audit.check_generated(report, _generated_doc(outputs))
    assert any(expected in message for message in _messages(report, "생성명령"))


def test_all_strong_word_candidates_are_printed() -> None:
    normal = audit.Report()
    audit.check_strong(normal, "\n".join("(관찰) 전부 확인됐다." for _ in range(13)))
    assert not normal.warn

    mutant = audit.Report()
    audit.check_strong(mutant, "\n".join("전부 확인됐다." for _ in range(13)))
    details = [message for message in mutant.warn if message.startswith("        ")]
    assert len(details) == 13


def test_evidence_tag_only_exempts_the_same_sentence() -> None:
    normal = audit.Report()
    audit.check_strong(normal, "(관찰) 전부 확인됐다.")
    assert not normal.warn

    mutant = audit.Report()
    audit.check_strong(mutant, "(관찰) 값입니다. 전부 확인됐다.")
    assert any("전부 확인됐다" in message for message in mutant.warn)


@pytest.mark.parametrize(
    "text",
    [
        "| (관찰) 값 | 전부 확인됐다. |",
        "(관찰) 값입니다.<br>전부 확인됐다.",
        "(관찰) 값입니다.<br/>전부 확인됐다.",
    ],
)
def test_evidence_tag_does_not_cross_markdown_or_html_boundaries(text: str) -> None:
    report = audit.Report()
    audit.check_strong(report, text)
    assert any("전부 확인됐다" in message for message in report.warn)


def test_evidence_tag_still_exempts_strong_word_in_same_table_cell() -> None:
    report = audit.Report()
    audit.check_strong(report, "| (관찰) 전부 확인됐다. |")
    assert not report.warn


def test_utf8_stdout_reconfigures_cp949_streams(monkeypatch) -> None:
    class Stream:
        def __init__(self) -> None:
            self.encoding = "cp949"
            self.calls = []

        def reconfigure(self, **kwargs) -> None:
            self.calls.append(kwargs)

    stdout, stderr = Stream(), Stream()
    monkeypatch.setattr(audit.sys, "stdout", stdout)
    monkeypatch.setattr(audit.sys, "stderr", stderr)
    audit._utf8_stdout()
    assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]


def test_utf8_stdout_can_emit_star_from_real_cp949_wrapper(monkeypatch) -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp949")
    monkeypatch.setattr(audit.sys, "stdout", stream)
    monkeypatch.setattr(audit.sys, "stderr", stream)

    audit._utf8_stdout()
    stream.write("★")
    stream.flush()

    assert stream.encoding.lower().replace("-", "") == "utf8"
    assert raw.getvalue().decode("utf-8") == "★"


@pytest.fixture
def real_claim_fixture(tmp_path, monkeypatch):
    source_doc = audit._DOC
    source_out = audit._OUT
    source_set = audit._SET
    doc = tmp_path / "brief.md"
    out = tmp_path / "results"
    eval_set = tmp_path / "embed_bench.json"
    shutil.copy2(source_doc, doc)
    shutil.copytree(source_out, out)
    shutil.copy2(source_set, eval_set)
    monkeypatch.setattr(audit, "_DOC", doc)
    monkeypatch.setattr(audit, "_OUT", out)
    monkeypatch.setattr(audit, "_SET", eval_set)
    rows_report = audit.Report()
    rows = audit._load_results(rows_report)
    assert not rows_report.fail
    # 본문 숫자 검사의 정상본은 현행 tail 스키마를 갖춘 것으로 만든다. 저장소의
    # Qwen3-4B@4bit 구형 스키마 결함은 별도 스키마 mutant 시험이 고정한다.
    for row in rows:
        if row.get("model") == "Qwen/Qwen3-Embedding-4B" and row.get("dtype") == "4bit":
            row["title"] = {"mrr@10": row["mrr@10"]}
            row["tail"] = copy.deepcopy(row["proviso"])
    data = audit._load_eval_set(rows_report)
    assert data is not None
    return doc, rows, data


def _run_claims(doc: pathlib.Path, rows: list[dict], data: dict) -> audit.Report:
    report = audit.Report()
    audit.check_claims(report, doc.read_text(encoding="utf-8"), rows, data)
    return report


def _mutate_doc(doc: pathlib.Path, before: str, after: str) -> None:
    text = doc.read_text(encoding="utf-8")
    assert before in text
    doc.write_text(text.replace(before, after, 1), encoding="utf-8", newline="")


def test_claim_sample_sizes_have_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "질의     제목 145개 · 뒷부분 60개(그중 진짜 면책 16개)",
                "질의     제목 144개 · 뒷부분 60개(그중 진짜 면책 16개)")
    assert any("표본 수" in message for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_eval_set_metadata_must_match_actual_arrays(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    mutant_data = copy.deepcopy(data)
    mutant_data["queries"].pop()
    assert _messages(_run_claims(doc, rows, mutant_data), "평가셋")


def test_claim_candidate_count_has_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "순위 아님 · 9개", "순위 아님 · 8개")
    assert any("예비 후보" in message for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_claim_4bit_metric_pair_has_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "제목 0.521→0.537", "제목 0.521→0.538")
    assert any("4bit 제목 전후값" in message for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_claim_4bit_title_pair_reads_nested_current_schema(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    qwen = next(row for row in rows
                if row.get("model") == "Qwen/Qwen3-Embedding-4B"
                and row.get("dtype") == "4bit")
    qwen["title"]["mrr@10"] = 0.538
    assert any("4bit 제목 전후값" in message
               for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_claim_4bit_tail_pair_has_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "뒷부분 0.253→0.270", "뒷부분 0.253→0.271")
    assert any("4bit 뒷부분 전후값" in message
               for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_claim_4bit_blind_before_after_has_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "| `Qwen3-Embedding-4B` | 13/60 | **0/60** |",
                "| `Qwen3-Embedding-4B` | 12/60 | **0/60** |")
    assert any("Qwen3-Embedding-4B 벡터무변화" in message
               for message in _messages(_run_claims(doc, rows, data), "본문숫자"))


def test_claim_gpu_condition_has_normal_and_mutant_cases(real_claim_fixture) -> None:
    doc, rows, data = real_claim_fixture
    assert not _run_claims(doc, rows, data).fail
    _mutate_doc(doc, "4bit 원 측정은 RunPod(RTX 2000 Ada), 재측정은 랩(RTX 4070 SUPER)입니다.",
                "4bit 원 측정은 RunPod(RTX 4070 SUPER), 재측정은 랩(RTX 4070 SUPER)입니다.")
    assert any("GPU 조건" in message for message in _messages(_run_claims(doc, rows, data), "본문숫자"))
