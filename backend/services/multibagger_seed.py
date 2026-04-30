"""Seed list for the multi-bagger scanner universe.

Curated growth candidates — mid/small caps across secular themes where
multi-baggers have historically emerged. Intentionally excludes mega caps
(>$500B) which rarely 10x from here, and penny stocks.

Each entry is (ticker, theme). Themes drive grouping in the UI.
"""

SCANNER_SEED: list[tuple[str, str]] = [
    # AI memory / storage supercycle
    ("SNDK", "AI_MEMORY"),
    ("MU", "AI_MEMORY"),
    ("PSTG", "AI_MEMORY"),
    ("NTAP", "AI_MEMORY"),
    ("WDC", "AI_MEMORY"),
    ("SMCI", "AI_MEMORY"),

    # AI compute / accelerators / networking
    ("AVGO", "AI_COMPUTE"),
    ("AMD", "AI_COMPUTE"),
    ("ARM", "AI_COMPUTE"),
    ("ALAB", "AI_COMPUTE"),
    ("CRDO", "AI_COMPUTE"),
    ("ANET", "AI_COMPUTE"),
    ("CIEN", "AI_COMPUTE"),
    ("LITE", "AI_COMPUTE"),
    ("VRT", "AI_COMPUTE"),
    ("CRWV", "AI_COMPUTE"),
    ("NBIS", "AI_COMPUTE"),

    # AI apps / platforms
    ("PLTR", "AI_APPS"),
    ("APP", "AI_APPS"),
    ("SNOW", "AI_APPS"),
    ("MDB", "AI_APPS"),
    ("NET", "AI_APPS"),
    ("CRWD", "AI_APPS"),
    ("ZS", "AI_APPS"),
    ("S", "AI_APPS"),

    # Nuclear / power renaissance
    ("SMR", "POWER_NUCLEAR"),
    ("OKLO", "POWER_NUCLEAR"),
    ("NNE", "POWER_NUCLEAR"),
    ("VST", "POWER_NUCLEAR"),
    ("CEG", "POWER_NUCLEAR"),
    ("TLN", "POWER_NUCLEAR"),
    ("GEV", "POWER_NUCLEAR"),
    ("LEU", "POWER_NUCLEAR"),
    ("CCJ", "POWER_NUCLEAR"),
    ("URA", "POWER_NUCLEAR"),

    # Robotics / space / defense tech
    ("RKLB", "ROBOTICS_SPACE"),
    ("ASTS", "ROBOTICS_SPACE"),
    ("LUNR", "ROBOTICS_SPACE"),
    ("KTOS", "ROBOTICS_SPACE"),
    ("AVAV", "ROBOTICS_SPACE"),
    ("SYM", "ROBOTICS_SPACE"),
    ("ISRG", "ROBOTICS_SPACE"),

    # Autonomy / EV / mobility
    ("JOBY", "AUTONOMY_MOBILITY"),
    ("ACHR", "AUTONOMY_MOBILITY"),
    ("LIDR", "AUTONOMY_MOBILITY"),
    ("AUR", "AUTONOMY_MOBILITY"),

    # Fintech / crypto exposure
    ("HOOD", "FINTECH_CRYPTO"),
    ("COIN", "FINTECH_CRYPTO"),
    ("MSTR", "FINTECH_CRYPTO"),
    ("SOFI", "FINTECH_CRYPTO"),
    ("AFRM", "FINTECH_CRYPTO"),
    ("NU", "FINTECH_CRYPTO"),
    ("RIOT", "FINTECH_CRYPTO"),
    ("MARA", "FINTECH_CRYPTO"),
    ("CLSK", "FINTECH_CRYPTO"),
    ("IREN", "FINTECH_CRYPTO"),
    ("BMNR", "FINTECH_CRYPTO"),
    ("CORZ", "FINTECH_CRYPTO"),

    # Biotech / GLP-1 / obesity / oncology
    ("VKTX", "BIOTECH_GLP1"),
    ("TRML", "BIOTECH_GLP1"),
    ("ALT", "BIOTECH_GLP1"),
    ("HIMS", "BIOTECH_GLP1"),
    ("CRSP", "BIOTECH_GLP1"),
    ("BEAM", "BIOTECH_GLP1"),
    ("NTLA", "BIOTECH_GLP1"),
    ("EXEL", "BIOTECH_GLP1"),
    ("RXRX", "BIOTECH_GLP1"),

    # Quantum computing
    ("IONQ", "QUANTUM"),
    ("RGTI", "QUANTUM"),
    ("QBTS", "QUANTUM"),
    ("QUBT", "QUANTUM"),

    # SaaS / growth software
    ("DDOG", "SAAS_GROWTH"),
    ("NOW", "SAAS_GROWTH"),
    ("HUBS", "SAAS_GROWTH"),
    ("TTD", "SAAS_GROWTH"),
    ("SHOP", "SAAS_GROWTH"),
    ("RBLX", "SAAS_GROWTH"),
    ("DUOL", "SAAS_GROWTH"),
    ("SEZL", "SAAS_GROWTH"),
    ("FICO", "SAAS_GROWTH"),

    # Recent IPOs / spinoffs / re-listings (<24 months old where possible)
    ("RDDT", "RECENT_IPO_SPINOFF"),
    ("CART", "RECENT_IPO_SPINOFF"),
    ("KVUE", "RECENT_IPO_SPINOFF"),
    ("SOLV", "RECENT_IPO_SPINOFF"),
    ("GEHC", "RECENT_IPO_SPINOFF"),
    ("VTRS", "RECENT_IPO_SPINOFF"),
    ("BIRK", "RECENT_IPO_SPINOFF"),
    ("KLAR", "RECENT_IPO_SPINOFF"),

    # Energy / commodity / infrastructure
    ("UEC", "POWER_NUCLEAR"),
    ("FLR", "POWER_NUCLEAR"),
    ("PWR", "POWER_NUCLEAR"),

    # Consumer / disruptive
    ("CAVA", "SAAS_GROWTH"),
    ("ELF", "SAAS_GROWTH"),
    ("ANF", "SAAS_GROWTH"),
    ("CELH", "SAAS_GROWTH"),
]
