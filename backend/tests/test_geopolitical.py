"""Geopolitical event detection (utils/geopolitical.py).

Real headlines from 2026-10-05 .. 2026-10-12. Before the fix, substring matching of two keywords
anywhere in headline + summary turned "3M's Margins Improve Amid Cost Pressures" into a trade war
and "What Cheaper Gas Would Mean For Costco's Sales" into an oil disruption, which cost every chip
stock 20 conviction points (MRVL, 2026-10-12).
"""
from __future__ import annotations

import pytest

from utils.geopolitical import _count_keyword_matches, detect_and_score, detect_geopolitical_events


def events(headline: str, summary: str = "", sentiment: float = 0.5) -> list[str]:
    found = detect_geopolitical_events([{"headline": headline, "summary": summary, "sentiment_score": sentiment}])
    return sorted(e.event_type.value for e in found)


@pytest.mark.parametrize("headline, summary", [
    # company results / business stories that merely mention costs, tariffs, gas or missiles
    ("3M's Margins Improve Amid Cost Pressures: Can the Trend Continue?",
     "Tariffs and higher input costs weighed on industrial peers; tariffs remain a headwind."),
    ("What Cheaper Gas Would Mean For Costco's Sales And Margins", "Lower gas prices and fuel costs could lift traffic."),
    ("GE Vernova Surges 9.4% in a Month: Hold or Fold the Stock?", "Tariff exposure and trade tension are risks."),
    ("3 Electric Vehicle Stocks To Watch In October 2026", "Oil price swings and fuel costs drive EV demand."),
    ("Boeing Wins $14.7 Bil Missile Deal. U.S. Sends Resources To Middle East As Conflict Escalates.", ""),
    ("RTX Corporation (RTX)'s $20.7 Billion Missile Deal: Investors Should Watch Production, Not the Headline", ""),
    ("Becton Dickinson to Invest $19 Billion in U.S. in Exchange for Tariff Relief", "Tariffs on medical devices."),
    ("Constellation Energy Soars 12% on Google Nuclear Deal for 890 MW; Vistra Jumps 8%", ""),
    ("Google Faces £1 Billion Antitrust Lawsuit in London Over Play Store Fees", ""),
    # whole-word matching: no "war" inside "software", no price war, deregulation is not a crackdown
    ("Software stocks rally on AI demand", "Software names led the Nasdaq higher."),
    ("Streaming price war heats up as Netflix cuts prices", ""),
    ("Deregulation push lifts bank stocks", "Deregulation hopes boosted financials."),
])
def test_business_stories_are_not_geopolitical_events(headline, summary):
    assert events(headline, summary) == []


@pytest.mark.parametrize("headline, summary, expected", [
    ("US raises tariffs on Chinese chips, Beijing vows retaliation",
     "The trade war escalated as new tariffs hit semiconductors.", "TRADE_WAR"),
    ("Commerce tightens chip export controls on China", "New export control rules restrict AI chip export to China.",
     "TRADE_WAR"),
    ("Oil prices climb above $100 on Strait of Hormuz attacks, Gulf storm threat", "", "OIL_DISRUPTION"),
    ("OPEC+ agrees to deeper oil production cuts", "OPEC+ will cut oil supply by 1m bpd.", "OIL_DISRUPTION"),
    ("Chevron CEO Mike Wirth warns Trump diesel export ban would hurt supply", "", "OIL_DISRUPTION"),
    ("Missile strikes hit Kyiv as Russia escalates war in Ukraine", "Troops advanced overnight.", "MILITARY_CONFLICT"),
    ("Stock Market Today: Futures Decline as Pentagon Prepares for Potential Military Strike", "", "MILITARY_CONFLICT"),
    ("Yemen's Houthis say they attacked Riyadh airport with ballistic missile - Reuters",
     "The strike marks an escalation in the regional conflict.", "MILITARY_CONFLICT"),
    ("US imposes fresh sanctions on Iran's shadow fleet - Reuters",
     "The Treasury blacklisted tankers carrying Iranian oil.", "SANCTIONS"),
    ("US and China reach trade deal, roll back tariffs", "A trade agreement was signed.", "DIPLOMATIC_BREAKTHROUGH"),
])
def test_real_geopolitical_headlines_are_detected(headline, summary, expected):
    assert expected in events(headline, summary)


def test_a_lone_anchor_word_without_support_is_not_enough():
    """One generic anchor word and nothing else in headline or summary stays below the 2-hit bar."""
    assert events("US imposes fresh sanctions on Iran's shadow fleet - Reuters") == []


def test_relief_headlines_are_not_trade_war_escalations():
    assert "TRADE_WAR" not in events("US and China reach trade deal, roll back tariffs", "Tariffs will be lowered.")
    assert "SANCTIONS" not in events("EU lifts sanctions on Venezuelan oil", "Sanctions relief for exporters.")


def test_fuel_export_bans_are_energy_events_not_trade_wars():
    got = events("The Trump Administration Considered a Diesel Export Ban. Chevron's CEO Called The Notion 'Unwise.'")
    assert "TRADE_WAR" not in got and "OIL_DISRUPTION" in got


def test_keyword_counting_is_whole_word_and_counts_a_phrase_once():
    kws = ["tariff", "trade war", "war", "missile", "missile attack"]
    assert _count_keyword_matches("tariffs", kws) == 1                 # plural is the same keyword
    assert _count_keyword_matches("software toward", kws) == 0          # no substring hits
    assert _count_keyword_matches("missile attack", kws) == 1           # phrase + inner word merge
    assert _count_keyword_matches("trade war over tariffs", kws) == 2


def test_the_mrvl_false_signals_are_gone():
    """The two articles behind MRVL's -20 on 2026-10-12 no longer score anything for semis."""
    arts = [
        {"headline": "3M's Margins Improve Amid Cost Pressures: Can the Trend Continue?",
         "summary": "Tariffs and higher costs; tariffs remain a headwind.", "sentiment_score": 0.83},
        {"headline": "What Cheaper Gas Would Mean For Costco's Sales And Margins",
         "summary": "Lower gas prices and fuel costs.", "sentiment_score": 0.49},
    ]
    assert detect_and_score(arts, "AI/Semiconductors") == []
