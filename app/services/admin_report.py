"""관리자 대시보드 요약 보고서 PDF 생성 (Phase 14).

현재 대시보드가 보여주는 것과 같은 데이터(준비상태·코호트 표본·이벤트·지식갭)를 요약해 PDF로
만든다. 한글은 시스템 폰트(맑은 고딕)를 임베딩하며, 폰트가 없으면 조용히 깨진 글자로 만들지
않고 ConfigError로 실패한다(무폴백). 브랜드명은 config 단일 소스에서 읽는다.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ConfigError
from db.sqlite_legacy.models import KnowledgeGap, RunEvent
from db.postgres.ops_repository import PgOpsStore

def _register_fonts():
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    settings = get_settings()
    reg, bold = settings.PDF_FONT_REGULAR, settings.PDF_FONT_BOLD
    if not reg.exists() or not bold.exists():
        raise ConfigError(
            f"한글 폰트를 찾을 수 없습니다: {reg}. config PDF_FONT_REGULAR/BOLD 경로를 수정하세요."
        )
    pdfmetrics.registerFont(TTFont("Malgun", str(reg)))
    pdfmetrics.registerFont(TTFont("MalgunBd", str(bold)))
    return "Malgun", "MalgunBd"


def _collect(db: Session | PgOpsStore) -> dict:
    """대시보드와 같은 요약 데이터를 모은다."""
    from app.obs.readiness import check_readiness

    readiness = check_readiness()

    #: ★주문 요약을 **코호트 요약으로 교체했다**(2026-08-04).
    #:   보험 보고서에 "총 결제금액"이 실려 있으면 읽는 사람이 이 제품을 오해한다.
    #:   두 트랙을 나란히 싣되 **합계를 만들지 않는다** — 합치면 합성이 실제로 샌다.
    from app.adapters import demo_submission_store as demo
    from app.composition import build_cohort
    from app.core.domain.insurance import DataSource, KcdCode

    cohort: dict[str, dict] = {}
    try:
        q = build_cohort()
        kc = KcdCode(version_label="", code="", name_ko="")
        for src in (DataSource.VERIFIED_REAL, DataSource.SYNTHETIC):
            ans = q.run(kcd_code=kc, product_id="", age_band=None, data_source=src)
            cohort[src.value] = {"n": ans.stats.n, "approved": ans.stats.approved_n,
                                 "denied": ans.stats.denied_n,
                                 "min_sample": ans.stats.min_sample}
    except Exception as e:  # noqa: BLE001
        #: ★0 으로 때우지 않는다. "못 셌다"와 "0건"은 다르다.
        cohort = {"error": str(e)[:120]}
    try:
        demo_counts = demo.counts()
    except Exception as e:  # noqa: BLE001
        demo_counts = {"error": str(e)[:120]}

    if isinstance(db, PgOpsStore):
        ops_summary = db.summary()
        event_total = ops_summary["event_total"]
        event_by_kind = ops_summary["event_by_kind"]
        gap_total = ops_summary["gap_total"]
        gap_unresolved = ops_summary["gap_unresolved"]
    else:
        event_total = db.query(func.count(RunEvent.id)).scalar() or 0
        event_by_kind = (
            db.query(RunEvent.kind, func.count(RunEvent.id))
            .group_by(RunEvent.kind)
            .order_by(func.count(RunEvent.id).desc())
            .limit(8)
            .all()
        )
        gap_total = db.query(func.count(KnowledgeGap.id)).scalar() or 0
        gap_unresolved = (
            db.query(func.count(KnowledgeGap.id))
            .filter(KnowledgeGap.resolved.is_(False))
            .scalar()
            or 0
        )

    return {
        "readiness": readiness,
        "cohort": cohort,
        "demo_counts": demo_counts,
        "event_total": int(event_total),
        "event_by_kind": [(str(k), int(v)) for k, v in event_by_kind],
        "gap_total": int(gap_total),
        "gap_unresolved": int(gap_unresolved),
    }


def build_admin_report_pdf(
    db: Session | PgOpsStore, generated_at: datetime | None = None
) -> bytes:
    """관리자 요약 보고서 PDF 바이트를 만든다. generated_at 미지정 시 현재 시각."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.lib.styles import ParagraphStyle

    reg, bold = _register_fonts()
    data = _collect(db)
    brand = get_settings().BRAND_NAME
    ts = (generated_at or datetime.now()).strftime("%Y-%m-%d %H:%M")

    body = ParagraphStyle("b", fontName=reg, fontSize=10, leading=15)
    small = ParagraphStyle("s", fontName=reg, fontSize=8, textColor=colors.grey)
    h1 = ParagraphStyle("h1", fontName=bold, fontSize=18, leading=24)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=12.5, leading=18, spaceBefore=10, spaceAfter=4)

    def table(rows, widths):
        t = Table(rows, colWidths=widths, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), reg),
            ("FONTNAME", (0, 0), (-1, 0), bold),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f3640")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b2bec3")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6fa")]),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    story = []
    story.append(Paragraph(f"{brand} · 관리자 운영 요약 보고서", h1))
    story.append(Paragraph(f"생성일시 {ts} · 본 보고서는 생성 시점의 스냅샷입니다.", small))
    story.append(Spacer(1, 8))

    r = data["readiness"]
    def yn(v):
        return "정상" if v else "미준비"
    story.append(Paragraph("1. 시스템 준비 상태", h2))
    story.append(table([
        ["항목", "상태"],
        ["전체 준비(ready)", yn(r.get("ready"))],
        ["데이터베이스 테이블", yn(r.get("db_tables_ready"))],
        ["보험 조항 인덱스", yn(r.get("clause_index_ready"))],
    ], [70 * mm, 40 * mm]))

    story.append(Paragraph("2. 코호트 표본 현황 (트랙별 — 합계를 내지 않음)", h2))
    c = data["cohort"]
    if "error" in c:
        story.append(Paragraph(f"집계하지 못했습니다: {c['error']}", body))
    else:
        rows = [["트랙", "표본 n", "지급", "부지급", "최소표본"]]
        for key, label in (("verified_real", "실제(검증됨)"), ("synthetic", "합성(데모)")):
            v = c.get(key, {})
            rows.append([label, str(v.get("n", "-")), str(v.get("approved", "-")),
                         str(v.get("denied", "-")), str(v.get("min_sample", "-"))])
        story.append(table(rows, [38 * mm, 22 * mm, 20 * mm, 22 * mm, 24 * mm]))
        story.append(Spacer(1, 3))
        #: ★수치 옆에 항상 붙는다. 표만 보고 "승인율"을 읽어 가지 않게.
        story.append(Paragraph(
            "합성 트랙은 시연용 생성 데이터이며 실제 지급 통계가 아닙니다. "
            "최소표본 미만이면 비율을 계산하지 않습니다.", body))
    dc = data["demo_counts"]
    if "error" not in dc:
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            f"합성 제출 {dc['submitted']}건 중 검수 승격 {dc['promoted']}건 "
            f"(대기 {dc['pending']}건) — 승격된 것만 표본에 들어갑니다.", body))

    story.append(Paragraph("3. 에이전트 이벤트 요약", h2))
    story.append(Paragraph(f"총 이벤트 {data['event_total']}건 (상위 종류)", body))
    if data["event_by_kind"]:
        rows = [["종류(kind)", "건수"]] + [[k, str(v)] for k, v in data["event_by_kind"]]
        story.append(Spacer(1, 3))
        story.append(table(rows, [90 * mm, 40 * mm]))

    story.append(Paragraph("4. 지식갭(운영자 확인 필요)", h2))
    story.append(Paragraph(
        f"미해결 {data['gap_unresolved']}건 / 전체 {data['gap_total']}건", body))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=20 * mm,
        title=f"{brand} 관리자 요약 보고서", author=brand,
    )
    doc.build(story)
    return buf.getvalue()


def save_admin_report(db: Session | PgOpsStore, out_dir: Path | None = None) -> Path:
    """보고서를 파일로 저장하고 경로를 반환한다(다운로드와 별개로 서버 보관용)."""
    from app.core.config import ROOT_DIR

    now = datetime.now()
    pdf = build_admin_report_pdf(db, generated_at=now)
    # 모든 문서·산출물은 docs/ 아래에 모은다(RULE.md 4장).
    out_dir = out_dir or (ROOT_DIR / "docs" / "generated_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"admin_report_{now.strftime('%Y%m%d_%H%M%S')}.pdf"
    path.write_bytes(pdf)
    return path
