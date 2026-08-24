"""글꼴 매핑이 없는 PDF의 글자를 **되살린다** — OCR 없이.

핵심 — 왜 이 모듈이 있나 (2026-08-11)

    문서 확정이 막힌 283건 중 23건은 표지가 이렇게 나왔다 —

        ⯝G G G 㵜
        ⱨⵤ␭ٻ⊬䟸㐘㋄㢌⨀⽸⸨㣙⸨䜌ڃᵥ㐔䝉ڄڃڣۄڌڒڋڌڄ

    처음엔 「추출 실패라 자동으로 못 푼다」고 적었다. **게으른 결론이었다.**

    실제로는 CID 글꼴에 `ToUnicode` 가 없어 PyMuPDF 가 **글리프 번호를 그대로**
    내놓은 것이다. 글리프 번호는 유니코드에서 **일정한 거리**만큼 떨어져 있다 —

        무 U+BB34  ←  ⱨ U+2C68   차이 0x8ECC
        배 U+BC30  ←  ⵤ U+2D64   차이 0x8ECC
        당 U+B2F9  ←  ␭ U+242D   차이 0x8ECC

    참고 — 즉 **계산으로 되돌릴 수 있다.** OCR 도, 재수집도 필요 없다.

참고 — 위험 — 오프셋을 코드에 박지 않는다

    `0x8ECC` 는 **그 글꼴의 값**이다. 문서마다 다르고, **한 문서 안에서도 여럿**이다
    (글꼴이 여러 개면 거리도 여러 개다). 그래서 박지 않고 문서에서 찾는다.

핵심 — 위험 — 「한글이 많이 나오는 거리」로 찾으면 **틀린다**

    처음 그렇게 만들었다. 실측에서 오프셋 `0xA585` 가 한글 **68%** 로 뽑혔는데
    결과는 「텢G G G 㵜 | 퇭틩즲가젱…」 이었다. 한글 자리로 옮기기만 하면
    비율은 오른다 — **뜻이 되지 않아도.** 정답은 `0x8ECC` 였다.

    참고 — 대리 지표가 진짜 기준을 밀어낸 것이다. 그래서 **약관이라면 반드시 나오는
      낱말**(보험·약관·계약·니다…)로 채점한다. 우연한 거리로는 이 낱말들이
      한꺼번에 나올 수 없다.

참고 — 이건 **읽기 보조**이지 원문이 아니다

    되살린 글자는 식별·검색에 쓴다. 화면에 인용할 원문으로 쓰려면
    사람이 한 번 확인해야 한다. `recover()` 가 근거(오프셋·한글 비율)를
    함께 돌려주는 것은 그 확인을 위해서다.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass

#: 한글 완성형. 「되살아났다」의 판정 기준.
_HANGUL = range(0xAC00, 0xD7A4)

#: 글리프 번호가 놓이는 자리. 여기 글자가 많으면 매핑이 깨진 것이다.
_GLYPH_ZONE = range(0x0100, 0xA000)

#: 핵심 — **한글 비율로 채점하지 않는다.**
#:
#:   처음엔 「한글이 가장 많이 나오는 거리」를 골랐다. 실측에서 오프셋 `0xA585` 가
#:   한글 **68%** 로 뽑혔는데 결과는 「텢G G G 㵜 | 퇭틩즲가젱…」 이었다.
#:   한글 자리로 옮기기만 하면 비율은 오른다 — **뜻이 되지 않아도.**
#:   정답은 `0x8ECC`(「무배당 노후실손의료비보장보험」)였다.
#:
#:   참고 — 그래서 **약관이라면 반드시 나오는 낱말**로 채점한다. 우연한 거리로는
#:     이 낱말들이 한꺼번에 나올 수 없다.
_ANCHORS = ("보험", "약관", "계약", "보장", "회사", "지급", "니다", "합니다",
            "경우", "제1조", "특별약관", "가입")

#: 이만큼은 닻 낱말이 나와야 「되살아났다」고 본다.
_MIN_ANCHOR_HITS = 4

#: 위험 — 줄 하나를 바꾸려면 닻이 **이만큼 더** 나와야 한다. 1 로 두면 틀린 거리로 옮겨도
#:   우연히 하나가 걸려 통과한다 — 실측으로 그렇게 뚫렸다.
_LINE_MARGIN = 2


@dataclass(frozen=True)
class Recovery:
    """되살린 결과와 **그 근거**."""

    text: str
    offset: int          #: 찾아낸 거리. 0 이면 되살리지 않은 것이다.
    hangul_ratio: float  #: 되살린 뒤 한글 비율
    recovered: bool


def looks_broken(text: str, *, min_chars: int = 200) -> bool:
    """글꼴 매핑이 깨진 글인가.

    핵심 — **짧은 글로는 판단하지 않는다.** 표지 한 줄이 「- 1 -」인 것과
      104쪽이 통째로 깨진 것은 다르다. 짧으면 `False` 를 낸다 —
      「모른다」를 「깨졌다」로 바꾸지 않는다.
    """
    body = [c for c in (text or "") if not c.isspace()]
    if len(body) < min_chars:
        return False
    hangul = sum(1 for c in body if ord(c) in _HANGUL)
    glyphy = sum(1 for c in body if ord(c) in _GLYPH_ZONE)
    #: 한글이 거의 없는데 글리프 자리 글자가 많으면 깨진 것이다.
    return hangul / len(body) < 0.02 and glyphy / len(body) > 0.3


def _shift(text: str, offset: int) -> str:
    """한글 자리로 **옮겨지는 글자만** 옮긴다.

    참고 — 라틴·숫자는 글꼴이 달라 거리가 다르다. 억지로 옮기면 **없던 글자를 만든다.**
    """
    return "".join(chr(o + offset) if (o := ord(c)) + offset in _HANGUL else c
                   for c in text or "")


def _anchor_hits(text: str) -> int:
    """약관이라면 나올 낱말이 몇 개나 보이나. **이것이 채점 기준이다.**"""
    return sum(text.count(a) for a in _ANCHORS)


def find_offsets(text: str, *, sample: int = 20000, top: int = 6) -> list[int]:
    """이 문서에서 쓸 만한 거리들. **여럿일 수 있다.**

    참고 — 위험 — 한 문서에 글꼴이 여럿이면 거리도 여럿이다. 실측 — 삼성화재 약관 한 편에서
      `0x9F56` 으로는 「보험약관은중요합니다」가, `0x8DAA` 로는
      「실손의료보험은사람의질병또는상해로인한손해」가 되살아났다.
      **하나만 고르면 나머지 글꼴은 그대로 깨진 채 남는다.**
    """
    #: 참고 — 위험 — **글 앞부분만 보지 않는다.** 두 번째 글꼴은 뒤쪽에만 나올 수 있고,
    #:   앞부분만 표본으로 삼으면 그 거리는 후보에 아예 오르지 못한다(시험이 잡았다).
    head = (text or "")[:sample]
    body = [ord(c) for c in text or "" if not c.isspace()]
    freq = collections.Counter(c for c in body if c in _GLYPH_ZONE)
    if not freq:
        return []
    cand = collections.Counter()
    for code, n in freq.most_common(120):
        #: 흔한 한글로 옮겨 보는 거리들을 후보로 모은다.
        for target in (0xAC00, 0xB098, 0xB2E4, 0xC758, 0xC5D0, 0xB294, 0xC744, 0xBCF4):
            cand[target - code] += n
    scored = []
    for off, _ in cand.most_common(160):
        hits = _anchor_hits(_shift(head, off)) + _anchor_hits(_shift(text or "", off))
        if hits >= _MIN_ANCHOR_HITS:
            scored.append((hits, off))
    scored.sort(reverse=True)
    return [off for _, off in scored[:top]]


def recover(text: str, *, offsets: list[int] | None = None) -> Recovery:
    """깨진 글을 되살린다. **되살아나지 않으면 원문을 그대로 돌려준다.**

    참고 — 줄마다 **가장 잘 되살아나는 거리**를 고른다. 한 문서에 글꼴이 여럿이기 때문이다.
      어떤 거리도 원문보다 낫지 않으면 그 줄은 **건드리지 않는다.**
    """
    offs = find_offsets(text) if offsets is None else offsets
    if not offs:
        return Recovery(text or "", 0, 0.0, False)

    lines, used = [], collections.Counter()
    for line in (text or "").splitlines():
        base = _anchor_hits(line)
        #: 위험·핵심 — 한 낱말 더 나온다고 바꾸지 않는다. 틀린 거리로 옮겨도 우연히 하나는
        #:   걸린다 — 실제로 그렇게 「띴 승계땽鴀띘…」 이 통과했다(시험이 잡았다).
        best, best_hits, best_off = line, base + _LINE_MARGIN - 1, 0
        for off in offs:
            cand = _shift(line, off)
            hits = _anchor_hits(cand)
            if hits > best_hits:
                best, best_hits, best_off = cand, hits, off
        if best_off:
            used[best_off] += 1
        lines.append(best)
    fixed = "\n".join(lines)

    #: 위험 — 되살아났다고 **말할 수 있을 때만** 되살렸다고 한다.
    if _anchor_hits(fixed) - _anchor_hits(text or "") < _MIN_ANCHOR_HITS:
        return Recovery(text or "", 0, 0.0, False)
    body = [c for c in fixed if not c.isspace()]
    ratio = (sum(1 for c in body if ord(c) in _HANGUL) / len(body)) if body else 0.0
    return Recovery(fixed, used.most_common(1)[0][0] if used else offs[0], ratio, True)


__all__ = ["Recovery", "find_offsets", "looks_broken", "recover"]
