from __future__ import annotations

from typing import Any

import pytest

from scripts.eval import bench_embedders as bench


@pytest.mark.parametrize(
    ("argv", "renderer", "expected_kwargs"),
    [
        (["--report", "--md"], "report", {"dtype": "float16", "md": True}),
        (
            ["--report", "--md", "--dtype", "4bit"],
            "report",
            {"dtype": "4bit", "md": True},
        ),
        (["--prefixes", "--md"], "prefix_table", {"md": True}),
        (["--repro", "--md"], "repro_commands", {"md": True}),
    ],
)
def test_output_modes_exit_zero_without_falling_through_to_model(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    renderer: str,
    expected_kwargs: dict[str, Any],
) -> None:
    """보고 전용 모드는 출력 뒤 실제 모델 측정 경로로 내려가지 않는다."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_renderer(*args: Any, **kwargs: Any) -> None:
        if args:
            # report()는 dtype을 위치 인자로 받는다. 비교하기 쉽게 이름을 붙인다.
            kwargs = {"dtype": args[0], **kwargs}
        calls.append((renderer, kwargs))

    def unexpected_run(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("출력 모드가 실제 모델 측정 경로로 내려갔습니다")

    monkeypatch.setattr(bench, renderer, fake_renderer)
    monkeypatch.setattr(bench, "run", unexpected_run)

    assert bench.main(argv) == 0
    assert calls == [(renderer, expected_kwargs)]


def test_model_mode_still_runs_the_requested_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    """보고 모드의 조기 반환이 실제 측정 모드를 막지 않는다."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run(model: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((model, kwargs))
        return {"proviso_blind_count": 0}

    monkeypatch.setattr(bench, "run", fake_run)

    assert bench.main(["--model", "example/model", "--batch", "3", "--device", "cpu"]) == 0
    assert calls == [
        (
            "example/model",
            {
                "q_prefix": "",
                "d_prefix": "",
                "batch": 3,
                "device": "cpu",
                "no_fp16": False,
                "quant": "",
                "max_seq": 0,
                "probes_only": False,
            },
        )
    ]


@pytest.mark.parametrize("argv", [[], ["--probes-only"]])
def test_measurement_options_without_model_still_fail(argv: list[str]) -> None:
    """출력 모드가 아닌 실행에는 모델이 계속 필수다."""
    with pytest.raises(SystemExit) as exc:
        bench.main(argv)
    assert exc.value.code == 2
