"""PDF daily report generator using ReportLab.

Produces a professional, print-friendly report covering:
  1. Executive summary
  2. Recommendations summary table
  3. Detailed per-ticker recommendations with signals
  4. Market news
  5. Upcoming catalysts
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import (
    EarningsCalendar,
    MarketNews,
    NewsTickerRelevance,
    Recommendation,
    UniverseStock,
    Watchlist,
    WatchlistDailySnapshot,
)

logger = logging.getLogger(__name__)

# ── Action colors ────────────────────────────────────────────────────────────

ACTION_COLORS = {
    "STRONG_BUY": colors.HexColor("#2ea043"),
    "BUY": colors.HexColor("#56d364"),
    "HOLD": colors.HexColor("#d29922"),
    "SELL": colors.HexColor("#f85149"),
    "STRONG_SELL": colors.HexColor("#da3633"),
}

ACTION_TEXT_COLORS = {
    "STRONG_BUY": colors.white,
    "BUY": colors.HexColor("#1a1a2e"),
    "HOLD": colors.HexColor("#1a1a2e"),
    "SELL": colors.white,
    "STRONG_SELL": colors.white,
}

HEADER_BG = colors.HexColor("#1a1a2e")
ALT_ROW = colors.HexColor("#f4f4f8")
SUMMARY_BG = colors.HexColor("#f0f0f5")
GREEN = colors.HexColor("#2ea043")
RED = colors.HexColor("#da3633")
AMBER = colors.HexColor("#d29922")


def _f(val, fmt=",.2f", prefix="$") -> str:
    """Format a Decimal/float value safely."""
    if val is None:
        return "—"
    try:
        return f"{prefix}{float(val):{fmt}}"
    except (ValueError, TypeError):
        return "—"


def _pct(val) -> str:
    if val is None:
        return "—"
    try:
        return f"{float(val):.1f}"
    except (ValueError, TypeError):
        return "—"


# ── Styles ───────────────────────────────────────────────────────────────────

def _build_styles():
    ss = getSampleStyleSheet()
    styles = {
        "title": ParagraphStyle(
            "ReportTitle", parent=ss["Title"], fontSize=22,
            spaceAfter=4, alignment=TA_CENTER,
        ),
        "subtitle": ParagraphStyle(
            "ReportSubtitle", parent=ss["Normal"], fontSize=13,
            textColor=colors.gray, alignment=TA_CENTER, spaceAfter=16,
        ),
        "h2": ParagraphStyle(
            "SectionHeader", parent=ss["Heading2"], fontSize=15,
            spaceBefore=18, spaceAfter=8, textColor=HEADER_BG,
        ),
        "h3": ParagraphStyle(
            "TickerHeader", parent=ss["Heading3"], fontSize=12,
            spaceBefore=12, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "ReportBody", parent=ss["Normal"], fontSize=9.5,
            leading=13, spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "SmallText", parent=ss["Normal"], fontSize=8,
            leading=10, textColor=colors.gray,
        ),
        "metric_label": ParagraphStyle(
            "MetricLabel", parent=ss["Normal"], fontSize=8,
            textColor=colors.gray, alignment=TA_CENTER,
        ),
        "metric_value": ParagraphStyle(
            "MetricValue", parent=ss["Normal"], fontSize=11,
            alignment=TA_CENTER, fontName="Helvetica-Bold",
        ),
        "cell": ParagraphStyle(
            "TableCell", parent=ss["Normal"], fontSize=8.5,
            leading=11,
        ),
        "cell_center": ParagraphStyle(
            "TableCellCenter", parent=ss["Normal"], fontSize=8.5,
            leading=11, alignment=TA_CENTER,
        ),
        "cell_white": ParagraphStyle(
            "TableCellWhite", parent=ss["Normal"], fontSize=8.5,
            leading=11, textColor=colors.white,
        ),
        "cell_center_white": ParagraphStyle(
            "TableCellCenterWhite", parent=ss["Normal"], fontSize=8.5,
            leading=11, alignment=TA_CENTER, textColor=colors.white,
        ),
    }
    return styles


# Full content width with 0.75" margins on letter
CONTENT_W = letter[0] - 1.5 * inch  # 7.0 inches


# ── Page templates ───────────────────────────────────────────────────────────

def _header_footer(canvas, doc, report_date: date):
    canvas.saveState()
    # Header
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.gray)
    canvas.drawString(
        doc.leftMargin, doc.pagesize[1] - 0.5 * inch,
        "EdgeFlow Daily Analysis",
    )
    canvas.drawRightString(
        doc.pagesize[0] - doc.rightMargin,
        doc.pagesize[1] - 0.5 * inch,
        report_date.strftime("%B %d, %Y"),
    )
    canvas.setStrokeColor(colors.HexColor("#cccccc"))
    canvas.line(
        doc.leftMargin, doc.pagesize[1] - 0.55 * inch,
        doc.pagesize[0] - doc.rightMargin, doc.pagesize[1] - 0.55 * inch,
    )
    # Footer
    canvas.drawCentredString(
        doc.pagesize[0] / 2, 0.4 * inch,
        f"Page {doc.page}",
    )
    canvas.drawRightString(
        doc.pagesize[0] - doc.rightMargin, 0.4 * inch,
        "Generated by EdgeFlow",
    )
    canvas.restoreState()


# ── Data gathering ───────────────────────────────────────────────────────────

async def _gather_data(report_date: date, session: AsyncSession) -> dict:
    """Fetch all data needed for the report."""
    # Watchlist — use snapshot for historical, active for today
    if report_date == date.today():
        wl_result = await session.execute(
            select(Watchlist)
            .where(Watchlist.is_active.is_(True))
            .options(selectinload(Watchlist.sector))
        )
        watchlist = list(wl_result.scalars().all())
    else:
        snap_result = await session.execute(
            select(WatchlistDailySnapshot)
            .where(WatchlistDailySnapshot.snapshot_date == report_date)
        )
        watchlist = list(snap_result.scalars().all())

    # Recommendations
    rec_result = await session.execute(
        select(Recommendation)
        .where(Recommendation.recommendation_date == report_date)
        .options(selectinload(Recommendation.suggested_options))
        .order_by(Recommendation.conviction_score.desc())
    )
    recommendations = list(rec_result.scalars().all())

    # Company names
    name_result = await session.execute(select(UniverseStock.ticker, UniverseStock.company_name))
    company_names = {r.ticker: r.company_name or r.ticker for r in name_result.all()}

    # News — last 24h from report date
    news_start = datetime(report_date.year, report_date.month, report_date.day)
    news_end = news_start + timedelta(days=1)
    news_result = await session.execute(
        select(MarketNews)
        .where(
            MarketNews.published_at >= news_start,
            MarketNews.published_at < news_end,
        )
        .options(selectinload(MarketNews.ticker_relevances))
        .order_by(MarketNews.published_at.desc())
        .limit(30)
    )
    news = list(news_result.scalars().unique().all())

    # Catalysts within 14 days of report_date
    cat_result = await session.execute(
        select(EarningsCalendar)
        .where(EarningsCalendar.earnings_date.between(
            report_date, report_date + timedelta(days=14)
        ))
        .order_by(EarningsCalendar.earnings_date.asc())
    )
    catalysts = list(cat_result.scalars().all())

    return {
        "watchlist": watchlist,
        "recommendations": recommendations,
        "company_names": company_names,
        "news": news,
        "catalysts": catalysts,
    }


# ── Section builders ─────────────────────────────────────────────────────────

def _build_summary(data: dict, report_date: date, styles: dict) -> list:
    """Executive summary section."""
    recs = data["recommendations"]
    watchlist = data["watchlist"]
    news = data["news"]
    catalysts = data["catalysts"]

    elements = []

    # Title
    formatted_date = report_date.strftime("%A, %B %d, %Y")
    elements.append(Paragraph("EdgeFlow Daily Analysis Report", styles["title"]))
    elements.append(Paragraph(formatted_date, styles["subtitle"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#cccccc")))
    elements.append(Spacer(1, 12))

    # Count sectors
    sectors = set()
    for w in watchlist:
        s = getattr(w, "sector", None)
        if s and hasattr(s, "name"):
            sectors.add(s.name)
        elif hasattr(w, "sector") and isinstance(w.sector, str):
            sectors.add(w.sector)

    buy_side = sum(1 for r in recs if r.action in ("STRONG_BUY", "BUY"))
    sell_side = sum(1 for r in recs if r.action in ("SELL", "STRONG_SELL"))
    hold_side = sum(1 for r in recs if r.action == "HOLD")
    avg_conv = (
        sum(float(r.conviction_score or 0) for r in recs) / len(recs)
        if recs else 0
    )
    top_pick = recs[0] if recs else None

    # Summary metrics as a table
    metrics = [
        ("Watchlist", f"{len(watchlist)} stocks"),
        ("Buy / Hold / Sell", f"{buy_side} / {hold_side} / {sell_side}"),
        ("Avg Conviction", f"{avg_conv:.1f}"),
        ("News Articles", str(len(news))),
        ("Catalysts (14d)", str(len(catalysts))),
    ]
    if top_pick:
        cn = data["company_names"].get(top_pick.ticker, top_pick.ticker)
        metrics.insert(3, (
            "Top Pick",
            f"{top_pick.ticker} — {top_pick.action.replace('_', ' ')} ({_pct(top_pick.conviction_score)})",
        ))

    metric_data = [[
        Paragraph(f"<b>{label}</b>", styles["body"]),
        Paragraph(value, styles["body"]),
    ] for label, value in metrics]

    t = Table(metric_data, colWidths=[2.0 * inch, CONTENT_W - 2.0 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SUMMARY_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))

    return elements


def _build_rec_summary_table(data: dict, styles: dict) -> list:
    """Recommendations summary table."""
    recs = data["recommendations"]
    if not recs:
        return [
            Paragraph("Recommendation Summary", styles["h2"]),
            Paragraph("No recommendations for this date.", styles["body"]),
        ]

    elements = [Paragraph("Recommendation Summary", styles["h2"])]

    header = [
        Paragraph("<b>Ticker</b>", styles["cell_center_white"]),
        Paragraph("<b>Action</b>", styles["cell_center_white"]),
        Paragraph("<b>Conviction</b>", styles["cell_center_white"]),
        Paragraph("<b>Signals</b>", styles["cell_center_white"]),
        Paragraph("<b>Price</b>", styles["cell_center_white"]),
        Paragraph("<b>Target</b>", styles["cell_center_white"]),
        Paragraph("<b>Risk</b>", styles["cell_center_white"]),
    ]

    rows = [header]
    for r in recs:
        action_label = r.action.replace("_", " ")
        rows.append([
            Paragraph(f"<b>{r.ticker}</b>", styles["cell_center"]),
            Paragraph(action_label, styles["cell_center"]),
            Paragraph(_pct(r.conviction_score), styles["cell_center"]),
            Paragraph(str(r.signal_count or 0), styles["cell_center"]),
            Paragraph(_f(r.current_price), styles["cell_center"]),
            Paragraph(_f(r.target_price), styles["cell_center"]),
            Paragraph(r.risk_level or "—", styles["cell_center"]),
        ])

    col_w = [0.9 * inch, 1.2 * inch, 1.0 * inch, 0.7 * inch, 1.0 * inch, 1.0 * inch, 1.2 * inch]  # = 7.0"
    t = Table(rows, colWidths=col_w, repeatRows=1)

    # Build style commands
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
    ]

    # Action column coloring + alternating rows
    for i, r in enumerate(recs, start=1):
        bg = ACTION_COLORS.get(r.action, colors.white)
        fg = ACTION_TEXT_COLORS.get(r.action, colors.black)
        style_cmds.append(("BACKGROUND", (1, i), (1, i), bg))
        style_cmds.append(("TEXTCOLOR", (1, i), (1, i), fg))
        if i % 2 == 0:
            style_cmds.append(("BACKGROUND", (0, i), (0, i), ALT_ROW))
            for col in range(2, 7):
                style_cmds.append(("BACKGROUND", (col, i), (col, i), ALT_ROW))

    t.setStyle(TableStyle(style_cmds))
    elements.append(t)
    elements.append(Spacer(1, 12))

    return elements


def _build_rec_details(data: dict, styles: dict) -> list:
    """Detailed per-ticker recommendation sections."""
    recs = data["recommendations"]
    if not recs:
        return []

    elements = [Paragraph("Detailed Recommendations", styles["h2"])]

    for rec in recs:
        cn = data["company_names"].get(rec.ticker, "")
        action_label = rec.action.replace("_", " ")
        action_color = ACTION_COLORS.get(rec.action, colors.gray)

        # Ticker header with colored bar
        elements.append(Spacer(1, 6))
        header_text = f'<font color="#{action_color.hexval()[2:]}">\u2588\u2588</font> '
        header_text += f"<b>{rec.ticker}</b>"
        if cn and cn != rec.ticker:
            header_text += f" — {cn}"
        header_text += f'  <font color="#{action_color.hexval()[2:]}" size="10">{action_label}</font>'
        elements.append(Paragraph(header_text, styles["h3"]))

        # Metrics row
        metrics_data = [[
            Paragraph(f"<b>{_f(rec.current_price)}</b><br/><font size='7' color='gray'>Price</font>", styles["cell_center"]),
            Paragraph(f"<b>{_f(rec.target_price)}</b><br/><font size='7' color='gray'>Target</font>", styles["cell_center"]),
            Paragraph(f"<b>{_f(rec.stop_loss_price)}</b><br/><font size='7' color='gray'>Stop Loss</font>", styles["cell_center"]),
            Paragraph(f"<b>{rec.risk_level or '—'}</b><br/><font size='7' color='gray'>Risk</font>", styles["cell_center"]),
            Paragraph(f"<b>{_pct(rec.conviction_score)}</b><br/><font size='7' color='gray'>Conviction</font>", styles["cell_center"]),
        ]]
        mt = Table(metrics_data, colWidths=[CONTENT_W / 5] * 5)
        mt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), SUMMARY_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(mt)
        elements.append(Spacer(1, 8))

        # Rationale
        if rec.rationale:
            elements.append(Paragraph(rec.rationale, styles["body"]))
            elements.append(Spacer(1, 8))

        # Signals table
        signals = rec.signals or []
        if isinstance(signals, str):
            try:
                signals = json.loads(signals)
            except (json.JSONDecodeError, TypeError):
                signals = []

        if signals:
            sig_header = [
                Paragraph("<b>Signal</b>", styles["cell"]),
                Paragraph("<b>Pts</b>", styles["cell_center"]),
                Paragraph("<b>Detail</b>", styles["cell"]),
            ]
            sig_rows = [sig_header]
            for s in signals:
                pts = s.get("points", 0)
                pts_color = "green" if pts > 0 else ("red" if pts < 0 else "gray")
                pts_str = f"+{pts}" if pts > 0 else str(pts)
                sig_rows.append([
                    Paragraph(s.get("signal", ""), styles["cell"]),
                    Paragraph(f'<font color="{pts_color}"><b>{pts_str}</b></font>', styles["cell_center"]),
                    Paragraph(s.get("detail", ""), styles["cell"]),
                ])

            sig_t = Table(sig_rows, colWidths=[1.7 * inch, 0.6 * inch, CONTENT_W - 2.3 * inch], repeatRows=1)
            sig_t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeaef")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            elements.append(sig_t)
            elements.append(Spacer(1, 6))

        # Entry/exit
        if rec.entry_strategy or rec.exit_rules:
            parts = []
            if rec.entry_strategy:
                parts.append(f"<b>Entry:</b> {rec.entry_strategy}")
            if rec.exit_rules:
                parts.append(f"<b>Exit:</b> {rec.exit_rules}")
            elements.append(Paragraph(" &nbsp;|&nbsp; ".join(parts), styles["small"]))
            elements.append(Spacer(1, 4))

        # Suggested options
        options = rec.suggested_options or []
        if options:
            opt_col = CONTENT_W / 6
            opt_header = [
                Paragraph("<b>Type</b>", styles["cell_center"]),
                Paragraph("<b>Strike</b>", styles["cell_center"]),
                Paragraph("<b>Expiry</b>", styles["cell_center"]),
                Paragraph("<b>Premium</b>", styles["cell_center"]),
                Paragraph("<b>Delta</b>", styles["cell_center"]),
                Paragraph("<b>Breakeven</b>", styles["cell_center"]),
            ]
            opt_rows = [opt_header]
            for o in options:
                opt_rows.append([
                    Paragraph(o.contract_type, styles["cell_center"]),
                    Paragraph(_f(o.strike), styles["cell_center"]),
                    Paragraph(str(o.expiry), styles["cell_center"]),
                    Paragraph(_f(o.premium_estimate), styles["cell_center"]),
                    Paragraph(_pct(o.delta_estimate), styles["cell_center"]),
                    Paragraph(_f(o.breakeven_price), styles["cell_center"]),
                ])
            opt_t = Table(opt_rows, colWidths=[opt_col] * 6, repeatRows=1)
            opt_t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eaeaef")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            elements.append(Spacer(1, 2))
            elements.append(Paragraph("<b>Suggested Options</b>", styles["small"]))
            elements.append(opt_t)

        # Separator
        elements.append(Spacer(1, 4))
        elements.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#e0e0e0")))

    return elements


def _build_news(data: dict, styles: dict) -> list:
    """Market news section."""
    news = data["news"]
    if not news:
        return [
            Paragraph("Market News", styles["h2"]),
            Paragraph("No news articles for this date.", styles["body"]),
        ]

    elements = [Paragraph("Market News", styles["h2"])]

    for article in news:
        # Sentiment color
        sent = float(article.sentiment_score or 0)
        if sent > 0.3:
            sent_color = "green"
            sent_dot = "\u25cf"
        elif sent < -0.3:
            sent_color = "red"
            sent_dot = "\u25cf"
        else:
            sent_color = "#d29922"
            sent_dot = "\u25cf"

        # Related tickers
        tickers = []
        for rel in (article.ticker_relevances or []):
            tickers.append(rel.ticker)
        ticker_str = ", ".join(sorted(set(tickers))[:5]) if tickers else ""

        # Source + time
        source = article.source or ""
        pub = article.published_at
        time_str = pub.strftime("%H:%M") if pub else ""
        meta = f"{source} {time_str}".strip()

        # Headline
        headline = article.headline or ""
        hl_text = f"<b>{headline}</b>"
        if meta:
            hl_text += f'  <font size="7" color="gray">{meta}</font>'
        elements.append(Paragraph(hl_text, styles["body"]))

        # Summary + sentiment
        parts = []
        if article.summary:
            summary = article.summary[:200]
            if len(article.summary) > 200:
                summary += "..."
            parts.append(summary)

        sent_text = f'<font color="{sent_color}">{sent_dot} {sent:+.2f}</font>'
        if article.impact_level:
            sent_text += f'  <font size="7" color="gray">[{article.impact_level}]</font>'
        if ticker_str:
            sent_text += f'  <font size="7" color="gray">{ticker_str}</font>'
        parts.append(sent_text)

        elements.append(Paragraph(" — ".join(parts) if parts else "", styles["small"]))
        elements.append(Spacer(1, 4))

    return elements


def _build_catalysts(data: dict, report_date: date, styles: dict) -> list:
    """Upcoming catalysts section."""
    catalysts = data["catalysts"]
    if not catalysts:
        return []

    elements = [Paragraph("Upcoming Catalysts", styles["h2"])]

    header = [
        Paragraph("<b>Ticker</b>", styles["cell_center_white"]),
        Paragraph("<b>Earnings Date</b>", styles["cell_center_white"]),
        Paragraph("<b>Quarter</b>", styles["cell_center_white"]),
        Paragraph("<b>Consensus EPS</b>", styles["cell_center_white"]),
        Paragraph("<b>Days Until</b>", styles["cell_center_white"]),
    ]
    rows = [header]
    for c in catalysts:
        days_until = (c.earnings_date - report_date).days
        rows.append([
            Paragraph(f"<b>{c.ticker}</b>", styles["cell_center"]),
            Paragraph(str(c.earnings_date), styles["cell_center"]),
            Paragraph(c.fiscal_quarter or "—", styles["cell_center"]),
            Paragraph(_f(c.consensus_eps, ".4f"), styles["cell_center"]),
            Paragraph(str(days_until), styles["cell_center"]),
        ])

    t = Table(rows, colWidths=[1.1 * inch, 1.6 * inch, 1.2 * inch, 1.5 * inch, 1.6 * inch], repeatRows=1)  # = 7.0"
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    # Alternating rows
    for i in range(1, len(rows)):
        if i % 2 == 0:
            t._argW  # just to access; style applied next
            pass
    style_cmds = list(t._argS[0].getCommands()) if hasattr(t, "_argS") else []

    elements.append(t)
    return elements


# ── Public API ───────────────────────────────────────────────────────────────

async def generate_daily_report(report_date: date, session: AsyncSession) -> bytes:
    """Generate a full daily PDF report and return it as bytes."""
    data = await _gather_data(report_date, session)
    styles = _build_styles()

    elements: list = []
    elements.extend(_build_summary(data, report_date, styles))
    elements.extend(_build_rec_summary_table(data, styles))
    elements.extend(_build_rec_details(data, styles))
    elements.extend(_build_news(data, styles))
    elements.extend(_build_catalysts(data, report_date, styles))

    # Build PDF
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    def on_page(canvas, doc_obj):
        _header_footer(canvas, doc_obj, report_date)

    doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)

    logger.info(
        "Generated daily report for %s: %d recs, %d news, %d catalysts, %d bytes",
        report_date, len(data["recommendations"]), len(data["news"]),
        len(data["catalysts"]), buf.tell(),
    )

    return buf.getvalue()
