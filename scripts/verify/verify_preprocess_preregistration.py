"""D4 전처리 품질 기준의 prospective 사전등록을 검증한다.

현재 S7을 소급 승인하지 않는다. 기본 실행은 레지스트리 파일과 SHA-256 sidecar,
fail-closed 계약만 검사한다. 다음 extraction generation은 ``--evidence``로 별도 증거를
제출해야 하며 사람 D5와 아직 합의되지 않은 수치 기준은 누락 시 실패한다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "config" / "preprocess_quality_preregistration.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_MUTANTS = {
    "missing-prose-veto",
    "missing-method-gate",
    "discarded-rejection-reason",
}
EXPECTED_SCALARS = {
    "document_count": ("eq", 1367),
    "manifest_mismatch_count": ("eq", 0),
    "reference_confirmed_violation_count": ("eq", 0),
    "candidate_serving_leak_count": ("eq", 0),
    "candidate_citation_leak_count": ("eq", 0),
}
REQUIRED_QUALITY_METRICS = {
    "word_coverage": "gte",
    "cell_mislink_rate": "lte",
    "sentence_cut_rate": "lte",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def sidecar_path(path: Path) -> Path:
    return path.with_suffix(".sha256")


def _timestamp(value: Any, field: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{field}: timezone 포함 ISO-8601 문자열이 아니다")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field}: ISO-8601 파싱 실패")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field}: timezone이 없다")
        return None
    return parsed


def _check_sidecar(path: Path) -> list[str]:
    sidecar = sidecar_path(path)
    if not sidecar.is_file():
        return [f"{sidecar}: SHA-256 sidecar 없음"]
    try:
        recorded = sidecar.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError) as exc:
        return [f"{sidecar}: sidecar 읽기 실패: {exc}"]
    if not SHA256_RE.fullmatch(recorded):
        return [f"{sidecar}: SHA-256 64자리 hex가 아님"]
    actual = sha256_file(path)
    return [] if recorded == actual else [f"{sidecar}: 지문 불일치"]


def validate_registry(registry: dict[str, Any]) -> list[str]:
    """정해진 계약이 빠지거나 완화되면 실패한다."""
    errors: list[str] = []
    if registry.get("registry_version") != "1":
        errors.append("registry_version: 지원 값은 '1'이다")
    if registry.get("policy_id") != "preprocess-d4-prospective-v1":
        errors.append("policy_id: 정본 정책 ID 불일치")
    _timestamp(registry.get("registered_at"), "registered_at", errors)

    scope = registry.get("scope") or {}
    if scope.get("mode") != "prospective_only":
        errors.append("scope.mode: prospective_only가 아니다")
    _timestamp(
        scope.get("effective_manifest_built_at_not_before"),
        "scope.effective_manifest_built_at_not_before",
        errors,
    )
    historical = {
        item.get("generation_id"): item.get("status")
        for item in scope.get("historical_generations") or []
        if isinstance(item, dict)
    }
    if historical.get("s7_hybrid-table-v1") != "not_eligible_retroactively":
        errors.append("현행 S7은 not_eligible_retroactively여야 한다")

    machine = registry.get("machine_gates") or {}
    for name, (operator, value) in EXPECTED_SCALARS.items():
        gate = machine.get(name) or {}
        if gate.get("operator") != operator or gate.get("value") != value:
            errors.append(f"machine_gates.{name}: {operator} {value} 계약 불일치")
    mutation = machine.get("d6_mutation") or {}
    if mutation.get("operator") != "all_required_ids_killed":
        errors.append("machine_gates.d6_mutation.operator 불일치")
    if set(mutation.get("required_ids") or []) != REQUIRED_MUTANTS:
        errors.append("machine_gates.d6_mutation.required_ids 불일치")

    external = registry.get("external_gates") or {}
    d5 = external.get("d5_human_table_quality") or {}
    if d5.get("status") != "required_external":
        errors.append("D5 사람 라벨은 required_external이어야 한다")
    for metric in ("precision", "recall"):
        gate = (d5.get("per_stratum") or {}).get(metric) or {}
        if gate.get("operator") != "gte" or gate.get("value") != 0.9:
            errors.append(f"D5 층별 {metric} 계약은 gte 0.9다")
    binding = external.get("quality_threshold_binding") or {}
    if binding.get("status") != "required_external":
        errors.append("미합의 품질 수치 기준은 required_external이어야 한다")
    for metric, operator in REQUIRED_QUALITY_METRICS.items():
        gate = (binding.get("required_metrics") or {}).get(metric) or {}
        if gate.get("operator") != operator or gate.get("value") is not None:
            errors.append(f"{metric}: 결과 전 수치 미합의 상태(null)가 아니다")

    decision = registry.get("decision") or {}
    if decision.get("all_gates_required") is not True:
        errors.append("decision.all_gates_required는 true여야 한다")
    if decision.get("missing_or_unknown_evidence") != "fail_closed":
        errors.append("decision.missing_or_unknown_evidence는 fail_closed여야 한다")
    if decision.get("human_release_approval_required") is not True:
        errors.append("decision.human_release_approval_required는 true여야 한다")
    if decision.get("serving_pointer_change_by_this_registry") is not False:
        errors.append("이 registry는 serving pointer를 바꾸면 안 된다")
    return errors


def load_registry(path: Path = DEFAULT_REGISTRY) -> tuple[dict[str, Any], list[str]]:
    if not path.is_file():
        return {}, [f"registry 없음: {path}"]
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"registry 읽기 실패: {exc}"]
    if not isinstance(registry, dict):
        return {}, ["registry 최상위가 객체가 아니다"]
    return registry, [*_check_sidecar(path), *validate_registry(registry)]


def generation_status(registry: dict[str, Any], generation_id: str) -> str:
    for item in (registry.get("scope") or {}).get("historical_generations") or []:
        if isinstance(item, dict) and item.get("generation_id") == generation_id:
            return str(item.get("status") or "unknown")
    return "prospective_evidence_required"


def evaluate_machine_gates(registry: dict[str, Any], results: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    machine = registry["machine_gates"]
    for name in EXPECTED_SCALARS:
        if name not in results:
            errors.append(f"machine_results.{name}: 누락(fail-closed)")
            continue
        expected = machine[name]["value"]
        if results[name] != expected:
            errors.append(f"machine_results.{name}: {results[name]!r} != {expected!r}")
    killed = results.get("d6_killed_mutants")
    if not isinstance(killed, list):
        errors.append("machine_results.d6_killed_mutants: 누락(fail-closed)")
    else:
        missing = set(machine["d6_mutation"]["required_ids"]) - set(killed)
        if missing:
            errors.append(f"D6 mutation 생존: {sorted(missing)}")
    return errors


def evaluate_external_summary(registry: dict[str, Any], evidence: dict[str, Any]) -> list[str]:
    """사람/사전 binding 결과의 최소 요약을 fail-closed로 검사한다.

    실제 artifact와 지문 검사는 CLI ``--evidence`` 경로에서 추가로 수행한다.
    """
    errors: list[str] = []
    d5 = evidence.get("d5_human_table_quality")
    if not isinstance(d5, dict) or d5.get("status") != "passed":
        errors.append("D5 사람 라벨 결과가 passed가 아니다(required_external)")
    else:
        if d5.get("human_reviewed") is not True or d5.get("adjudication_complete") is not True:
            errors.append("D5 사람 검수/불일치 조정이 완료되지 않았다")
        strata = d5.get("strata")
        if not isinstance(strata, list) or not strata:
            errors.append("D5 층별 결과가 없다")
        else:
            limits = registry["external_gates"]["d5_human_table_quality"]["per_stratum"]
            for index, row in enumerate(strata):
                for metric in ("precision", "recall"):
                    value = row.get(metric) if isinstance(row, dict) else None
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                        errors.append(f"D5 strata[{index}].{metric}: 유한 숫자가 아니다")
                    elif value < limits[metric]["value"]:
                        errors.append(f"D5 strata[{index}].{metric}: {value} < {limits[metric]['value']}")
                for field in ("f1", "ci95"):
                    if not isinstance(row, dict) or field not in row:
                        errors.append(f"D5 strata[{index}].{field}: 누락")

    binding = evidence.get("quality_threshold_binding")
    if not isinstance(binding, dict) or binding.get("status") != "passed":
        errors.append("품질 수치 기준 사전 binding이 없다(required_external)")
    else:
        thresholds = binding.get("thresholds") or {}
        values = evidence.get("quality_metrics") or {}
        for metric, operator in REQUIRED_QUALITY_METRICS.items():
            gate = thresholds.get(metric) or {}
            threshold = gate.get("value")
            value = values.get(metric)
            if gate.get("operator") != operator:
                errors.append(f"binding.{metric}.operator: {operator}가 아니다")
                continue
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in (threshold, value)):
                errors.append(f"binding/quality_metrics.{metric}: 유한 숫자가 아니다")
                continue
            if (operator == "gte" and value < threshold) or (operator == "lte" and value > threshold):
                errors.append(f"quality_metrics.{metric}: 사전 기준 미달")

    approval = evidence.get("human_release_approval") or {}
    if approval.get("approved") is not True or not approval.get("approved_by"):
        errors.append("사람 release 승인이 없다")
    return errors


def _artifact_errors(descriptor: Any, root: Path, label: str) -> tuple[Path | None, list[str]]:
    if not isinstance(descriptor, dict):
        return None, [f"{label}: artifact descriptor 누락"]
    relative = descriptor.get("path")
    declared = descriptor.get("sha256")
    if not isinstance(relative, str) or not relative:
        return None, [f"{label}.path: 누락"]
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None, [f"{label}.path: artifact root 밖 경로"]
    errors: list[str] = []
    if not path.is_file():
        return path, [f"{label}: 파일 없음 {path}"]
    if not isinstance(declared, str) or not SHA256_RE.fullmatch(declared.lower()):
        errors.append(f"{label}.sha256: 64자리 hex가 아님")
    elif sha256_file(path) != declared.lower():
        errors.append(f"{label}: 선언 SHA-256과 실제 파일 불일치")
    errors.extend(_check_sidecar(path))
    return path, errors


def count_manifest_mismatches(manifest: dict[str, Any], root: Path) -> int:
    """manifest가 가리키는 산출물/config 바이트를 실제로 다시 해시한다.

    evidence가 ``0``이라고 주장한 값만 믿지 않는다. 경로가 root 밖이거나 출력이 비어
    있어도 한 건의 mismatch로 센다.
    """
    mismatches = 0
    seen: set[str] = set()
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        return 1
    root = root.resolve()
    for row in documents:
        if not isinstance(row, dict):
            mismatches += 1
            continue
        sha12 = row.get("sha12")
        if not isinstance(sha12, str) or not sha12 or sha12 in seen:
            mismatches += 1
        else:
            seen.add(sha12)
        outputs = row.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            mismatches += 1
            continue
        for output in outputs.values():
            if not isinstance(output, dict):
                mismatches += 1
                continue
            relative, recorded = output.get("path"), output.get("sha256")
            if not isinstance(relative, str) or not isinstance(recorded, str):
                mismatches += 1
                continue
            path = (root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError:
                mismatches += 1
                continue
            if not path.is_file() or not SHA256_RE.fullmatch(recorded.lower()):
                mismatches += 1
            elif sha256_file(path) != recorded.lower():
                mismatches += 1
    for name, recorded in (manifest.get("config") or {}).items():
        path = root / "config" / str(name)
        if not path.is_file() or not isinstance(recorded, str) or not SHA256_RE.fullmatch(recorded.lower()):
            mismatches += 1
        elif sha256_file(path) != recorded.lower():
            mismatches += 1
    return mismatches


def verify_evidence(registry_path: Path, evidence_path: Path, artifact_root: Path) -> list[str]:
    registry, errors = load_registry(registry_path)
    if errors:
        return errors
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"evidence 읽기 실패: {exc}"]
    generation_id = evidence.get("generation_id")
    if generation_status(registry, str(generation_id)) == "not_eligible_retroactively":
        return [f"{generation_id}: not_eligible_retroactively"]
    actual_registry_hash = sha256_file(registry_path)
    if evidence.get("policy_id") != registry["policy_id"]:
        errors.append("evidence.policy_id 불일치")
    if evidence.get("registry_sha256") != actual_registry_hash:
        errors.append("evidence.registry_sha256 불일치")

    manifest_path, artifact_errors = _artifact_errors(evidence.get("manifest"), artifact_root, "manifest")
    errors.extend(artifact_errors)
    manifest_built_at: datetime | None = None
    computed_manifest_mismatches: int | None = None
    if manifest_path and not artifact_errors:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"manifest 읽기 실패: {exc}")
        else:
            manifest_built_at = _timestamp(
                (manifest.get("built_at_range") or {}).get("min"),
                "manifest.built_at_range.min",
                errors,
            )
            rows = manifest.get("documents")
            if not isinstance(rows, list) or len(rows) != registry["machine_gates"]["document_count"]["value"]:
                errors.append("manifest.documents 수가 사전 기준과 다르다")
            computed_manifest_mismatches = count_manifest_mismatches(manifest, artifact_root)

    registered_at = _timestamp(registry.get("registered_at"), "registered_at", errors)
    if manifest_built_at and registered_at and manifest_built_at < registered_at:
        errors.append("target manifest가 registry보다 먼저 생성되어 소급 적용할 수 없다")

    machine_results = evidence.get("machine_results") or {}
    if computed_manifest_mismatches is not None and machine_results.get("manifest_mismatch_count") != computed_manifest_mismatches:
        errors.append(
            "machine_results.manifest_mismatch_count가 실제 재해시 결과와 다르다 "
            f"({machine_results.get('manifest_mismatch_count')!r} != {computed_manifest_mismatches})"
        )
    errors.extend(evaluate_machine_gates(registry, machine_results))
    errors.extend(evaluate_external_summary(registry, evidence))

    d5 = evidence.get("d5_human_table_quality") or {}
    _, d5_errors = _artifact_errors(d5.get("artifact"), artifact_root, "D5 artifact")
    errors.extend(d5_errors)
    binding = evidence.get("quality_threshold_binding") or {}
    binding_path, binding_errors = _artifact_errors(binding.get("artifact"), artifact_root, "threshold binding")
    errors.extend(binding_errors)
    if binding_path and not binding_errors:
        try:
            frozen = json.loads(binding_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"threshold binding 읽기 실패: {exc}")
        else:
            if frozen.get("policy_id") != registry["policy_id"]:
                errors.append("threshold binding.policy_id 불일치")
            if frozen.get("registry_sha256") != actual_registry_hash:
                errors.append("threshold binding.registry_sha256 불일치")
            if frozen.get("generation_id") != generation_id:
                errors.append("threshold binding.generation_id 불일치")
            bound_at = _timestamp(frozen.get("registered_at"), "threshold binding.registered_at", errors)
            if bound_at and manifest_built_at and bound_at >= manifest_built_at:
                errors.append("threshold binding은 target manifest보다 먼저 동결되어야 한다")
            if frozen.get("thresholds") != binding.get("thresholds"):
                errors.append("evidence threshold와 동결 artifact가 다르다")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify prospective D4 preprocessing preregistration")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--generation-id", default="")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--artifact-root", type=Path, default=ROOT)
    args = parser.parse_args()

    registry, errors = load_registry(args.registry)
    if errors:
        for error in errors:
            print(f"FAIL {error}")
        return 1
    print(f"PASS registry {registry['policy_id']} · sha256 {sha256_file(args.registry)}")
    if args.generation_id:
        status = generation_status(registry, args.generation_id)
        print(f"{args.generation_id}: {status}")
        return 2 if status == "not_eligible_retroactively" else 0
    if args.evidence:
        errors = verify_evidence(args.registry, args.evidence, args.artifact_root)
        for error in errors:
            print(f"FAIL {error}")
        if errors:
            return 1
        print("PASS prospective D4 candidate evidence")
    else:
        print("s7_hybrid-table-v1: not_eligible_retroactively")
        print("next extraction generation: required_external D5/threshold binding before evaluation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
