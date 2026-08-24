from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "docs" / "handoff" / "19_담당자별_코드범위_및_구현현황.html"


class _Index(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.hrefs: list[str] = []
        self.external_assets: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if values.get("href"):
            self.hrefs.append(values["href"])
        if tag in {"script", "img", "link"} and values.get("src"):
            source = values["src"]
            if source.startswith(("http://", "https://", "//")):
                self.external_assets.append(source)


def _index() -> tuple[str, _Index]:
    source = HTML.read_text(encoding="utf-8")
    index = _Index()
    index.feed(source)
    return source, index


def test_ownership_brief_contains_all_contracted_people_and_boundaries() -> None:
    source, _ = _index()

    for person in ("송채영", "김지혜", "서유현", "정재희", "최연우"):
        assert person in source
    for required in (
        "From → To",
        "공동 승인",
        "주 담당 합의 필요",
        "D6 + 표 계약",
        "12 passed",
        "not_eligible_retroactively",
        "비전공자용 60초 요약",
        "데이터 수집·전처리",
        "AI 검색",
        "AI 판정·설명",
        "에이전트",
        "백엔드",
        "프론트엔드",
    ):
        assert required in source

    for term in (
        "fail-closed — 모르면 안전하게 멈추기",
        "readiness — 영업 시작 전 점검",
        "manifest — 데이터 포장 명세서",
        "rerank — 검색 결과 다시 줄 세우기",
        "DSN — 데이터베이스 접속 주소 묶음",
        "serving — 지금 실제 답변에 쓰는 버전",
        "P·R·F1 — 검색 품질을 보는 세 숫자",
        "mutation test — 일부러 고장 내 보는 안전시험",
    ):
        assert term in source


def test_ownership_brief_local_links_and_anchors_are_valid() -> None:
    _, index = _index()

    assert len(index.ids) == len(set(index.ids))
    ids = set(index.ids)
    missing_anchors = [href for href in index.hrefs if href.startswith("#") and href[1:] not in ids]
    assert not missing_anchors

    missing_paths: list[str] = []
    for href in index.hrefs:
        if href.startswith(("#", "http://", "https://", "mailto:")):
            continue
        raw_path = unquote(href.split("#", 1)[0])
        if raw_path and not (HTML.parent / raw_path).resolve().exists():
            missing_paths.append(href)
    assert not missing_paths
    assert not index.external_assets


def test_ownership_brief_has_responsive_print_and_filter_contract() -> None:
    source, _ = _index()

    assert "@media (max-width:" in source
    assert "@media print" in source
    assert 'id="ownerFilter"' in source
    assert "data-owners" in source
    assert 'aria-live="polite"' in source
