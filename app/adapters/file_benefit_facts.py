"""사람 승인 S7.1 자기부담금 산출물을 fail-closed로 읽는다."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.core.domain.benefit_facts import SelfPayFact
from app.core.errors import InfraError


class FileSelfPayFactSource:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._cache: dict[str, list[SelfPayFact]] | None = None

    @staticmethod
    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8-sig"))

    @staticmethod
    def _jsonl(path: Path) -> list[dict]:
        return [
            json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]

    def _load(self) -> dict[str, list[SelfPayFact]]:
        accepted_path = self.root / "config/accepted_s7_1_facts.json"
        accepted = self._json(accepted_path)
        if (accepted.get("release_state") != "accepted"
                or accepted.get("serving_eligible") is not True
                or accepted.get("citation_eligible") is not True):
            raise InfraError("S7.1 자기부담금 사실이 운영 승인 상태가 아닙니다.")

        fact_dir = self.root / "data/work/s7_1_approved_facts"
        manifest_path = fact_dir / "manifest.json"
        if self._sha(manifest_path) != accepted.get("provenance", {}).get("fact_manifest_sha256"):
            raise InfraError("자기부담금 승인 설정과 fact manifest 해시가 다릅니다.")
        manifest = self._json(manifest_path)
        facts_path = fact_dir / "approved_facts.jsonl"
        expected_hash = manifest.get("artifacts", {}).get("approved_facts", {}).get("sha256")
        if self._sha(facts_path) != expected_hash:
            raise InfraError("자기부담금 fact 파일 해시가 manifest와 다릅니다.")

        facts = self._jsonl(facts_path)
        expected_count = accepted.get("approval", {}).get("approved_facts")
        if len(facts) != expected_count:
            raise InfraError(f"자기부담금 승인 건수가 다릅니다: {len(facts)} != {expected_count}")
        if any(
            row.get("approval") != "human_pattern_approved"
            or row.get("serving_eligible") is not True
            or row.get("citation_eligible") is not True
            or row.get("inferred") is not False
            for row in facts
        ):
            raise InfraError("승인되지 않았거나 추론된 자기부담금 사실이 섞였습니다.")

        preprocess = self._json(self.root / "data/manifests/preprocess/manifest_s6.json")
        full_by_prefix: dict[str, list[str]] = {}
        for row in preprocess.get("documents") or []:
            full = str(row.get("input_sha256") or "")
            if len(full) == 64:
                full_by_prefix.setdefault(full[:12], []).append(full)

        result: dict[str, list[SelfPayFact]] = {}
        for row in facts:
            prefix = str(row.get("document_sha12") or "")
            candidates = list(dict.fromkeys(full_by_prefix.get(prefix, [])))
            if len(candidates) != 1:
                raise InfraError(f"자기부담금 사실의 전체 문서 SHA를 유일하게 찾지 못했습니다: {prefix}")
            full = candidates[0]
            result.setdefault(full, []).append(SelfPayFact(
                policy_version_sha=full,
                candidate_id=str(row["candidate_id"]),
                plan=str(row.get("plan") or ""),
                services=tuple(row.get("service") or ()),
                institution=str(row.get("institution") or ""),
                coverage=tuple(row.get("coverage") or ()),
                formula=str(row.get("amount_formula") or ""),
                amount_tokens=tuple(row.get("amount_tokens") or ()),
                rate_tokens=tuple(row.get("rate_tokens") or ()),
                page=int(row.get("page_1based") or 0),
                content_hash=str(row.get("content_hash") or ""),
                approval=str(row.get("approval") or ""),
            ))
        return result

    def load_for_policy(self, policy_version_sha: str) -> list[SelfPayFact]:
        if self._cache is None:
            self._cache = self._load()
        return list(self._cache.get(policy_version_sha.lower(), ()))


__all__ = ["FileSelfPayFactSource"]
