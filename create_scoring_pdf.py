#!/usr/bin/env python3
"""
Travint.ai — Scoring Framework PDF Generator
Creates a professional PDF showing all scoring thresholds for all categories
and all identity layers in table format with colored level indicators.
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.platypus.flowables import HRFlowable
from datetime import date

# ── Colour palette ────────────────────────────────────────────────────────────
GREEN_DOT   = colors.HexColor("#22c55e")
YELLOW_DOT  = colors.HexColor("#eab308")
ORANGE_DOT  = colors.HexColor("#f97316")
RED_DOT     = colors.HexColor("#ef4444")
PURPLE_DOT  = colors.HexColor("#a855f7")

GREEN_BG    = colors.HexColor("#f0fdf4")
YELLOW_BG   = colors.HexColor("#fefce8")
ORANGE_BG   = colors.HexColor("#fff7ed")
RED_BG      = colors.HexColor("#fef2f2")
PURPLE_BG   = colors.HexColor("#faf5ff")

HEADER_BG   = colors.HexColor("#1e293b")
HEADER_FG   = colors.white
SUBHEAD_BG  = colors.HexColor("#334155")
DIVIDER     = colors.HexColor("#e2e8f0")
PAGE_BG     = colors.white
ACCENT      = colors.HexColor("#6366f1")

LEVEL_COLORS = {
    "GREEN":  (GREEN_DOT,  GREEN_BG),
    "YELLOW": (YELLOW_DOT, YELLOW_BG),
    "ORANGE": (ORANGE_DOT, ORANGE_BG),
    "RED":    (RED_DOT,    RED_BG),
    "PURPLE": (PURPLE_DOT, PURPLE_BG),
}

# ── Page geometry ─────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

# ── Styles ────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def S(name, **kw):
    """Create a named paragraph style."""
    base = styles.get(name, styles["Normal"])
    return ParagraphStyle(name + "_custom_" + str(id(kw)), parent=base, **kw)

TITLE_STYLE   = S("Normal", fontSize=26, fontName="Helvetica-Bold",
                  textColor=colors.white, alignment=TA_CENTER, spaceAfter=4)
SUBTITLE_STYLE= S("Normal", fontSize=11, fontName="Helvetica",
                  textColor=colors.HexColor("#94a3b8"), alignment=TA_CENTER)
DATE_STYLE    = S("Normal", fontSize=9,  fontName="Helvetica",
                  textColor=colors.HexColor("#64748b"), alignment=TA_CENTER)

SECTION_STYLE = S("Normal", fontSize=14, fontName="Helvetica-Bold",
                  textColor=colors.white, spaceAfter=0, spaceBefore=0)
CAT_STYLE     = S("Normal", fontSize=12, fontName="Helvetica-Bold",
                  textColor=colors.white, spaceAfter=0, spaceBefore=0)
CAT_SUB_STYLE = S("Normal", fontSize=8, fontName="Helvetica",
                  textColor=colors.HexColor("#94a3b8"), spaceAfter=0)

LEVEL_STYLE   = S("Normal", fontSize=9.5, fontName="Helvetica-Bold",
                  textColor=colors.HexColor("#1e293b"), leading=12)
BODY_STYLE    = S("Normal", fontSize=8.5, fontName="Helvetica",
                  textColor=colors.HexColor("#374151"), leading=11, spaceAfter=2)
NOTE_STYLE    = S("Normal", fontSize=7.5, fontName="Helvetica-Oblique",
                  textColor=colors.HexColor("#6b7280"), leading=10)
VETO_STYLE    = S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                  textColor=colors.HexColor("#991b1b"), leading=11)
FLOOR_STYLE   = S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                  textColor=colors.HexColor("#7e22ce"), leading=11)
SOURCE_STYLE  = S("Normal", fontSize=7.5, fontName="Helvetica",
                  textColor=colors.HexColor("#6b7280"), leading=10)

# ── Helper: colored dot cell ──────────────────────────────────────────────────
def level_label(level: str) -> Paragraph:
    dot, _ = LEVEL_COLORS[level]
    hex_color = dot.hexval() if hasattr(dot, 'hexval') else "#{:02x}{:02x}{:02x}".format(
        int(dot.red*255), int(dot.green*255), int(dot.blue*255))
    return Paragraph(
        f'<font color="{hex_color}">●</font> <b>{level}</b>',
        LEVEL_STYLE
    )

# ── Helper: threshold table row builder ───────────────────────────────────────
def threshold_rows(data):
    """
    data = list of (level_str, description_str)
    Returns list of table rows ready for Table().
    """
    rows = []
    for level, desc in data:
        _, bg = LEVEL_COLORS[level]
        rows.append([level_label(level), Paragraph(desc, BODY_STYLE)])
    return rows

def threshold_table(rows_data, col_widths=None):
    """Build a styled threshold table."""
    if col_widths is None:
        col_widths = [22*mm, CONTENT_W - 22*mm]

    rows = [
        [Paragraph("<b>Level</b>", S("Normal", fontSize=8.5,
                                     fontName="Helvetica-Bold",
                                     textColor=colors.white)),
         Paragraph("<b>Threshold &amp; Reasoning</b>", S("Normal", fontSize=8.5,
                                                          fontName="Helvetica-Bold",
                                                          textColor=colors.white))]
    ]

    style_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), SUBHEAD_BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("TOPPADDING",    (0, 0), (-1, 0), 5),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1),
         [LEVEL_COLORS[r[0]][1] for r in rows_data]),
        ("GRID",          (0, 0), (-1, -1), 0.3, DIVIDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING",    (0, 1), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
    ]

    for i, (level, desc) in enumerate(rows_data, start=1):
        _, bg = LEVEL_COLORS[level]
        style_cmds.append(("BACKGROUND", (0, i), (0, i), bg))
        rows.append(threshold_rows([(level, desc)])[0])

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


# ── Section header flowable ───────────────────────────────────────────────────
def section_header(title, subtitle=""):
    elems = []
    header_data = [[Paragraph(title, SECTION_STYLE)]]
    if subtitle:
        header_data[0].append(Paragraph(subtitle, CAT_SUB_STYLE))

    tbl = Table([[Paragraph(title, SECTION_STYLE)]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (-1, -1), HEADER_BG),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",(0, 0), (-1, -1), 10),
        ("TOPPADDING",  (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0,0), (-1, -1), 8),
        ("ROUNDEDCORNERS", (0,0), (-1,-1), [4,4,4,4]),
    ]))
    elems.append(Spacer(1, 6*mm))
    elems.append(tbl)
    if subtitle:
        elems.append(Paragraph(subtitle, NOTE_STYLE))
    elems.append(Spacer(1, 3*mm))
    return elems


def category_header(title, subtitle=""):
    row = [Paragraph(title, CAT_STYLE)]
    tbl = Table([[Paragraph(title, CAT_STYLE)]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, -1), SUBHEAD_BG),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
    ]))
    elems = [tbl]
    if subtitle:
        elems.append(Paragraph(subtitle, NOTE_STYLE))
    elems.append(Spacer(1, 2*mm))
    return elems


def note_box(text, color=None):
    if color is None:
        color = colors.HexColor("#eff6ff")
    tbl = Table([[Paragraph(text, NOTE_STYLE)]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,-1), color),
        ("LEFTPADDING",  (0,0), (-1,-1), 8),
        ("RIGHTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",   (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0), (-1,-1), 5),
        ("BOX",          (0,0), (-1,-1), 0.5, colors.HexColor("#bfdbfe")),
    ]))
    return [tbl, Spacer(1, 2*mm)]


# ── Cover page ────────────────────────────────────────────────────────────────
def build_cover():
    elems = []
    elems.append(Spacer(1, 30*mm))

    cover_tbl = Table(
        [[Paragraph("Travint.ai", TITLE_STYLE)],
         [Paragraph("Scoring Framework", TITLE_STYLE)],
         [Spacer(1, 4*mm)],
         [Paragraph("Complete threshold definitions for all 7 security categories<br/>"
                    "and all 6 identity layers", SUBTITLE_STYLE)],
         [Spacer(1, 3*mm)],
         [Paragraph(f"Generated: {date.today().strftime('%d %B %Y')}", DATE_STYLE)],
         ],
        colWidths=[CONTENT_W]
    )
    cover_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), HEADER_BG),
        ("LEFTPADDING",   (0, 0), (-1, -1), 15),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 15),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING",    (0, 0), (0, 0),   20),
        ("BOTTOMPADDING", (0, -1), (0, -1), 20),
    ]))
    elems.append(cover_tbl)
    elems.append(Spacer(1, 10*mm))

    # Legend
    legend_rows = [
        [Paragraph("<b>Level Legend</b>", S("Normal", fontSize=9, fontName="Helvetica-Bold",
                                             textColor=colors.HexColor("#374151")))],
    ]
    legend_data = [
        ("GREEN",  "Normal conditions — standard precautions"),
        ("YELLOW", "Elevated structural risk — be aware, make contingency plans"),
        ("ORANGE", "Significant risk — meaningful precautions required"),
        ("RED",    "High risk — reconsider travel"),
        ("PURPLE", "Extreme risk — do not travel"),
    ]
    for level, desc in legend_data:
        dot, bg = LEVEL_COLORS[level]
        legend_rows.append([
            Table([[level_label(level), Paragraph(desc, BODY_STYLE)]],
                  colWidths=[22*mm, CONTENT_W - 22*mm - 6*mm])
        ])

    leg_tbl = Table(legend_rows, colWidths=[CONTENT_W])
    leg_tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("BACKGROUND",    (0, 1), (-1, -1), colors.white),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("BOX",           (0, 0), (-1, -1), 0.5, DIVIDER),
        ("LINEBELOW",     (0, 0), (-1, 0),  0.5, DIVIDER),
        ("INNERGRID",     (0, 1), (-1, -1), 0.3, DIVIDER),
    ]))
    elems.append(leg_tbl)

    elems.append(PageBreak())
    return elems


# ── Part 1: Category thresholds ───────────────────────────────────────────────
def build_categories():
    elems = []
    elems += section_header(
        "PART 1 — Security Category Thresholds",
        "Definitions and reasoning for each of the 7 security categories across 5 scoring levels"
    )

    # ── 1. ARMED CONFLICT ────────────────────────────────────────────────────
    elems += category_header(
        "1. ARMED CONFLICT",
        "Score: fighting ON the country's territory, or attacks directly threatening it. "
        "Score what a traveler physically encounters — not the country's foreign policy."
    )
    rows = [
        ("GREEN",  "No armed conflict on national territory. No active fighting anywhere in the country. "
                   "Traveler can move freely. Military exists but is not engaged domestically. "
                   "WHY: The country is at peace internally."),
        ("YELLOW", "Localised or frozen conflict in remote border areas that does not affect traveler movement. "
                   "OR the country's military is deployed overseas in a foreign war with zero fighting on home soil. "
                   "WHY: Risk exists but is geographically contained or entirely external."),
        ("ORANGE", "Active conflict in part of the country (under ~20% of territory). Capital and major cities "
                   "are safe. Conflict zones are known and avoidable. Under ~500 conflict-related deaths per month. "
                   "WHY: Real conflict exists and affects some travelers, but the country is still largely functional."),
        ("RED",    "Widespread conflict affecting multiple major regions. OR capital or large cities directly "
                   "threatened (either condition alone is sufficient — not both required). OR regular "
                   "missile/rocket/airstrike attacks on populated areas regardless of interception rate — "
                   "routine incoming fire = RED minimum. WHY: Normal travel planning is impossible."),
        ("PURPLE", "Full-scale war. Active fighting in or near the capital or major cities. Daily incoming fire. "
                   "Territory actively contested across multiple fronts. "
                   "WHY: The country is a war zone. Consular protection may be unavailable."),
    ]
    elems.append(threshold_table(rows))
    elems += note_box(
        "NOTE: Overseas military deployment = YELLOW at most. "
        "Regular intercepted missiles = RED minimum (the threat is real regardless of interception). "
        "This is the ONLY category with a hard veto on the total score (RED → total RED; PURPLE → total PURPLE)."
    )
    elems.append(Spacer(1, 4*mm))

    # ── 2. REGIONAL INSTABILITY ──────────────────────────────────────────────
    elems += category_header(
        "2. REGIONAL INSTABILITY",
        "Score: how much neighbouring/regional conflicts affect THIS country. "
        "Assess spillover risk INTO this country — not the neighbour's situation."
    )
    rows = [
        ("GREEN",  "Stable neighbourhood. No active wars in bordering countries. No meaningful spillover: "
                   "no refugee flows affecting security, no cross-border incidents."),
        ("YELLOW", "Some regional tensions or low-level conflicts nearby. Minimal direct spillover. "
                   "Perhaps some refugees or diplomatic tensions but no security impact on travelers."),
        ("ORANGE", "Active conflict in a neighbouring country with documented spillover: significant refugee flows "
                   "creating security pressure, cross-border incidents, armed groups operating across the border."),
        ("RED",    "Direct threat from neighbouring conflict. Missiles or armed groups crossing the border. "
                   "Meaningful risk of being drawn into the wider conflict. Country is providing active support "
                   "to a warring party and faces retaliatory risk."),
        ("PURPLE", "Country is a frontline state, direct participant, or theatre of a regional war. "
                   "The country is not merely affected — it is functionally part of the regional war."),
    ]
    elems.append(threshold_table(rows))
    elems += note_box(
        "NOTE: Regional instability has NO hard veto on the total score. It raises the weighted average but "
        "cannot force a total by itself. Example: Poland (regional_instability ORANGE-RED due to Ukraine war) "
        "can still have total YELLOW-ORANGE because no fighting is on Polish soil."
    )
    elems.append(Spacer(1, 4*mm))

    # ── 3. TERRORISM ─────────────────────────────────────────────────────────
    elems += category_header(
        "3. TERRORISM",
        "Score: organised threat from non-state actors using violence for political ends. "
        "Key discriminator: is there an ORGANISED GROUP with ONGOING CAPABILITY AND INTENT?"
    )
    rows = [
        ("GREEN",  "No credible terrorist threat. No attacks with fatalities in 5+ years. "
                   "No organised groups operating in the country with stated intent to attack."),
        ("YELLOW", "Very low threat. Only foiled plots, minor incidents, or a single isolated lone-wolf attack "
                   "with NO evidence of an organised group and no further attacks since. A one-off tragedy, not a campaign."),
        ("ORANGE", "Credible, active threat. An organised group with demonstrated attack capability exists and "
                   "is operating in the country. 1-2 attacks with casualties (1-4 deaths each) in past 2 years "
                   "by organised actors. A lone-wolf attack with high death toll but NO organised group = ORANGE."),
        ("RED",    "Active organised campaign with demonstrated repeat intent. Requires BOTH: "
                   "(a) identified organised group with stated/demonstrated ongoing intent AND "
                   "(b) multiple attacks with deaths in past 2 years, OR 3+ attacks in past 12 months by "
                   "same/affiliated group, OR persistent monthly incidents. "
                   "KEY: A single lone-wolf attack, even with 6+ deaths, does NOT = RED without organised group evidence."),
        ("PURPLE", "Sustained high-frequency organised campaign. Weekly or near-weekly attacks by organised actors. "
                   "OR 3+ attacks each with 2+ deaths in the past year by the same group or network. "
                   "OR terrorism is integral to an active war. PURPLE = superlative of RED. "
                   "Requires frequency AND organised capability."),
    ]
    elems.append(threshold_table(rows))
    elems += note_box(
        "NOTE: terrorism PURPLE → total at minimum RED (soft floor, not hard veto). "
        "terrorism RED → total at minimum ORANGE. "
        "Frequency + organised capability are the key discriminators — NOT body count alone."
    )
    elems.append(Spacer(1, 4*mm))

    elems.append(PageBreak())

    # ── 4. CIVIL STRIFE ──────────────────────────────────────────────────────
    elems += category_header(
        "4. CIVIL STRIFE",
        "Score: political violence, government repression, and social unrest affecting travelers. "
        "Distinct from armed conflict (military forces) and terrorism (targeted political attacks)."
    )
    rows = [
        ("GREEN",  "Politically stable. Protests are rare and peaceful when they occur. "
                   "Government transition follows established rules. No significant unrest."),
        ("YELLOW", "Occasional protests or political tensions. Demonstrations are peaceful or quickly dispersed. "
                   "No significant violence. Traveler can easily avoid."),
        ("ORANGE", "Sustained protests with episodes of violence. Some parts of cities periodically unsafe. "
                   "Government response includes tear gas, water cannons, or periodic arrests. "
                   "Also ORANGE: authoritarian government with strict enforcement of laws that could "
                   "criminalise ordinary traveler behaviour (photography, dress, speech)."),
        ("RED",    "Widespread unrest, significant riots, or sustained political violence affecting major cities. "
                   "Government using lethal force against protesters. OR authoritarian government with pattern "
                   "of detaining/arresting foreigners. OR significant breakdown of rule of law."),
        ("PURPLE", "Coup, active civil war, or complete collapse of public order. Government has lost control "
                   "of significant territory to competing armed factions. No predictable rule of law anywhere. "
                   "Emergency laws suspending civil rights."),
    ]
    elems.append(threshold_table(rows))
    elems += note_box(
        "NOTE: civil_strife PURPLE → total at minimum RED (soft floor). "
        "civil_strife RED → total at minimum ORANGE."
    )
    elems.append(Spacer(1, 4*mm))

    # ── 5. CRIME ─────────────────────────────────────────────────────────────
    elems += category_header(
        "5. CRIME",
        "Score: criminal risk to travelers — primarily violent crime and kidnapping. "
        "Primary anchor: intentional homicide rate/100k/year (UNODC). Score realistic traveler encounter."
    )
    rows = [
        ("GREEN",  "Under 5 homicides/100k/year. Low organised crime affecting travelers. "
                   "Petty theft possible in tourist areas but violent crime against travelers rare."),
        ("YELLOW", "5–15 homicides/100k/year. Petty theft and pickpocketing common. "
                   "Occasional opportunistic crime. Violent crime against travelers uncommon. Standard urban awareness sufficient."),
        ("ORANGE", "15–30 homicides/100k/year. OR documented kidnapping risk in specific provinces or areas. "
                   "Robbery, carjacking, and assault are realistic risks in certain areas or times of day."),
        ("RED",    "30–60 homicides/100k/year. OR documented kidnapping-for-ransom specifically targeting "
                   "foreign nationals. OR criminal organisations controlling significant territory."),
        ("PURPLE", "Over 60 homicides/100k/year. OR criminal organisations exercise SUBSTANTIAL territorial control "
                   "over MULTIPLE states/large provinces — meaning the state has effectively ceded governance. "
                   "NOT PURPLE: gang presence in neighbourhoods; cartel activity in one city; USA despite gang violence. "
                   "Mexico overall = RED (not PURPLE) despite cartels in some states."),
    ]
    elems.append(threshold_table(rows))
    elems.append(Spacer(1, 4*mm))

    # ── 6. HEALTH ────────────────────────────────────────────────────────────
    elems += category_header(
        "6. HEALTH",
        "Score: traveler's ability to access safe, effective medical care and avoid serious disease. "
        "Score what a traveler in a MAJOR CITY would experience — not the worst rural area."
    )
    rows = [
        ("GREEN",  "High-income country with fully functional, accessible hospital system. "
                   "Standard travel vaccinations sufficient. No active disease outbreaks. "
                   "Examples: EU countries, USA, Australia, Japan, Israel."),
        ("YELLOW", "Adequate healthcare in major cities. Some limitations in rural areas or specialist care. "
                   "Minor endemic disease considerations. Travel health insurance advisable."),
        ("ORANGE", "Limited or variable healthcare outside major cities. Active endemic diseases requiring "
                   "prophylaxis (malaria, dengue, cholera in specific regions). Medical evacuation insurance "
                   "strongly recommended."),
        ("RED",    "Poor healthcare infrastructure even in major cities. Standard surgical care not reliably "
                   "available or safe. Active epidemic or disease outbreak affecting travelers. "
                   "Medical evacuation very likely needed for any serious illness."),
        ("PURPLE", "Healthcare system has PHYSICALLY COLLAPSED. Hospitals are bombed, closed, or completely "
                   "non-functional in major cities. No emergency care available. "
                   "Examples: Yemen 2023, Syria 2015-2019, Gaza 2024. "
                   "NOT PURPLE: under-funded, sanctions-strained, or strained by war casualties — "
                   "if hospitals are open and treating patients, it is not PURPLE."),
    ]
    elems.append(threshold_table(rows))
    elems.append(Spacer(1, 4*mm))

    # ── 7. INFRASTRUCTURE ────────────────────────────────────────────────────
    elems += category_header(
        "7. INFRASTRUCTURE",
        "Score: physical state of roads, power, water supply, and communications ONLY. "
        "Missile alerts, curfews, and security restrictions are ARMED CONFLICT factors — not infrastructure."
    )
    rows = [
        ("GREEN",  "Modern, well-maintained infrastructure. Road fatality rate under 10/100k/year. "
                   "Reliable electricity, clean water, internet/mobile in urban areas. "
                   "Examples: EU, USA, Australia, Israel (March 2026 — roads/power/water/internet all function)."),
        ("YELLOW", "Generally good infrastructure with some gaps. Road fatality rate 10–20/100k. "
                   "Utilities reliable in cities but variable in rural areas. Some seasonal issues."),
        ("ORANGE", "Unreliable infrastructure in significant parts of the country. Road fatality rate 20–30/100k. "
                   "OR frequent, unpredictable power or water outages. Rural roads dangerous or impassable."),
        ("RED",    "Poor infrastructure nationwide. Road fatality rate over 30/100k. OR utilities unreliable "
                   "throughout the country — not just rural areas. OR infrastructure physically damaged by "
                   "conflict/disaster and not yet restored."),
        ("PURPLE", "Infrastructure has PHYSICALLY COLLAPSED. No reliable roads, power, water, or communications "
                   "in MAJOR CITIES. Traveler movement impossible without private security and logistics. "
                   "NOT PURPLE: power cuts, poor roads, slow internet, censored internet — "
                   "even in a war zone, if roads/utilities function, it is not PURPLE."),
    ]
    elems.append(threshold_table(rows))
    elems += note_box(
        "EVIDENCE GATE: Infrastructure cannot score RED or PURPLE if: roads are passable in major cities, "
        "electricity is available, water is available, AND mobile/internet connectivity is available — "
        "unless there is a specific source quote documenting physical damage. "
        "Israel (March 2026) is YELLOW despite active war, because all systems function normally."
    )
    elems.append(Spacer(1, 4*mm))

    return elems


# ── Part 2: Total Score Logic ──────────────────────────────────────────────────
def build_total_score():
    elems = []
    elems.append(PageBreak())
    elems += section_header(
        "PART 2 — Total Score Logic",
        "How the 7 category scores combine into a single overall travel risk level"
    )

    logic_rows = [
        [Paragraph("<b>Layer</b>", S("Normal", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
         Paragraph("<b>Rule</b>", S("Normal", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
         Paragraph("<b>Rationale</b>", S("Normal", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white))],
        [Paragraph("1. Hard Veto", VETO_STYLE),
         Paragraph("armed_conflict RED → total = RED<br/>armed_conflict PURPLE → total = PURPLE", BODY_STYLE),
         Paragraph("Active widespread conflict on national territory overrides all other factors. "
                   "No other single category triggers a hard veto.", BODY_STYLE)],
        [Paragraph("2. Weighted Average", S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                                             textColor=colors.HexColor("#1d4ed8"), leading=11)),
         Paragraph("<b>Security cats × 2:</b> armed_conflict, regional_instability, terrorism, civil_strife<br/>"
                   "<b>Other cats × 1:</b> crime, health, infrastructure<br/>"
                   "Total weight = 11. Thresholds: ≤1.4→GREEN, ≤2.4→YELLOW, ≤3.4→ORANGE, ≤4.4→RED, else PURPLE", BODY_STYLE),
         Paragraph("Security threats are the primary concern. Infrastructure/health are important but "
                   "a bad road network is not as dangerous as an active terrorist campaign.", BODY_STYLE)],
        [Paragraph("3. Soft Floors", FLOOR_STYLE),
         Paragraph("terrorism or civil_strife PURPLE → total at minimum RED<br/>"
                   "terrorism or civil_strife RED → total at minimum ORANGE", BODY_STYLE),
         Paragraph("Near-weekly attacks or widespread political violence create a floor for the total. "
                   "PURPLE total requires armed_conflict — terrorism alone cannot reach PURPLE unless weighted average gets there.", BODY_STYLE)],
    ]

    tbl = Table(logic_rows, colWidths=[32*mm, 80*mm, CONTENT_W-112*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), HEADER_BG),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("BACKGROUND",    (0, 1), (-1, 1), colors.HexColor("#fef2f2")),
        ("BACKGROUND",    (0, 2), (-1, 2), colors.HexColor("#eff6ff")),
        ("BACKGROUND",    (0, 3), (-1, 3), colors.HexColor("#faf5ff")),
        ("GRID",          (0, 0), (-1, -1), 0.3, DIVIDER),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 7),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elems.append(tbl)
    elems.append(Spacer(1, 4*mm))
    return elems


# ── Part 3: Identity Layer Logic ──────────────────────────────────────────────
def build_identity_layers():
    elems = []
    elems.append(PageBreak())
    elems += section_header(
        "PART 3 — Identity Layer Scoring Logic",
        "How scores are adjusted for each identity group. BASE layer is the floor — "
        "identity layers can equal or exceed base scores, rarely lower."
    )

    # Universal principle box
    elems += note_box(
        "UNIVERSAL PRINCIPLE: The base layer score is the MINIMUM for all identity layers. "
        "Each identity layer only adjusts categories where belonging to that group creates a STRUCTURALLY "
        "different risk. If there is no structural difference, the layer inherits the base score for that category. "
        "The same total score logic (hard veto, weighted average, soft floors) applies to all layers, "
        "PLUS any layer-specific hard vetoes and soft floors defined below.",
        color=colors.HexColor("#f0f9ff")
    )
    elems.append(Spacer(1, 2*mm))

    # ── Layer tables helper ───────────────────────────────────────────────────
    def layer_adj_table(adjustments):
        """adjustments = list of (category, action, detail)"""
        rows = [[
            Paragraph("<b>Category</b>", S("Normal", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white)),
            Paragraph("<b>Action vs Base</b>", S("Normal", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white)),
            Paragraph("<b>Condition</b>", S("Normal", fontSize=8.5, fontName="Helvetica-Bold", textColor=colors.white)),
        ]]
        style_cmds = [
            ("BACKGROUND",    (0, 0), (-1, 0), SUBHEAD_BG),
            ("GRID",          (0, 0), (-1, -1), 0.3, DIVIDER),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]
        for i, (cat, action, detail) in enumerate(adjustments, start=1):
            if "INHERIT" in action:
                action_style = NOTE_STYLE
                bg = colors.HexColor("#f8fafc")
            elif "RAISE" in action:
                action_style = S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#b45309"), leading=11)
                bg = colors.HexColor("#fffbeb")
            else:
                action_style = BODY_STYLE
                bg = colors.white
            style_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
            rows.append([
                Paragraph(cat, S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                                  textColor=colors.HexColor("#374151"), leading=11)),
                Paragraph(action, action_style),
                Paragraph(detail, NOTE_STYLE),
            ])
        tbl = Table(rows, colWidths=[32*mm, 30*mm, CONTENT_W-62*mm])
        tbl.setStyle(TableStyle(style_cmds))
        return tbl

    def veto_floor_table(items, is_veto=True):
        color = colors.HexColor("#fef2f2") if is_veto else colors.HexColor("#faf5ff")
        border_color = RED_DOT if is_veto else PURPLE_DOT
        title_color = colors.HexColor("#991b1b") if is_veto else colors.HexColor("#7e22ce")
        label = "HARD VETOES" if is_veto else "SOFT FLOORS"
        rows = [[Paragraph(f"<b>{label}</b>",
                            S("Normal", fontSize=8.5, fontName="Helvetica-Bold",
                              textColor=title_color))]]
        for trigger, result in items:
            rows.append([
                Table([[
                    Paragraph(f"<b>If:</b> {trigger}", BODY_STYLE),
                    Paragraph(f"<b>Then:</b> {result}", BODY_STYLE),
                ]], colWidths=[(CONTENT_W-2)/2, (CONTENT_W-2)/2])
            ])
        tbl = Table(rows, colWidths=[CONTENT_W])
        tbl.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (-1, 0), color),
            ("BACKGROUND",    (0, 1), (-1, -1), colors.white),
            ("BOX",           (0, 0), (-1, -1), 0.8, border_color),
            ("LINEBELOW",     (0, 0), (-1, 0),  0.5, border_color),
            ("LEFTPADDING",   (0, 0), (-1, -1), 7),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 7),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("INNERGRID",     (0, 1), (-1, -1), 0.2, DIVIDER),
        ]))
        return tbl

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 1: BASE
    # ─────────────────────────────────────────────────────────────────────────
    elems += category_header("LAYER 1: BASE",
                              "General international travelers — the default layer and floor for all other layers")
    elems += note_box(
        "The base layer applies to ALL travelers regardless of identity. "
        "It uses the full scoring framework from Part 1 with no adjustments. "
        "All other identity layers inherit base scores unless a structural difference exists.",
        color=colors.HexColor("#f8fafc")
    )
    elems.append(Spacer(1, 3*mm))

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 2: JEWISH / ISRAELI
    # ─────────────────────────────────────────────────────────────────────────
    elems.append(PageBreak())
    elems += category_header("LAYER 2: JEWISH / ISRAELI",
                              "Covers Jewish travelers of any nationality AND Israeli passport holders. "
                              "Score for the higher-risk of the two where they differ.")
    adj = [
        ("armed_conflict",      "INHERIT BASE",  "Missiles/bombs don't discriminate by religion. Exception: if conflict specifically targets Israeli nationals as a stated goal, raise terrorism (not armed_conflict)."),
        ("regional_instability","INHERIT BASE",  "Geopolitical dynamics are the same for all travelers."),
        ("terrorism",           "RAISE if...",   "Multiple documented incidents targeting Jews/Israelis in past 3 years → min ORANGE. Israeli NSC terrorism warning → one level above base (min ORANGE). Active groups with stated goal of targeting Israelis (Hezbollah, IRGC proxies) → min RED."),
        ("civil_strife",        "RAISE if...",   "Government institutionally hostile to Israel/Jews (not just critical). State-sanctioned antisemitism with legal enforcement. Protests targeting Jews/Israelis with documented violence. → one level above base."),
        ("crime",               "RAISE if...",   "Documented hate crime pattern targeting Jews (multiple incidents in past 3 years). Local Jewish community reports active targeting. → one level above base."),
        ("health",              "INHERIT BASE",  "Exception: if Israeli passport holder cannot access state hospitals (rare). Note in narrative but rarely changes score."),
        ("infrastructure",      "INHERIT BASE",  "Israeli passport restrictions affect ENTRY, not in-country infrastructure once admitted."),
    ]
    elems.append(layer_adj_table(adj))
    elems.append(Spacer(1, 2*mm))

    veto_items = [
        ("Israeli passport legally banned (Iran, Saudi, Lebanon, Syria, Libya, Yemen, Iraq, Pakistan)",
         "total = RED minimum. Traveler CANNOT LEGALLY ENTER. Note: American/EU Jews without Israeli passport may still enter some countries."),
        ("Israeli NSC Warning Level 4 (Do Not Travel)",
         "total = PURPLE. Israeli state believes nationals face extreme, likely lethal, risk."),
    ]
    floor_items = [
        ("Israeli NSC Warning Level 3 (Reconsider Travel)", "total at minimum RED"),
        ("Israeli NSC Warning Level 2 (Exercise Caution)",  "total at minimum one level above base"),
        ("Israeli NSC Warning Level 1 (Standard precautions)", "no floor added — inherit base"),
        ("Documented organised antisemitic attack in past 24 months", "terrorism at minimum ORANGE"),
    ]
    elems.append(veto_floor_table(veto_items, is_veto=True))
    elems.append(Spacer(1, 2*mm))
    elems.append(veto_floor_table(floor_items, is_veto=False))
    elems.append(Spacer(1, 2*mm))
    elems += note_box(
        "Data sources: Israeli NSC travel warnings (israeli_nsc_warnings.yaml) · ADL Global 100 · "
        "Kantor Center Annual Report · FRA (EU Fundamental Rights Agency) · CST / CRIF incident reports"
    )
    elems.append(Spacer(1, 4*mm))

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 3: SOLO WOMEN
    # ─────────────────────────────────────────────────────────────────────────
    elems.append(PageBreak())
    elems += category_header("LAYER 3: SOLO WOMEN",
                              "Women traveling alone internationally, without a companion. "
                              "Key risks: legal restrictions, gender-based violence (GBV), transport safety, women's healthcare.")
    adj = [
        ("armed_conflict",      "RAISE if...",   "Conflict involves documented systematic use of sexual violence as a weapon of war (e.g. DRC, Sudan). Raise one level above base to signal additional targeting risk."),
        ("regional_instability","INHERIT BASE",  "Geopolitical dynamics are the same for all travelers."),
        ("terrorism",           "INHERIT BASE",  "Terrorism generally does not discriminate by gender. Exception: Taliban-controlled areas where women specifically targeted for visible presence."),
        ("civil_strife",        "RAISE if...",   "Country ENFORCES legal dress code for women with criminal penalties (mandatory hijab with police enforcement). Guardianship laws restricting women's independent movement. Active crackdown with documented arrests. → min one level above base."),
        ("crime",               "RAISE if...",   "High sexual harassment/assault rate in public spaces documented as structural risk (India, Egypt, parts of Morocco). Femicide rate significantly elevated above general homicide rate. Public transport/taxis documented as unsafe for solo women at night. → min one level above base."),
        ("health",              "RAISE if...",   "Reproductive healthcare inaccessible (contraception unavailable, abortion illegal with no exceptions). Sexual assault care non-functional (no rape kits, reporting leads to re-victimisation). Emergency contraception illegal."),
        ("infrastructure",      "INHERIT BASE",  "Physical road network is the same. Transport safety for women is captured in crime, not infrastructure."),
    ]
    elems.append(layer_adj_table(adj))
    elems.append(Spacer(1, 2*mm))

    veto_items = [
        ("Country requires male guardian (mahram) for a woman to travel independently",
         "total = RED minimum. Traveler cannot exercise basic freedom of movement. Historically: Saudi Arabia (easing), Afghanistan (complete restriction)."),
        ("Country requires male permission to obtain a passport or travel abroad",
         "total = RED minimum."),
    ]
    floor_items = [
        ("Legal dress code with criminal enforcement",
         "civil_strife at minimum ORANGE; total at minimum one level above base"),
        ("Documented pattern of sexual assault targeting tourists (multiple incidents reported as pattern)",
         "crime at minimum ORANGE"),
        ("US State Dept, UK FCDO or equivalent specifically advises women to exercise extra caution or reconsider travel",
         "total at minimum one level above base"),
    ]
    elems.append(veto_floor_table(veto_items, is_veto=True))
    elems.append(Spacer(1, 2*mm))
    elems.append(veto_floor_table(floor_items, is_veto=False))
    elems.append(Spacer(1, 2*mm))
    elems += note_box(
        "Data sources: Georgetown GIWPS Women Peace & Security Index · UNODC gender-disaggregated violence statistics · "
        "UN Women country profiles · US State Dept women's travel advisories · Plan International Girls' Index"
    )
    elems.append(Spacer(1, 4*mm))

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 4: LGBTQ+
    # ─────────────────────────────────────────────────────────────────────────
    elems.append(PageBreak())
    elems += category_header("LAYER 4: LGBTQ+",
                              "Lesbian, gay, bisexual, transgender, and queer travelers. "
                              "Risks are primarily legal (criminalisation) and social (violence, discrimination). "
                              "In some countries risk is from the state; in others from civil society; in some both. "
                              "[Proposed — not yet implemented]")
    adj = [
        ("armed_conflict",      "INHERIT BASE",  "Armed conflict does not structurally discriminate by sexual orientation."),
        ("regional_instability","INHERIT BASE",  "Geopolitical dynamics are the same for all travelers."),
        ("terrorism",           "RAISE if...",   "Documented targeting of LGBTQ+ venues or individuals by organised actors (rare but exists, e.g. Pulse nightclub type incidents with organised group link)."),
        ("civil_strife",        "RAISE substantially if...", "Same-sex relations criminalised with active enforcement (arrests, prosecutions) → min RED. Government conducting active campaign against LGBTQ+ (raids, forced outing, mandatory conversion therapy). Religious or state-sponsored violence with impunity. INHERIT if country is conservative culturally but LGBTQ+ travel is tolerated without legal risk."),
        ("crime",               "RAISE if...",   "Hate crimes targeting LGBTQ+ documented as a pattern. Police routinely fail to protect or participate in persecution. Same-sex relations technically criminalised = de facto legal persecution = elevated crime risk even if rarely enforced."),
        ("health",              "RAISE if...",   "LGBTQ+-affirming healthcare unavailable. Traveler cannot safely disclose medical history (HIV status, hormone therapy). HIV-positive travelers denied entry or treatment."),
        ("infrastructure",      "INHERIT BASE",  "Physical infrastructure is the same for all travelers."),
    ]
    elems.append(layer_adj_table(adj))
    elems.append(Spacer(1, 2*mm))

    veto_items = [
        ("Same-sex relations punishable by death (Iran, Saudi, Yemen, Afghanistan, parts of Nigeria, Qatar)",
         "total = PURPLE"),
        ("Same-sex relations criminalised with active enforcement (prison sentences, documented recent prosecutions)",
         "total = RED minimum"),
    ]
    floor_items = [
        ("Same-sex relations criminalised but rarely enforced (laws on books, no recent prosecutions)",
         "civil_strife at minimum ORANGE; total at minimum one level above base"),
        ("ILGA World: 'Death penalty or flogging'", "PURPLE veto applied"),
        ("ILGA World: 'Imprisonment'",              "RED floor applied"),
        ("ILGA World: 'Other penalties'",           "ORANGE floor applied"),
    ]
    elems.append(veto_floor_table(veto_items, is_veto=True))
    elems.append(Spacer(1, 2*mm))
    elems.append(veto_floor_table(floor_items, is_veto=False))
    elems.append(Spacer(1, 2*mm))
    elems += note_box(
        "Data sources: ILGA World: State-Sponsored Homophobia report (annual) · "
        "Human Rights Watch LGBTQ+ country reports · Stonewall Global Diversity Index · Rainbow Europe rating"
    )
    elems.append(Spacer(1, 4*mm))

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 5: JOURNALISTS
    # ─────────────────────────────────────────────────────────────────────────
    elems.append(PageBreak())
    elems += category_header("LAYER 5: JOURNALISTS",
                              "Professional journalists, documentary filmmakers, photojournalists, bloggers covering news topics. "
                              "Risks: physical (conflict zones) and institutional (arrest, censorship, expulsion). "
                              "[Proposed — not yet implemented]")
    adj = [
        ("armed_conflict",      "RAISE if...",   "Journalists specifically and deliberately targeted by warring parties (documented killings of journalists in the conflict). Protected under IHL but routinely violated. → raise one level above base."),
        ("regional_instability","INHERIT BASE",  "Geopolitical dynamics are the same for all travelers."),
        ("terrorism",           "RAISE if...",   "Journalists specifically targeted by terrorist groups (Al-Shabaab, IS have targeted journalists). → raise above base if documented."),
        ("civil_strife",        "PRIMARY DIFF — START FROM BASE",
         "RSF Press Freedom Index: bottom 30 countries → RED floor. "
         "CPJ Impunity Index (journalists killed with no prosecution) → RED floor. "
         "Journalists imprisoned in past 2 years → ORANGE. "
         "Foreign journalists deported/expelled/denied entry → ORANGE. "
         "Accreditation requirements restricting independent reporting → YELLOW."),
        ("crime",               "RAISE if...",   "Journalists specifically targeted by criminal organisations for their reporting (Mexico, Philippines, parts of Latin America where cartels kill journalists). → raise above base."),
        ("health",              "INHERIT BASE",  "Healthcare access is the same. Note in narrative if field access requires going to areas with degraded infrastructure."),
        ("infrastructure",      "INHERIT BASE",  "Note in narrative if journalists need to access conflict areas with degraded infrastructure."),
    ]
    elems.append(layer_adj_table(adj))
    elems.append(Spacer(1, 2*mm))

    veto_items = [
        ("Country is in top 10 worst for press freedom (RSF bottom 10)",
         "civil_strife = RED; total = RED minimum"),
        ("Journalist killed in country in past 2 years for doing their job (CPJ database)",
         "total = at minimum one level above base"),
    ]
    floor_items = [
        ("RSF ranking in bottom 30 (countries 151–180)",
         "civil_strife at minimum ORANGE; total at minimum ORANGE"),
        ("CPJ 'Committee to Protect Journalists' ongoing concern designation",
         "civil_strife at minimum ORANGE"),
    ]
    elems.append(veto_floor_table(veto_items, is_veto=True))
    elems.append(Spacer(1, 2*mm))
    elems.append(veto_floor_table(floor_items, is_veto=False))
    elems.append(Spacer(1, 2*mm))
    elems += note_box(
        "Data sources: RSF Press Freedom Index (rsf.org/en/index) · CPJ (Committee to Protect Journalists: cpj.org) · "
        "Reporters Without Borders country cards · IFJ (International Federation of Journalists) safety reports"
    )
    elems.append(Spacer(1, 4*mm))

    # ─────────────────────────────────────────────────────────────────────────
    # LAYER 6: AID WORKERS
    # ─────────────────────────────────────────────────────────────────────────
    elems.append(PageBreak())
    elems += category_header("LAYER 6: AID WORKERS",
                              "Humanitarian aid workers, NGO staff, UN personnel, development workers. "
                              "Operate in areas others avoid; sometimes targeted for leverage (kidnapping) or ideology. "
                              "[Proposed — not yet implemented]")
    adj = [
        ("armed_conflict",      "RAISE if...",   "Aid workers/humanitarian convoys specifically targeted in the conflict (documented attacks on ICRC, MSF, UN vehicles). Humanitarian access denied by warring parties (OCHA HNO data). Aid workers killed in this specific conflict. → raise one level above base."),
        ("regional_instability","INHERIT BASE",  "Geopolitical dynamics are the same for all travelers."),
        ("terrorism",           "RAISE if...",   "Aid workers or NGO staff specifically targeted by terrorist/armed groups (kidnapping for ransom or ideological targeting — documented in Somalia, Mali, CAR). → raise above base."),
        ("civil_strife",        "RAISE if...",   "Government has expelled or banned NGOs (Ethiopia 2009 law, Russia 2012 'foreign agents' law). Aid workers face bureaucratic obstruction amounting to operational paralysis (visa denial, movement restrictions). Aid workers treated as foreign agents or spies. → raise above base."),
        ("crime",               "RAISE if...",   "Kidnapping-for-ransom specifically targeting NGO/UN workers documented in past 3 years. High perceived wealth makes aid workers specific targets even in moderate-crime countries. → raise above base."),
        ("health",              "INHERIT BASE",  "Aid workers typically have their own medical evacuation protocols and are better prepared than general travelers. Score the country's health infrastructure accurately regardless of traveler's preparation."),
        ("infrastructure",      "INHERIT BASE",  "Note in narrative if humanitarian access routes are specifically blocked or degraded."),
    ]
    elems.append(layer_adj_table(adj))
    elems.append(Spacer(1, 2*mm))

    veto_items = [
        ("Aid workers killed in country in past 2 years (AWSD database) — repeated incidents",
         "total = RED minimum"),
        ("Country has legally expelled or banned humanitarian organisations",
         "civil_strife = at minimum ORANGE; total = at minimum one level above base"),
    ]
    floor_items = [
        ("OCHA classified as 'humanitarian crisis' (Level 3 emergency)",
         "total at minimum ORANGE"),
        ("UN DSS (Department of Safety and Security) security phase 3+ for relevant region",
         "total at minimum RED for that area"),
    ]
    elems.append(veto_floor_table(veto_items, is_veto=True))
    elems.append(Spacer(1, 2*mm))
    elems.append(veto_floor_table(floor_items, is_veto=False))
    elems.append(Spacer(1, 2*mm))
    elems += note_box(
        "Data sources: AWSD (Aid Worker Security Database: aidworkersecurity.org) · "
        "OCHA Financial Tracking Service and HNO reports · UN DSS phase classifications · "
        "ICRC operational updates · MSF operational reports"
    )
    elems.append(Spacer(1, 4*mm))

    return elems


# ── Layer comparison summary table ────────────────────────────────────────────
def build_summary():
    elems = []
    elems.append(PageBreak())
    elems += section_header(
        "PART 4 — Identity Layer Comparison Summary",
        "Quick-reference: which categories each identity layer adjusts vs base"
    )

    layers   = ["jewish_israeli", "solo_women", "lgbtq", "journalists", "aid_workers"]
    cats     = ["armed_conflict", "regional_instability", "terrorism", "civil_strife",
                "crime", "health", "infrastructure"]

    # Map: (layer, cat) → symbol
    # ● = always adjusts / structural  ◑ = may adjust  ○ = inherit base  ✕ = hard veto triggers here
    matrix = {
        ("jewish_israeli",  "armed_conflict"):       "○",
        ("jewish_israeli",  "regional_instability"): "○",
        ("jewish_israeli",  "terrorism"):            "●",
        ("jewish_israeli",  "civil_strife"):         "◑",
        ("jewish_israeli",  "crime"):                "◑",
        ("jewish_israeli",  "health"):               "○",
        ("jewish_israeli",  "infrastructure"):       "○",

        ("solo_women",      "armed_conflict"):       "◑",
        ("solo_women",      "regional_instability"): "○",
        ("solo_women",      "terrorism"):            "○",
        ("solo_women",      "civil_strife"):         "●",
        ("solo_women",      "crime"):                "●",
        ("solo_women",      "health"):               "◑",
        ("solo_women",      "infrastructure"):       "○",

        ("lgbtq",           "armed_conflict"):       "○",
        ("lgbtq",           "regional_instability"): "○",
        ("lgbtq",           "terrorism"):            "◑",
        ("lgbtq",           "civil_strife"):         "●",
        ("lgbtq",           "crime"):                "◑",
        ("lgbtq",           "health"):               "◑",
        ("lgbtq",           "infrastructure"):       "○",

        ("journalists",     "armed_conflict"):       "◑",
        ("journalists",     "regional_instability"): "○",
        ("journalists",     "terrorism"):            "◑",
        ("journalists",     "civil_strife"):         "●",
        ("journalists",     "crime"):                "◑",
        ("journalists",     "health"):               "○",
        ("journalists",     "infrastructure"):       "○",

        ("aid_workers",     "armed_conflict"):       "◑",
        ("aid_workers",     "regional_instability"): "○",
        ("aid_workers",     "terrorism"):            "◑",
        ("aid_workers",     "civil_strife"):         "◑",
        ("aid_workers",     "crime"):                "◑",
        ("aid_workers",     "health"):               "○",
        ("aid_workers",     "infrastructure"):       "○",
    }

    col_w = (CONTENT_W - 42*mm) / len(layers)
    col_widths = [42*mm] + [col_w] * len(layers)

    header_style = S("Normal", fontSize=7.5, fontName="Helvetica-Bold",
                      textColor=colors.white, alignment=TA_CENTER)
    cat_style_sm = S("Normal", fontSize=7.5, fontName="Helvetica-Bold",
                      textColor=colors.HexColor("#374151"))
    cell_style   = S("Normal", fontSize=10, alignment=TA_CENTER,
                      textColor=colors.HexColor("#374151"))

    rows = [[Paragraph("CATEGORY", header_style)] +
            [Paragraph(l.replace("_", "\n"), header_style) for l in layers]]

    style_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0), HEADER_BG),
        ("GRID",          (0, 0), (-1, -1), 0.3, DIVIDER),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN",         (1, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 5),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]

    for i, cat in enumerate(cats, start=1):
        bg = colors.HexColor("#f8fafc") if i % 2 == 0 else colors.white
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
        row = [Paragraph(cat.replace("_", "_\u200b"), cat_style_sm)]
        for layer in layers:
            sym = matrix.get((layer, cat), "○")
            color_map = {"●": "#15803d", "◑": "#b45309", "○": "#9ca3af"}
            c = color_map.get(sym, "#374151")
            row.append(Paragraph(f'<font color="{c}"><b>{sym}</b></font>', cell_style))
        rows.append(row)

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    elems.append(tbl)
    elems.append(Spacer(1, 3*mm))

    # Legend
    legend_tbl = Table([[
        Paragraph('<font color="#15803d"><b>●</b></font> Always adjusts (structural risk exists)',
                  S("Normal", fontSize=8, fontName="Helvetica")),
        Paragraph('<font color="#b45309"><b>◑</b></font> May adjust (conditional on evidence)',
                  S("Normal", fontSize=8, fontName="Helvetica")),
        Paragraph('<font color="#9ca3af"><b>○</b></font> Inherit base (no structural difference)',
                  S("Normal", fontSize=8, fontName="Helvetica")),
    ]], colWidths=[CONTENT_W/3]*3)
    legend_tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ("BOX",         (0,0), (-1,-1), 0.5, DIVIDER),
        ("LEFTPADDING", (0,0), (-1,-1), 8),
        ("TOPPADDING",  (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
    ]))
    elems.append(legend_tbl)
    elems.append(Spacer(1, 6*mm))
    return elems


# ── Page number footer ────────────────────────────────────────────────────────
def add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#94a3b8"))
    page_num = canvas.getPageNumber()
    canvas.drawRightString(
        PAGE_W - MARGIN,
        12 * mm,
        f"Travint.ai Scoring Framework — Page {page_num}"
    )
    canvas.drawString(
        MARGIN,
        12 * mm,
        f"Confidential — {date.today().strftime('%d %B %Y')}"
    )
    canvas.restoreState()


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    output_path = r"C:\Applications\TravintAI\Travint_Scoring_Framework.pdf"

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=20 * mm,
        title="Travint.ai — Scoring Framework",
        author="Travint.ai",
        subject="Complete scoring threshold definitions for all categories and identity layers",
    )

    story = []
    story += build_cover()
    story += build_categories()
    story += build_total_score()
    story += build_identity_layers()
    story += build_summary()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"\n✓ PDF created: {output_path}")


if __name__ == "__main__":
    main()
