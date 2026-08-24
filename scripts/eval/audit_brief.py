"""임베딩 브리핑을 **제출 전에 스스로 검사한다.**

★왜 만들었나 — 코덱스와 **14라운드**를 돌고도 수렴하지 않았다.

    라운드마다 새 결함이 나왔다. 진단(2026-08-03)에 따르면 원인은 셋이다.

      ① **대상이 계속 움직였다.** 문서가 205 → 882줄로 커지는 동안
         **고정된 대상을 전수 검수한 적이 한 번도 없었다.**
      ② 검수가 전수가 아니었다 — 최신 수정 부위에 쏠렸다.
      ③ 내 서술 습관 — 결론을 먼저 쓰고 한계를 뒤에서 수습하고,
         `확인됐다`·`때문이다`·`전부`를 **증거보다 한 단계 세게** 쓴다.

    사람이 한 문장씩 잡아내는 방식으로는 안 끝난다. 기계로 잡히는 것은
    기계에 맡기고, 사람은 **기계가 못 잡는 것**만 본다.

★무엇을 검사하나 — **첫 실패에서 멈추지 않는다.** 전부 모아서 보고한다.

    1. 결과 JSON 의 개수·스키마       (재측정 누락·조건 불일치)
    2. 생성 영역이 실제 출력과 같은가  (손으로 고친 표)
    3. 본문 숫자가 데이터와 맞는가     (산문에 복제된 수치)
    4. 단정 어휘가 증거 태그를 갖는가  (증거보다 센 문장)
    5. 문서 안 링크가 실재하는가

★검사가 못 잡는 것도 **적어 둔다.** "통과 = 정확하다"가 아니다.

쓰는 법:
    python -m scripts.eval.audit_brief            # 검사
    python -m scripts.eval.audit_brief --freeze   # 통과 시 해시 동결
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DOC = _ROOT / "docs" / "handoff" / "13_임베딩모델_선정_브리핑.md"
_OUT = _ROOT / "data" / "eval" / "embed_bench_results"
_LOCK = _ROOT / "docs" / "handoff" / ".13_브리핑.audit.json"
_SET = _ROOT / "data" / "eval" / "embed_bench.json"

_FROZEN_FILES = {
    "doc": _DOC,
    "audit_brief.py": pathlib.Path(__file__).resolve(),
    "bench_embedders.py": _ROOT / "scripts" / "eval" / "bench_embedders.py",
    "merge_probe_remeasure.py": _ROOT / "scripts" / "eval" / "merge_probe_remeasure.py",
    "build_retrieval_set.py": _ROOT / "scripts" / "eval" / "build_retrieval_set.py",
    "embed_bench.json": _SET,
    "remote_bench.sh": _ROOT / "scripts" / "eval" / "remote_bench.sh",
}

_RESULT_REQUIRED = (
    "model", "dtype", "blind_eps", "proviso_blind_count", "proviso_probes",
    "probe_norm_min", "probe_norm_max", "title", "tail", "exclusion",
)
_TASK_REQUIRED = ("n", "mrr@10", "recall@1", "recall@5", "recall@10", "ranks")
_TASK_SIZES = {"title": 145, "tail": 60, "exclusion": 16}
_METRICS = ("mrr@10", "recall@1", "recall@5", "recall@10")
_BLIND_EPS = 1e-9
_EXPECTED_DTYPES = {"float16": 17, "4bit": 4}
_EXPECTED_CANDIDATES = 9
_QWEN_4B = "Qwen/Qwen3-Embedding-4B"
_OLD_4BIT_BLIND = {
    "Qwen3-Embedding-8B": 18,
    "comsat-embed-ko-8b-preview": 15,
    "Qwen3-Embedding-4B": 13,
    "llama-embed-nemotron-8b": 11,
}

#: ★증거 강도 태그. 센 어휘를 쓰려면 문장에 이 중 하나가 있어야 한다.
#:
#:   영어 태그를 한국어 본문에 박으면 읽는 사람이 걸린다. 그래서 **읽히는 말**로 둔다 —
#:   괄호 안의 이 표현이 곧 태그다.
#:
#:     (통제 비교)   다른 조건을 같게 두고 하나만 바꿔 쟀다
#:     (관찰)        본 값 그대로. 원인은 말하지 않는다
#:     (해석)        값에서 끌어낸 설명. 다르게 설명될 수 있다
#:     (정책)        팀이 고른 것. 측정이 아니다
#:     (원인 미상)   가리지 못했다
_TAGS = ("(통제 비교)", "(관찰)", "(해석)", "(정책)", "(원인 미상)",
         "controlled", "observed", "inferred", "policy", "unknown")

#: 증거보다 세게 읽히는 어휘. ★**금지어가 아니라 후보**다 —
#:   사람이 봐야 할 곳을 좁혀 준다.
_STRONG = [
    "때문입니다", "때문이다", "때문이었", "귀속", "입증", "증명",
    "확인됐", "확인됩니다", "확인된다", "실측으로 확인",
    "반드시", "필수입니다", "필수다", "전부", "완전히", "그대로다",
    "동일합니다", "동등", "보장합니다", "해결됐", "최고", "1위",
]

#: 이런 문맥은 검사에서 뺀다 — 철회문·금지문 인용·부정문이다.
_EXEMPT = re.compile(
    r"않습니다|않는다|아닙니다|아니다|없습니다|없다|말고|금지|철회|"
    r"쓰지 않은|하면 안|믿으면 안|단정하|안 됩니다|못 |뜻이 아|것은 아"
)


class Report:
    def __init__(self) -> None:
        self.fail: list[str] = []
        self.warn: list[str] = []
        self.ok: list[str] = []

    def bad(self, kind: str, msg: str) -> None:
        self.fail.append(f"[{kind}] {msg}")

    def note(self, kind: str, msg: str) -> None:
        self.warn.append(f"[{kind}] {msg}")

    def good(self, msg: str) -> None:
        self.ok.append(msg)


def _load_results(r: Report) -> list[dict[str, Any]]:
    """읽을 수 있는 결과를 전부 돌려준다. 깨진 파일도 파일별로 보고한다."""
    rows: list[dict[str, Any]] = []
    for p in sorted(_OUT.glob("*.json")):
        try:
            value = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            r.bad("결과JSON", f"{p.name} 을 읽지 못했습니다: {type(exc).__name__}: {exc}")
            continue
        if not isinstance(value, dict):
            r.bad("결과스키마", f"{p.name} 최상위 값이 객체가 아닙니다")
            continue
        value["__audit_file__"] = p.name
        rows.append(value)
    return rows


def _row_name(d: dict[str, Any]) -> str:
    model = str(d.get("model") or "<알 수 없는 모델>")
    dtype = str(d.get("dtype") or "?")
    source = str(d.get("__audit_file__") or "<파일명 없음>")
    return f"{model}@{dtype} [{source}]"


def _metric_value(r: Report, model: str, path: str, value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        r.bad("결과스키마", f"{model} 의 {path} 가 숫자가 아닙니다: {value!r}")
        return None
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        r.bad("지표범위", f"{model} 의 {path}={value!r} (기대 0~1)")
        return None
    return numeric


def check_results(r: Report, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """결과 JSON의 개수·스키마·조건·저장 지표를 전수 검사한다."""
    if rows is None:
        rows = _load_results(r)
    by_dtype: dict[str, int] = {}
    for d in rows:
        dtype = str(d.get("dtype", "?"))
        by_dtype[dtype] = by_dtype.get(dtype, 0) + 1
    if by_dtype != _EXPECTED_DTYPES:
        r.bad("결과", f"정밀도별 개수가 다릅니다: {by_dtype} (기대 {_EXPECTED_DTYPES})")
    else:
        r.good(f"결과 21건 · {by_dtype}")

    pairs: dict[tuple[Any, Any], list[str]] = {}
    for d in rows:
        pairs.setdefault((d.get("model"), d.get("dtype")), []).append(
            str(d.get("__audit_file__", "<파일명 없음>"))
        )
    duplicates = {pair: files for pair, files in pairs.items() if len(files) > 1}
    if duplicates:
        for (model, dtype), files in duplicates.items():
            r.bad("중복", f"모델·dtype 중복: {model!r} · {dtype!r}: {files}")
    else:
        r.good("모델·dtype 중복 없음")

    bad_dtype = [_row_name(d) for d in rows if d.get("probes_dtype_matches_original") is False]
    if bad_dtype:
        r.bad("조건", f"탐침 재측정의 정밀도가 원 측정과 다릅니다: {bad_dtype}")

    #: ★GPU 차이는 **막지 않고 알린다.** 원 측정 기계(RunPod)는 반납돼 못 맞춘다.
    #:   다만 문서가 그 사실을 적고 있어야 한다 — 아래 `check_claims` 가 센다.
    gpu_diff = [_row_name(d) for d in rows if d.get("probes_gpu_matches_original") is False]
    if gpu_diff:
        r.note("조건", f"탐침 재측정의 GPU 가 원 측정과 다릅니다({len(gpu_diff)}건): {gpu_diff}")

    for d in rows:
        model = _row_name(d)
        missing = [key for key in _RESULT_REQUIRED if key not in d]
        if missing:
            r.bad("결과스키마", f"{model} 의 필수 키 누락: {missing}")

        eps = d.get("blind_eps")
        # 키가 있는데 None이면 "값을 모른다"이지 정상값이 아니다. 앞서는 None을
        # 검사하지 않아 이름뿐인 blind_eps가 현행 결과처럼 통과했다.
        if "blind_eps" in d and eps != _BLIND_EPS:
            r.bad("blind_eps", f"{model} 의 blind_eps={eps!r} (기대 {_BLIND_EPS})")

        for key in _TASK_SIZES:
            if key not in d:
                r.bad("결과스키마", f"{model} 의 {key}.ranks 를 검사할 {key} 블록이 없습니다")
                continue
            blk = d[key]
            if not isinstance(blk, dict):
                r.bad("결과스키마", f"{model} 의 {key} 가 객체가 아닙니다")
                continue
            task_missing = [name for name in _TASK_REQUIRED if name not in blk]
            if task_missing:
                r.bad("결과스키마", f"{model} 의 {key} 필수 키 누락: {task_missing}")

            expected_n = _TASK_SIZES[key]
            n = blk.get("n")
            if n != expected_n:
                r.bad("표본수", f"{model} 의 {key}.n={n!r} (기대 {expected_n})")

            for metric in _METRICS:
                if metric in blk:
                    _metric_value(r, model, f"{key}.{metric}", blk[metric])

            # 보고표의 제목 열은 호환용 최상위 지표를 읽고, ranks 검산은 title
            # 블록을 읽는다. 두 벌이 갈라지면 각각은 정상이어도 표가 틀린다.
            if key == "title":
                for metric in _METRICS:
                    top = d.get(metric)
                    nested = blk.get(metric)
                    if (isinstance(top, (int, float)) and not isinstance(top, bool)
                            and isinstance(nested, (int, float)) and not isinstance(nested, bool)
                            and not math.isclose(float(top), float(nested), abs_tol=5e-5)):
                        r.bad(
                            "지표정합",
                            f"{model} 의 최상위 {metric}={top} ≠ title.{metric}={nested}",
                        )

            ranks = blk.get("ranks")
            if not isinstance(ranks, list):
                if "ranks" in blk:
                    r.bad("결과스키마", f"{model} 의 {key}.ranks 가 배열이 아닙니다")
                continue
            if n != len(ranks):
                r.bad("결과", f"{model} 의 {key}.ranks 길이 {len(ranks)} ≠ n {n!r}")
            bad_ranks = [rank for rank in ranks
                         if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0]
            if bad_ranks:
                r.bad("결과스키마", f"{model} 의 {key}.ranks 에 잘못된 등수: {bad_ranks}")
                continue
            if not ranks:
                continue
            calc_mrr = round(sum(1.0 / rank if 1 <= rank <= 10 else 0.0
                                 for rank in ranks) / len(ranks), 4)
            calc_hit = round(sum(1 <= rank <= 10 for rank in ranks) / len(ranks), 4)
            stored_mrr = blk.get("mrr@10")
            stored_hit = blk.get("recall@10")
            if isinstance(stored_mrr, (int, float)) and not isinstance(stored_mrr, bool):
                if not math.isclose(float(stored_mrr), calc_mrr, abs_tol=5e-5):
                    r.bad("지표재계산", f"{model} 의 {key}.mrr@10={stored_mrr} ≠ ranks 재계산 {calc_mrr}")
            if isinstance(stored_hit, (int, float)) and not isinstance(stored_hit, bool):
                if not math.isclose(float(stored_hit), calc_hit, abs_tol=5e-5):
                    r.bad("지표재계산", f"{model} 의 {key}.recall@10={stored_hit} ≠ ranks Hit@10 {calc_hit}")

        for metric in (*_METRICS, "truncated_ratio"):
            if metric in d:
                _metric_value(r, model, metric, d[metric])

    # 오진 기록: 처음에는 이 파일이 `--probes-only` 전용 결과라서 중첩 과제가
    # 없는 정상 예외일 수 있다고 보았다. 실제 파일에는 구형 최상위 검색 지표가 있고,
    # `bench_embedders.run()`은 probes-only 때 기존 JSON에 탐침 필드만 병합한다.
    # 따라서 Qwen3-4B@4bit는 모델 예외가 아니라 ranks 도입 전 전체 측정이 남은 것이다.
    # 비교·재계산 근거가 없으므로 다른 20건과 똑같이 필수 키 누락으로 보고한다.
    return rows


def check_generated(r: Report, doc: bytes) -> None:
    """생성 영역 바이트가 명령의 stdout 바이트와 정확히 같은가.

    ★"출력 그대로 붙였습니다"라고 적어 놓고 손으로 고치는 일을 막는다.
      실제로 그랬다 — 4bit 행을 지우다 머리글·구분선이 데이터와 어긋났다.

    ★앞서는 양쪽을 `strip()`하고 텍스트 모드에서 개행을 정규화한 뒤에도
      "그대로"라고 보고했다. 그것은 바이트 비교가 아니었다. 이제 BEGIN 다음
      바이트부터 END 직전까지(명령의 마지막 개행 포함)를 raw stdout과 비교한다.
    """
    cmds = {
        "fp16-report": ["--report", "--md"],
        "4bit-report": ["--report", "--md", "--dtype", "4bit"],
        "prefixes": ["--prefixes", "--md"],
        "repro": ["--repro", "--md"],
    }
    found = dict(re.findall(
        rb"<!-- BEGIN GENERATED: (\S+) -->(?:\r\n|\n)(.*?)"
        rb"<!-- END GENERATED: \1 -->",
        doc, re.S))
    if not found:
        r.bad("생성영역", "필수 생성 영역 마커가 전부 없습니다")
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for name, args in cmds.items():
        encoded_name = name.encode("ascii")
        if encoded_name not in found:
            r.bad("생성영역", f"필수 {name} 영역이 문서에 없습니다")
            continue
        command = " ".join(args)
        try:
            cp = subprocess.run(
                [sys.executable, "-m", "scripts.eval.bench_embedders", *args],
                capture_output=True, env=env, cwd=_ROOT,
            )
        except OSError as exc:
            r.bad("생성명령", f"{name} (`{command}`) 실행 자체가 실패했습니다: {exc}")
            continue
        if cp.returncode != 0:
            r.bad("생성명령", f"{name} (`{command}`) 종료 코드 {cp.returncode}")
        if cp.stderr:
            stderr = cp.stderr.decode("utf-8", errors="replace")
            r.bad("생성명령", f"{name} (`{command}`) stderr:\n{stderr}")
        if found[encoded_name] != cp.stdout:
            r.bad("생성영역", f"{name} 이 `{' '.join(args)}` stdout과 바이트 단위로 다릅니다")
        else:
            r.good(f"생성영역 {name} 바이트 일치")


def _load_eval_set(r: Report) -> dict[str, Any] | None:
    try:
        data = json.loads(_SET.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        r.bad("평가셋JSON", f"{_SET.name} 을 읽지 못했습니다: {type(exc).__name__}: {exc}")
        return None
    if not isinstance(data, dict):
        r.bad("평가셋JSON", f"{_SET.name} 최상위 값이 객체가 아닙니다")
        return None
    return data


def _expect_claim(r: Report, label: str, doc: str, expected: str) -> None:
    if expected not in doc:
        r.bad("본문숫자", f"{label}: 본문에 `{expected}` 가 없습니다")
    else:
        r.good(f"본문숫자 {label}")


def check_claims(
    r: Report,
    doc: str,
    rows: list[dict[str, Any]] | None = None,
    eval_set: dict[str, Any] | None = None,
) -> None:
    """본문 산문에 흩어진 **숫자**가 데이터와 맞는가.

    ★표는 대조했지만 **산문은 아무도 대조하지 않았다.**
      그래서 `0.549`(실제 0.548) · `1±0.002`(실제 0.999579~1.000488) ·
      `34건`(실제 46건) 이 계속 살아남았다.
    """
    if rows is None:
        rows = _load_results(r)
    if eval_set is None:
        eval_set = _load_eval_set(r)
    indexed = {
        f"{d.get('model')}|{d.get('dtype')}": d
        for d in rows if d.get("model") is not None and d.get("dtype") is not None
    }
    fp16 = [d for d in rows if d.get("dtype") == "float16"]

    def _norm(dtype: str) -> tuple[str, str] | None:
        v = [(d["probe_norm_min"], d["probe_norm_max"])
             for d in rows if d.get("dtype") == dtype
             and isinstance(d.get("probe_norm_min"), (int, float))
             and isinstance(d.get("probe_norm_max"), (int, float))]
        if not v:
            r.bad("본문숫자", f"{dtype} 노름 범위를 계산할 결과가 없습니다")
            return None
        return f"{min(a for a, _ in v):.6f}", f"{max(b for _, b in v):.6f}"

    current = indexed.get("jhgan/ko-sroberta-multitask|float16")
    facts = {
        "fp16 측정 횟수": (str(len(fp16)), r"fp16 (\d+)회"),
    }
    if current is None or "proviso_blind_count" not in current:
        r.bad("본문숫자", "현재 모델의 벡터무변화 값을 결과에서 찾지 못했습니다")
    else:
        facts["현재 모델 벡터무변화"] = (
            str(current["proviso_blind_count"]), r"탐침 60건 중 \*\*(\d+)건\*\*"
        )

    #: ★노름 범위는 **쌍으로** 본다. 하한만 보면 4bit 값을 fp16 으로 오인한다
    #:   (실제로 이 검사가 처음에 그랬다 — 검사도 틀릴 수 있다).
    for label, dtype in (("fp16", "float16"), ("4bit", "4bit")):
        bounds = _norm(dtype)
        if bounds is None:
            continue
        lo, hi = bounds
        want = f"**{lo}~{hi}**"
        alt = f"**{lo}**~**{hi}**"
        if want not in doc and alt not in doc:
            r.bad("본문숫자", f"{label} 노름 범위 {lo}~{hi} 가 본문에 그대로 없습니다")
        else:
            r.good(f"본문숫자 {label} 노름 범위 {lo}~{hi}")
    for label, (want, pat) in facts.items():
        got = re.findall(pat, doc)
        if not got:
            r.bad("본문숫자", f"{label} 를 본문에서 못 찾았습니다(패턴 {pat})")
        elif any(g != want for g in got):
            r.bad("본문숫자", f"{label}: 본문 {sorted(set(got))} ≠ 데이터 {want}")
        else:
            r.good(f"본문숫자 {label} = {want}")

    if eval_set is not None:
        queries = eval_set.get("queries")
        tail_queries = eval_set.get("proviso_queries")
        actual_sample = (
            len(queries) if isinstance(queries, list) else None,
            len(tail_queries) if isinstance(tail_queries, list) else None,
            sum(q.get("is_exclusion") is True for q in tail_queries)
            if isinstance(tail_queries, list) and all(isinstance(q, dict) for q in tail_queries)
            else None,
        )
        sample = (
            eval_set.get("query_count"), eval_set.get("proviso_query_count"),
            eval_set.get("exclusion_query_count"),
        )
        expected_sample = tuple(_TASK_SIZES.values())
        if sample != expected_sample:
            r.bad("평가셋", f"평가셋 표본 수 {sample} ≠ 기대 {expected_sample}")
        if actual_sample != sample:
            r.bad("평가셋", f"평가셋 메타데이터 {sample} ≠ 실제 배열 {actual_sample}")
        phrase = f"질의     제목 {sample[0]}개 · 뒷부분 {sample[1]}개(그중 진짜 면책 {sample[2]}개)"
        _expect_claim(r, "표본 수 145/60/16", doc, phrase)

    candidate_line = re.search(r"열세가 확인되지 않은 모델[^\n]*", doc)
    candidate_numbers = [] if candidate_line is None else re.findall(r"(\d+)개", candidate_line.group(0))
    if not candidate_numbers:
        r.bad("본문숫자", "예비 후보 개수 문장을 찾지 못했습니다")
    elif int(candidate_numbers[-1]) != _EXPECTED_CANDIDATES:
        r.bad("본문숫자", f"예비 후보 {candidate_numbers[-1]}개 ≠ 기대 {_EXPECTED_CANDIDATES}개")
    else:
        section = re.search(
            r"#### 열세가 확인되지 않은 모델.*?\n(.*?)(?:\n####|\n###|\n---)", doc, re.S
        )
        table_rows = [] if section is None else re.findall(r"^\| `[^\n]+$", section.group(1), re.M)
        if len(table_rows) != _EXPECTED_CANDIDATES:
            r.bad("본문숫자", f"예비 후보 표 행 {len(table_rows)}개 ≠ 제목 {_EXPECTED_CANDIDATES}개")
        else:
            r.good(f"본문숫자 예비 후보 {_EXPECTED_CANDIDATES}개")

    qwen_fp = indexed.get(f"{_QWEN_4B}|float16")
    qwen_4bit = indexed.get(f"{_QWEN_4B}|4bit")
    if qwen_fp is None or qwen_4bit is None:
        r.bad("본문숫자", "Qwen3-Embedding-4B fp16/4bit 짝을 결과에서 찾지 못했습니다")
    else:
        fp_tail = qwen_fp.get("tail")
        bit_tail = qwen_4bit.get("tail")
        if not isinstance(bit_tail, dict):
            legacy_tail = qwen_4bit.get("proviso")
            r.bad(
                "본문숫자",
                "Qwen3-4B@4bit의 tail이 없어 전후값을 완전한 현행 스키마로 검증할 수 없습니다; "
                "이미 저장된 proviso 값은 나머지 불일치를 모으는 데만 사용합니다",
            )
            bit_tail = legacy_tail if isinstance(legacy_tail, dict) else None
        fp_title = qwen_fp.get("title")
        bit_title = qwen_4bit.get("title")
        # 구형 결과도 나머지 불일치를 모으기 위해 최상위 값으로 계속 읽되,
        # 현행 블록이 있으면 반드시 그 값을 쓴다. 스키마 누락 자체는 check_results가 실패시킨다.
        fp_title = fp_title if isinstance(fp_title, dict) else qwen_fp
        bit_title = bit_title if isinstance(bit_title, dict) else qwen_4bit
        try:
            title_pair = f"제목 {float(fp_title['mrr@10']):.3f}→{float(bit_title['mrr@10']):.3f}"
            tail_pair = f"뒷부분 {float(fp_tail['mrr@10']):.3f}→{float(bit_tail['mrr@10']):.3f}"
        except (KeyError, TypeError, ValueError) as exc:
            r.bad("본문숫자", f"Qwen3-4B 전후값을 계산하지 못했습니다: {exc}")
        else:
            _expect_claim(r, "4bit 제목 전후값", doc, title_pair)
            _expect_claim(r, "4bit 뒷부분 전후값", doc, tail_pair)

    by_short_model = {
        str(d.get("model", "")).split("/")[-1]: d for d in rows if d.get("dtype") == "4bit"
    }
    for short, old_count in _OLD_4BIT_BLIND.items():
        row = by_short_model.get(short)
        if row is None:
            r.bad("본문숫자", f"4bit 전후값용 결과가 없습니다: {short}")
            continue
        new_count = row.get("proviso_blind_count")
        expected = f"| `{short}` | {old_count}/60 | **{new_count}/60** |"
        _expect_claim(r, f"{short} 벡터무변화 전후값", doc, expected)

    four_bit = [d for d in rows if d.get("dtype") == "4bit"]
    origin_gpus = {str(d.get("gpu")) for d in four_bit}
    probe_gpus = {str(d.get("probes_gpu")) for d in four_bit}
    mismatches = {d.get("probes_gpu_matches_original") for d in four_bit}
    gpu_sentence = re.search(
        r"4bit 원 측정은[^\n.!?]*RTX 2000 Ada[^\n.!?]*재측정은[^\n.!?]*RTX 4070 SUPER[^\n.!?]*[.!?]",
        doc,
    )
    if (not four_bit or not all("RTX 2000 Ada" in gpu for gpu in origin_gpus)
            or not all("RTX 4070 SUPER" in gpu for gpu in probe_gpus)
            or mismatches != {False}):
        r.bad("조건", f"4bit GPU 조건 데이터가 예상과 다릅니다: 원본={origin_gpus}, 재측정={probe_gpus}, 일치={mismatches}")
    elif not gpu_sentence:
        r.bad("본문숫자", "4bit 원 측정/재측정 GPU 조건을 같은 문장에서 찾지 못했습니다")
    else:
        r.good("본문숫자 4bit GPU 조건 문장")


def check_env(r: Report, doc: str) -> None:
    """문서가 적은 **라이브러리 버전이 실제와 같은가.**

    ★문서는 `5.6.1`, requirements 와 설치본은 `5.6.0` 이었다(코덱스 지적).
      재현하겠다는 사람이 다른 버전을 깐다.
    """
    try:
        import sentence_transformers as st
        got = st.__version__
    except Exception:  # noqa: BLE001
        r.note("환경", "sentence-transformers 를 못 읽었습니다")
        return
    if f"sentence-transformers **{got}**" not in doc and f"sentence-transformers {got}" not in doc:
        r.bad("환경", f"문서의 sentence-transformers 버전이 설치본({got})과 다릅니다")
    else:
        r.good(f"환경 sentence-transformers {got}")


def _claim_segments(line: str) -> list[str]:
    """증거 태그가 효력을 갖는 최소 텍스트 구간을 나눈다.

    Markdown 표의 셀과 HTML 줄바꿈은 사람이 서로 다른 주장으로 읽는 명시적
    경계다. 일반 한국어의 구두점 없는 문장까지 추측으로 자르지는 않는다.
    """
    stripped = line.strip()
    cells = re.split(r"(?<!\\)\|", stripped.strip("|")) if stripped.startswith("|") else [line]
    out: list[str] = []
    for cell in cells:
        for html_part in re.split(r"<br\s*/?>", cell, flags=re.I):
            out.extend(
                part for part in re.split(
                    r"(?<=[.!?])(?:\*{1,2}|_{1,2})?\s+", html_part
                )
                if part.strip()
            )
    return out


def check_strong(r: Report, doc: str) -> None:
    """단정 어휘가 **증거 태그**를 달고 있는가.

    ★어휘 자체는 죄가 없다. `~라고 하지 않는다` 같은 부정문·인용문은 뺀다.
      남은 것은 **사람이 봐야 할 후보**다 — 실패가 아니라 경고로 낸다.
    """
    fence = False
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(doc.splitlines(), 1):
        if line.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        # 마침표·표 셀·<br>처럼 문서가 명시한 경계만 나눈다. 한계: 구두점을
        # 생략한 일반 한국어 문장의 의미 경계는 확정할 수 없다.
        for sentence in _claim_segments(line):
            if _EXEMPT.search(sentence):
                continue
            for w in _STRONG:
                if w in sentence and not any(t in sentence for t in _TAGS):
                    hits.append((i, sentence.strip()))
                    break
    if hits:
        r.note("주장강도", f"증거 태그 없는 단정 어휘 {len(hits)}곳 — 사람이 확인하세요")
        for i, l in hits:
            r.warn.append(f"        {i}: {l}")
    else:
        r.good("단정 어휘 후보 없음")


def check_links(r: Report, doc: str) -> None:
    """문서가 가리키는 **로컬 파일이 실재하는가.**"""
    missing = []
    for target in re.findall(r"\]\((?!https?:)([^)#]+)", doc):
        p = (_DOC.parent / target).resolve()
        if not p.exists():
            missing.append(target)
    if missing:
        r.bad("링크", f"없는 파일을 가리킵니다: {sorted(set(missing))}")
    else:
        r.good("로컬 링크 전부 실재")


def _digests(r: Report | None = None) -> dict[str, str]:
    """★검수 대상을 고정한다. 없는 파일도 숨기지 않고 스냅샷에 남긴다."""
    out: dict[str, str] = {}
    frozen_files = dict(_FROZEN_FILES)
    # 테스트와 호출자가 `_DOC`를 바꾸면 문서 동결 대상도 같은 파일이어야 한다.
    frozen_files["doc"] = _DOC
    frozen_files["embed_bench.json"] = _SET
    for name, path in frozen_files.items():
        try:
            out[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            out[name] = f"!ERROR:{type(exc).__name__}:{exc}"
            if r is not None:
                r.bad("동결범위", f"{name} ({path}) 을 읽지 못했습니다: {exc}")
    h = hashlib.sha256()
    result_files = sorted(_OUT.glob("*.json"))
    if not result_files and r is not None:
        r.bad("동결범위", f"결과 JSON이 없습니다: {_OUT}")
    for p in result_files:
        try:
            data = p.read_bytes()
            name = p.name.encode("utf-8")
            # 파일명과 길이 경계를 함께 넣는다. 앞서는 내용만 이어 붙여 rename과
            # [ab,c]↔[a,bc] 재배치를 구분하지 못했다.
            h.update(len(name).to_bytes(8, "big"))
            h.update(name)
            h.update(len(data).to_bytes(8, "big"))
            h.update(data)
        except OSError as exc:
            if r is not None:
                r.bad("동결범위", f"{p} 을 읽지 못했습니다: {exc}")
            h.update(f"!ERROR:{p.name}:{exc}".encode("utf-8"))
    out["results"] = h.hexdigest()
    return out


def check_frozen(r: Report, current: dict[str, str], *, strict: bool = False) -> None:
    """동결본과 지금 대상이 같은가.

    ★**어긋남을 기본으로 실패 처리하지 않는다.**

        고치는 도중에는 어긋나는 것이 정상이다 — 고칠 때마다 실패가 뜨면
        진짜 결함이 그 안에 묻힌다. 실제로 그랬다(2026-08-03):
        도구를 보강하자 동결 어긋남 8건이 뜨면서 **진짜 결함 5건**을 덮었다.

        동결이 뜻을 갖는 때는 **"이 상태를 검수받겠다"고 선언할 때**다.
        그때는 `--strict-frozen` 으로 실패로 만든다.
    """
    """기본 실행에서 저장된 동결 스냅샷과 현재 대상을 대조한다."""
    try:
        locked = json.loads(_LOCK.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        (r.bad if strict else r.note)("동결본", f"{_LOCK} 을 읽지 못했습니다: {type(exc).__name__}: {exc}")
        return
    if not isinstance(locked, dict):
        r.bad("동결본", f"{_LOCK.name} 최상위 값이 객체가 아닙니다")
        return
    changed = False
    for name in sorted(set(locked) | set(current)):
        before = locked.get(name, "<동결본에 없음>")
        now = current.get(name, "<현재 범위에 없음>")
        if before != now:
            changed = True
            msg = f"{name} 이 동결본과 다릅니다: 동결={before} · 현재={now}"
            (r.bad if strict else r.note)("동결본", msg)
    if not changed:
        r.good(f"동결본 {_LOCK.name} 과 현재 대상 일치")


def main() -> int:
    ap = argparse.ArgumentParser()
    #: ★검수받겠다고 선언할 때만 동결 어긋남을 실패로 만든다.
    ap.add_argument("--strict-frozen", action="store_true",
                    help="동결본과 어긋나면 실패로 처리(검수 직전에 쓴다)")
    ap.add_argument("--freeze", action="store_true",
                    help="검사를 통과하면 대상 해시를 기록한다(동결)")
    a = ap.parse_args()

    r = Report()
    before = _digests(r)
    if not a.freeze:
        check_frozen(r, before, strict=a.strict_frozen)

    try:
        doc_bytes = _DOC.read_bytes()
        doc = doc_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        r.bad("문서", f"{_DOC} 을 UTF-8로 읽지 못했습니다: {type(exc).__name__}: {exc}")
        doc_bytes = b""
        doc = ""

    rows = _load_results(r)

    def run_check(name: str, fn: Any, *args: Any) -> None:
        try:
            fn(r, *args)
        except Exception as exc:  # noqa: BLE001
            # 검사 하나의 구현 결함 때문에 뒤 검사를 조용히 버리면 "전부 보고"가 아니다.
            r.bad("감사도구", f"{name} 검사 자체가 실패했습니다: {type(exc).__name__}: {exc}")

    run_check("결과", check_results, rows)
    run_check("생성영역", check_generated, doc_bytes)
    eval_set = _load_eval_set(r)
    run_check("본문숫자", check_claims, doc, rows, eval_set)
    run_check("환경", check_env, doc)
    run_check("주장강도", check_strong, doc)
    run_check("링크", check_links, doc)

    #: ★검사 중에 대상이 바뀌었으면 결과를 **믿지 않는다.**
    after = _digests()
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            r.bad("동결", f"검사 중 {name} 이 바뀌었습니다 — 결과를 폐기하고 다시 도세요")

    if a.freeze and not r.fail:
        try:
            _LOCK.write_text(
                json.dumps(before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            r.bad("동결본", f"{_LOCK} 을 쓰지 못했습니다: {exc}")
        else:
            r.good(f"동결: {_LOCK.name} 에 해시를 기록했습니다")

    print(f"{'=' * 60}\n임베딩 브리핑 감사\n{'=' * 60}")
    for m in r.ok:
        print(f"  ok   {m}")
    for m in r.warn:
        print(f"  ★    {m}" if m.startswith("[") else m)
    for m in r.fail:
        print(f"  ✗    {m}")
    print(f"\n통과 {len(r.ok)} · 확인필요 {len([m for m in r.warn if m.startswith('[')])} "
          f"· 실패 {len(r.fail)}")

    #: ★**이 검사가 못 잡는 것**을 적는다. "통과 = 정확하다"가 아니다.
    print("""
★이 검사가 **못 잡는 것**
  · 문장이 데이터가 뒷받침하는 것보다 센가 — 어휘 후보만 좁혀 줍니다
  · 실험 설계가 타당한가 · 통계 해석이 맞는가
  · 후보 선별 규칙이 결과와 일관된가
  → 이건 사람(또는 코덱스)이 봐야 합니다. 통과했다고 정확한 게 아닙니다.""")

    return 1 if r.fail else 0


def _utf8_stdout() -> None:
    """기본 CP949 콘솔에서도 감사 메시지를 끝까지 출력한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            # 오진 기록: 처음에는 모든 스트림이 TextIOWrapper라고 가정했다.
            # pytest의 캡처 스트림처럼 reconfigure가 없는 객체도 있으므로 그 경우만
            # 명시적으로 그대로 두며, 실제 출력 실패는 호출자에게 숨기지 않는다.
            continue


if __name__ == "__main__":
    _utf8_stdout()
    sys.exit(main())
