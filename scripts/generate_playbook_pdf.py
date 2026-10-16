import os
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.pdfgen import canvas

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 8)
        self.setFillColor(colors.HexColor("#6E7681"))
        
        # Header (pages > 1)
        if self._pageNumber > 1:
            self.drawString(54, 750, "ZAAKIY INSTITUTIONAL SYSTEM — TRADER'S PLAYBOOK & TELEMETRY GUIDE")
            self.setStrokeColor(colors.HexColor("#D0D7DE"))
            self.setLineWidth(0.5)
            self.line(54, 744, 558, 744)
            
        # Footer
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(558, 36, page_text)
        self.drawString(54, 36, "CONFIDENTIAL & PROPRIETARY — FOR INTERNAL TRADING USE ONLY")
        self.setStrokeColor(colors.HexColor("#D0D7DE"))
        self.setLineWidth(0.5)
        self.line(54, 48, 558, 48)
        self.restoreState()

def create_playbook_pdf(output_filename):
    doc = SimpleDocTemplate(
        output_filename,
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54
    )
    
    styles = getSampleStyleSheet()
    
    # Custom Palette
    primary_color = colors.HexColor("#0D1117")
    accent_blue   = colors.HexColor("#1F6FEB")
    bull_green    = colors.HexColor("#238636")
    bear_red      = colors.HexColor("#DA3633")
    warn_amber    = colors.HexColor("#D29922")
    slate_bg      = colors.HexColor("#F6F8FA")
    border_gray   = colors.HexColor("#D0D7DE")
    
    # Typography Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontName='Helvetica-Bold',
        fontSize=24,
        leading=28,
        textColor=primary_color,
        spaceAfter=6
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=11,
        leading=16,
        textColor=colors.HexColor("#57606A"),
        spaceAfter=15
    )
    h1_style = ParagraphStyle(
        'Heading1_Custom',
        parent=styles['Heading2'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        textColor=accent_blue,
        spaceBefore=14,
        spaceAfter=6
    )
    h2_style = ParagraphStyle(
        'Heading2_Custom',
        parent=styles['Heading3'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=primary_color,
        spaceBefore=10,
        spaceAfter=4
    )
    body_style = ParagraphStyle(
        'Body_Custom',
        parent=styles['BodyText'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#24292F"),
        spaceAfter=6
    )
    bold_body_style = ParagraphStyle(
        'BoldBody_Custom',
        parent=body_style,
        fontName='Helvetica-Bold'
    )
    table_cell_style = ParagraphStyle(
        'TableCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#24292F")
    )
    table_cell_bold = ParagraphStyle(
        'TableCellBold',
        parent=table_cell_style,
        fontName='Helvetica-Bold'
    )
    table_cell_header = ParagraphStyle(
        'TableCellHeader',
        parent=table_cell_style,
        fontName='Helvetica-Bold',
        textColor=colors.white
    )
    callout_style = ParagraphStyle(
        'CalloutText',
        parent=body_style,
        fontName='Helvetica-Oblique',
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#0969DA")
    )

    story = []

    # ─────────────────────────────────────────────────────────────────────────────
    # COVER / HEADER
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("ZAAKIY INSTITUTIONAL SYSTEM", title_style))
    story.append(Paragraph("<b>TradingView Indicator & Strategy Cockpit — Trader's Operating Manual</b><br/>"
                           "Comprehensive Telemetry Breakdown, Decision Matrix, and Execution Playbook", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=accent_blue, spaceBefore=0, spaceAfter=12))

    # ─────────────────────────────────────────────────────────────────────────────
    # SECTION 1: ARCHITECTURE OVERVIEW
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("1. Executive Summary & 7-Pillar Quantitative Engine", h1_style))
    story.append(Paragraph(
        "The <b>Zaakiy System</b> is an institutional-grade, multi-factor trend and momentum framework designed to "
        "eliminate retail whipsaws, distinguish healthy pullbacks from macro trend reversals, and execute asymmetric risk-reward trades. "
        "Unlike simplistic moving average crossover scripts, Zaakiy verifies 7 independent quantitative pillars before confirming any action:",
        body_style
    ))
    
    pillars_data = [
        [Paragraph("Pillar", table_cell_header), Paragraph("Mechanism", table_cell_header), Paragraph("Institutional Purpose", table_cell_header)],
        [Paragraph("<b>1. Trend Ribbon</b>", table_cell_style), Paragraph("21 / 50 EMA Ribbon + Optional 200 EMA", table_cell_style), Paragraph("Establishes primary trend regime and dynamic support/resistance cloud.", table_cell_style)],
        [Paragraph("<b>2. Supertrend Envelope</b>", table_cell_style), Paragraph("ATR Volatility Bands (Factor 3.0, Len 10)", table_cell_style), Paragraph("Volatility-adjusted trailing boundaries preventing early exits.", table_cell_style)],
        [Paragraph("<b>3. MACD Momentum</b>", table_cell_style), Paragraph("Fast 12, Slow 26, Signal 9 Histogram", table_cell_style), Paragraph("Measures impulse acceleration vs deceleration / exhaustion.", table_cell_style)],
        [Paragraph("<b>4. Relative Strength (RSI)</b>", table_cell_style), Paragraph("14-period RSI with regime thresholds", table_cell_style), Paragraph("Identifies momentum expansion (>50) and momentum divergences.", table_cell_style)],
        [Paragraph("<b>5. Volume & Flow (RVOL/CMF)</b>", table_cell_style), Paragraph("Relative Volume (RVOL) + Chaikin Money Flow", table_cell_style), Paragraph("Confirms true capital accumulation vs distribution / spoof wicks.", table_cell_style)],
        [Paragraph("<b>6. Valuation Zones</b>", table_cell_style), Paragraph("Dynamic Discount Buy & Premium Sell Areas", table_cell_style), Paragraph("Frames structural range: buy near wholesale floor, sell near ceiling.", table_cell_style)],
        [Paragraph("<b>7. Price Action Bar</b>", table_cell_style), Paragraph("Initiative close direction + zone containment", table_cell_style), Paragraph("Ensures trigger bar demonstrates initiative buyer/seller control.", table_cell_style)],
    ]
    t_pillars = Table(pillars_data, colWidths=[110, 180, 214])
    t_pillars.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent_blue),
        ('GRID', (0, 0), (-1, -1), 0.5, border_gray),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, slate_bg]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_pillars)
    story.append(Spacer(1, 10))

    # ─────────────────────────────────────────────────────────────────────────────
    # SECTION 2: TELEMETRY DASHBOARD GUIDE
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(Paragraph("2. The HUD Telemetry Cockpit (Understanding Every Statistic)", h1_style))
    story.append(Paragraph(
        "The HUD table on your chart updates on every tick, aggregating multi-timeframe analytics into an ultra-clean, "
        "high-contrast terminal interface. Every row has a specific tactical purpose:",
        body_style
    ))

    hud_data = [
        [Paragraph("Row / Statistic", table_cell_header), Paragraph("Normal Values", table_cell_header), Paragraph("What It Means & How to Trade It", table_cell_header)],
        [
            Paragraph("<b>Action</b>", table_cell_style),
            Paragraph("<font color='#58A6FF'><b>EARLY BUY</b></font><br/><font color='#238636'><b>BUY (Confirmed)</b></font><br/><font color='#FFD700'><b>TAKE PROFIT</b></font><br/><font color='#D29922'><b>DIP WATCH</b></font><br/>STAND DOWN", table_cell_style),
            Paragraph("<b>Automated Operational Directive:</b> Synthesizes all 7 pillars and valuation into an immediate, rule-based execution signal on every tick.", table_cell_style)
        ],
        [
            Paragraph("<b>Regime</b>", table_cell_style),
            Paragraph("<font color='#238636'><b>BULLISH</b></font> / <font color='#DA3633'><b>BEARISH</b></font>", table_cell_style),
            Paragraph("High-level directional bias. Never enter counter-regime trades without macro structural confirmation.", table_cell_style)
        ],
        [
            Paragraph("<b>Confluence</b>", table_cell_style),
            Paragraph("<b>0 / 7 to 7 / 7</b><br/>(Target: >= 5 / 7)", table_cell_style),
            Paragraph("<b>Primary Gatekeeper:</b> 5/7 to 7/7 confirms institutional sponsorship. 1/7 to 3/7 warns of failure/chop.", table_cell_style)
        ],
        [
            Paragraph("<b>Trend Health</b>", table_cell_style),
            Paragraph("<font color='#238636'><b>STRONG (Acc)</b></font><br/><font color='#D29922'><b>WEAKENING</b></font><br/>CONSOLIDATING", table_cell_style),
            Paragraph("Quantifies trend conviction via ADX + Divergences. <b>WEAKENING</b> flags RSI divergence or ADX exhaustion—the signal to trim position size.", table_cell_style)
        ],
        [
            Paragraph("<b>Structure</b>", table_cell_style),
            Paragraph("<b>TRENDING</b><br/><font color='#0969DA'><b>DIP PULLBACK</b></font><br/><font color='#DA3633'><b>MACRO REVERSAL</b></font>", table_cell_style),
            Paragraph("Classifies price vs Ribbon: <b>DIP PULLBACK</b> is a low-volume hold of 50 EMA (optimal buy); <b>MACRO REVERSAL</b> is structural failure (exit immediately).", table_cell_style)
        ],
        [
            Paragraph("<b>Valuation Area</b>", table_cell_style),
            Paragraph("<font color='#238636'><b>DISCOUNT (Buy)</b></font><br/><font color='#DA3633'><b>PREMIUM (Sell)</b></font><br/>FAIR VALUE", table_cell_style),
            Paragraph("Identifies wholesale vs retail distribution zones. Buy in Discount, scale out / take profit in Premium.", table_cell_style)
        ],
        [
            Paragraph("<b>Ribbon 21/50</b>", table_cell_style),
            Paragraph("Bullish (21>50) / Bearish (21<50)", table_cell_style),
            Paragraph("Fast and slow trend alignment. When stacked, confirms trend momentum is expanding.", table_cell_style)
        ],
        [
            Paragraph("<b>Supertrend</b>", table_cell_style),
            Paragraph("Bull Break / Bear Break", table_cell_style),
            Paragraph("Dynamic ATR volatility boundary. Acting as your trailing stop barrier.", table_cell_style)
        ],
        [
            Paragraph("<b>MACD Mom</b>", table_cell_style),
            Paragraph("Exp Bull / Exp Bear / Flat", table_cell_style),
            Paragraph("Cycle acceleration. 'Flat' indicates deceleration—watch for potential pullbacks.", table_cell_style)
        ],
        [
            Paragraph("<b>RSI (14)</b>", table_cell_style),
            Paragraph("Value (e.g. 71.9 Bull, 31.2 Bear)", table_cell_style),
            Paragraph(">50 = Bull dominance. >75 = Overextended (prepare TP). <25 = Capitulation (prepare short cover).", table_cell_style)
        ],
        [
            Paragraph("<b>Vol & CMF</b>", table_cell_style),
            Paragraph("RVOL x | CMF Score<br/>(e.g. 1.4x | +0.28)", table_cell_style),
            Paragraph("<b>RVOL >= 1.0x</b> validates institutional participation. <b>CMF > +0.05</b> proves net capital inflow; negative CMF warns of smart money selling.", table_cell_style)
        ],
        [
            Paragraph("<b>Open Interest (Perps)</b>", table_cell_style),
            Paragraph("OI Delta % (e.g. +3.4%)", table_cell_style),
            Paragraph("Rising OI with rising price = Genuine new capital accumulation. Falling OI = Short squeeze / liquidation.", table_cell_style)
        ],
    ]
    t_hud = Table(hud_data, colWidths=[100, 125, 279])
    t_hud.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), primary_color),
        ('GRID', (0, 0), (-1, -1), 0.5, border_gray),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, slate_bg]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_hud)

    # ─────────────────────────────────────────────────────────────────────────────
    # SECTION 3: THE 5 TRADING DECISIONS PLAYBOOK
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("3. Precision Execution Rules (The 5 Core Trading Decisions)", h1_style))
    story.append(Paragraph(
        "Use this operational decision matrix to execute trades objectively without emotional second-guessing:",
        body_style
    ))

    decisions = [
        ("A. Opening a Position (Long / Short Entry Gatekeepers)", [
            "<b>1. Confluence Requirement:</b> Must be <b>>= 5 / 7</b> (ideally 6/7 or 7/7). Never enter on 1/7 to 3/7.",
            "<b>2. Trend Health:</b> Must be <b>STRONG (Acc)</b> or <b>STEADY</b>. Stand down if CONSOLIDATING (ADX < 20).",
            "<b>3. Entry Style 1 — Breakout:</b> Enter on confirmed BUY/SELL signal when <code>Structure == TRENDING</code> and <code>RVOL >= 1.0x</code>.",
            "<b>4. Entry Style 2 — Pullback (Best R:R):</b> In a Bull trend, wait for <code>Structure == DIP PULLBACK</code> into the 21/50 EMA on low volume (RVOL < 1.0x). Enter when the first green candle closes above 21 EMA with positive MACD."
        ]),
        ("B. Reducing Position Size (De-Risking / Front-running Exhaustion)", [
            "<b>1. Signal: Trend Health switches to WEAKENING:</b> Occurs when RSI bearish divergence forms or ADX rolls over above 35. <b>Action: Trim 30% to 50% of position.</b>",
            "<b>2. Extreme RSI:</b> When RSI pushes above <b>75-80</b> on longs or drops below <b>20-25</b> on shorts, scale out 30%.",
            "<b>3. CMF Flow Divergence:</b> If price makes a new high but CMF turns negative (e.g. CMF = -0.12), smart money is offloading into retail FOMO. Reduce risk immediately."
        ]),
        ("C. Setting Take Profit (TP Targets)", [
            "<b>1. Institutional Valuation Zones:</b> The shaded <b>Premium Sell Area</b> (distribution ceiling) is the primary target for Longs. The <b>Discount Buy Area</b> is the target for Shorts.",
            "<b>2. Fixed R:R Targets:</b> The automated engine projects <b>+2.5R</b> (configurable 1.5R to 5.0R) based on initial risk.",
            "<b>3. Scaling Rule:</b> Scale 50% at +1.5R, scale 30% at Valuation Area ceiling, and let the remaining 20% runner trail behind the 21 EMA Ribbon."
        ]),
        ("D. Stop Loss (SL) & Moving to Breakeven", [
            "<b>1. Hard Initial Stop Loss:</b> Placed dynamically at <b>1.8x ATR</b> from entry, anchored just outside the <b>50 EMA Ribbon</b> and swing pivot.",
            "<b>2. Breakeven Lock (+2 ticks):</b> Once price achieves <b>+1.0R</b> in profit, the system automatically pulls the stop loss to Breakeven + 2 ticks, making the trade entirely risk-free.",
            "<b>3. Trailing Mechanism:</b> As long as <code>Trend Health == STRONG</code>, trail stop below the Supertrend line or candle close below the 21 EMA."
        ]),
        ("E. Closing a Position (100% Invalidation)", [
            "<b>1. Structure Flips to MACRO REVERSAL:</b> Price closes below 50 EMA with high volume (RVOL >= 1.2x) and negative money flow (CMF < -0.05). Close 100% immediately.",
            "<b>2. Confluence Collapse:</b> If confluence drops below <b>3 / 7</b> (as seen in the APTUSDT case study), the edge is gone.",
            "<b>3. Opposite Signal:</b> Confirmed flip to opposite Regime (e.g., Long position receives confirmed SELL signal)."
        ]),
    ]

    for title, points in decisions:
        story.append(Paragraph(title, h2_style))
        for pt in points:
            story.append(Paragraph(f"• {pt}", body_style))
        story.append(Spacer(1, 4))

    # ─────────────────────────────────────────────────────────────────────────────
    # SECTION 4: REAL-WORLD CHART CASE STUDIES
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 6))
    story.append(Paragraph("4. Real-World Chart Case Studies", h1_style))
    
    cases_data = [
        [Paragraph("Asset", table_cell_header), Paragraph("HUD Telemetry State", table_cell_header), Paragraph("Correct Institutional Action", table_cell_header)],
        [
            Paragraph("<b>ZKUSDT</b><br/>(4H Chart)", table_cell_style),
            Paragraph("Regime: BULLISH | Confluence: 4/7 (Rising)<br/>Health: STRONG (Acc) | Vol: 1.4x (High)<br/>Structure: TRENDING | RSI: 61.6", table_cell_style),
            Paragraph("<b>Prime Trend Expansion:</b> Ride long with stop below 21 EMA. CMF is -0.12, so plan to trim 40% when price touches the red Premium Sell Ceiling.", table_cell_style)
        ],
        [
            Paragraph("<b>APTUSDT</b><br/>(4H Chart)", table_cell_style),
            Paragraph("<font color='#DA3633'><b>Regime: BULL (Lagging) | Confluence: 1 / 7</b></font><br/>Health: CONSOLIDATING | Ribbon: Bearish<br/>Supertrend: Bear Break | RSI: 38.9 Bear", table_cell_style),
            Paragraph("<b>The Danger Trap:</b> DO NOT BUY. Confluence collapsed to 1/7 and all 5 indicators below are bright red. If holding a long, exit 100%.", table_cell_style)
        ],
        [
            Paragraph("<b>BTCUSD & OTHERS</b><br/>(4H Chart)", table_cell_style),
            Paragraph("Regime: BEARISH | Confluence: 5/7 to 6/7<br/>Supertrend: Bear Break | MACD: Exp Bear<br/>RSI: 31-37 Bear | Vol: 0.9x", table_cell_style),
            Paragraph("<b>High-Conviction Short:</b> High confluence short bias. Place stop above 50 EMA. Target is the green Discount Buy Area.", table_cell_style)
        ],
    ]
    t_cases = Table(cases_data, colWidths=[95, 175, 234])
    t_cases.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), accent_blue),
        ('GRID', (0, 0), (-1, -1), 0.5, border_gray),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, slate_bg]),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t_cases)

    # ─────────────────────────────────────────────────────────────────────────────
    # SECTION 5: CUSTOMIZATION & SETTINGS
    # ─────────────────────────────────────────────────────────────────────────────
    story.append(Spacer(1, 10))
    story.append(Paragraph("5. Customization & Settings Quick Reference", h1_style))
    story.append(Paragraph(
        "In TradingView, click the gear icon (⚙) on the Zaakiy indicator to adjust parameters to your trading style:",
        body_style
    ))

    settings_data = [
        [Paragraph("Setting Group", table_cell_header), Paragraph("Parameter", table_cell_header), Paragraph("Recommended Default", table_cell_header), Paragraph("Tactical Guidance", table_cell_header)],
        [
            Paragraph("Valuation Zones", table_cell_style),
            Paragraph("Show Institutional Areas<br/>Valuation Lookback<br/>Zone Depth (%)", table_cell_style),
            Paragraph("True<br/>40 bars<br/>25.0%", table_cell_style),
            Paragraph("Frames range highs/lows. Increase lookback to 60 for major swing levels; decrease to 25 for fast intraday scalps.", table_cell_style)
        ],
        [
            Paragraph("Execution & Risk", table_cell_style),
            Paragraph("Min Confluence Score<br/>SL ATR Multiplier<br/>Take-Profit R:R<br/>Breakeven Lock", table_cell_style),
            Paragraph("5 (out of 7)<br/>1.8x ATR<br/>2.5 R:R<br/>True (+1.0R)", table_cell_style),
            Paragraph("Requires 5/7 confirmation to trade. Automatically locks stop loss to breakeven once +1.0R is achieved.", table_cell_style)
        ],
        [
            Paragraph("Dashboard (HUD)", table_cell_style),
            Paragraph("Display HUD Table<br/>HUD Position<br/>Top Offset Distance (%)", table_cell_style),
            Paragraph("True<br/>Top Right (Below Header)<br/>5.5%", table_cell_style),
            Paragraph("Offsets table cleanly 15-20px below crypto token header so price and volume info are never obscured.", table_cell_style)
        ],
    ]
    t_settings = Table(settings_data, colWidths=[90, 120, 100, 194])
    t_settings.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), primary_color),
        ('GRID', (0, 0), (-1, -1), 0.5, border_gray),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, slate_bg]),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))
    story.append(t_settings)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"PDF successfully generated at: {output_filename}")

if __name__ == "__main__":
    output_pdf = "/Users/jithinjacob/Desktop/trdindicator/Zaakiy_Institutional_Indicator_Playbook.pdf"
    create_playbook_pdf(output_pdf)
