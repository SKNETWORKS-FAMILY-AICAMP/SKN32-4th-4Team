"""전처리 불변 manifest의 **파일 바이트** 지문 회귀 테스트."""

from __future__ import annotations

import json
import sys

from scripts.extract import build_manifest as M


def _minimal_manifest() -> str:
    return json.dumps(
        {
            "documents": [],
            "config": {},
            "code": {"git_commit": "", "dirty_sha256": ""},
        },
        ensure_ascii=False,
        indent=1,
        sort_keys=True,
    )


def test_sidecar_hashes_actual_file_bytes(tmp_path):
    out = tmp_path / "manifest_s6.json"
    body = '{\n "한글": "값"\n}'

    M.write_manifest_with_sidecar(out, body)

    recorded = M.manifest_sidecar_path(out).read_text(encoding="ascii").strip()
    assert recorded == M.sha256_file(out)
    assert b"\r\n" not in out.read_bytes()
    assert M.verify_manifest_sidecar(out) is None


def test_sidecar_mismatch_is_reported(tmp_path):
    out = tmp_path / "manifest_s6.json"
    M.write_manifest_with_sidecar(out, _minimal_manifest())
    M.manifest_sidecar_path(out).write_text("0" * 64 + "\n", encoding="ascii")

    problem = M.verify_manifest_sidecar(out)

    assert problem is not None
    assert "불일치" in problem


def test_sidecar_missing_is_reported(tmp_path):
    out = tmp_path / "manifest_s6.json"
    out.write_text(_minimal_manifest(), encoding="utf-8")

    assert M.verify_manifest_sidecar(out) == "manifest_s6.sha256 없음"


def test_verify_command_fails_on_bad_sidecar(tmp_path, monkeypatch):
    out = tmp_path / "manifest_s6.json"
    M.write_manifest_with_sidecar(out, _minimal_manifest())
    M.manifest_sidecar_path(out).write_text("f" * 64 + "\n", encoding="ascii")

    monkeypatch.setattr(M, "_ROOT", tmp_path)
    monkeypatch.setattr(M, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(M, "_CONFIG", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_manifest", "--schema", "s6", "--verify"])

    assert M.main() == 1


def test_config_state_includes_only_explicit_preprocess_provenance(tmp_path, monkeypatch):
    (tmp_path / "accepted_extraction.json").write_text('{"clause_tag":"s6"}', encoding="utf-8")
    (tmp_path / "generation_profiles.json").write_text('{"schema":1}', encoding="utf-8")
    (tmp_path / "precheck_mode.json").write_text('{"mode":"machine"}', encoding="utf-8")
    (tmp_path / "ocr_shadow48_bench.json").write_text('{"schema":6}', encoding="utf-8")
    monkeypatch.setattr(M, "_CONFIG", tmp_path)

    state = M.config_state()

    assert "accepted_extraction.json" not in state
    assert "generation_profiles.json" not in state
    assert "precheck_mode.json" not in state
    assert set(state) == {"ocr_shadow48_bench.json"}


def test_verify_ignores_legacy_recorded_non_preprocess_config(tmp_path, monkeypatch, capsys):
    rec = json.loads(_minimal_manifest())
    rec["config"] = {"generation_profiles.json": "0" * 64}
    out = tmp_path / "manifest_s6.json"
    M.write_manifest_with_sidecar(
        out, json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True)
    )

    monkeypatch.setattr(M, "_ROOT", tmp_path)
    monkeypatch.setattr(M, "_OUT_DIR", tmp_path)
    monkeypatch.setattr(M, "_CONFIG", tmp_path)
    monkeypatch.setattr(sys, "argv", ["build_manifest", "--schema", "s6", "--verify"])

    assert M.main() == 0
    assert "전처리 입력이 아니라 검증에서 제외" in capsys.readouterr().out
