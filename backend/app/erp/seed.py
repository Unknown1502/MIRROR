"""Deterministic baseline world for the demo (build plan parts 7 and 8)."""

from __future__ import annotations

PRODUCTS = [
    # id, name, family, unit, min_transfer, max_transfer
    ("XR-17", "XR-17 Relay Controller", "XR", "units", 50, 1000),
    ("XR-17B", "XR-17B Relay Controller Rev B", "XR", "units", 50, 1000),
    ("AX-77148", "AX-77148 Axial Fan Assembly", "AX", "units", 20, 500),
    ("CN-220", "CN-220 Connector Kit", "CN", "units", 100, 5000),
    ("PS-410", "PS-410 Power Supply", "PS", "units", 25, 800),
]

LOCATIONS = [
    # id, name, city, kind, aliases (spoken forms that resolve to exactly this location)
    (
        "MUMBAI-DC",
        "Mumbai Distribution Center",
        "Mumbai",
        "DC",
        ["mumbai dc", "mumbai distribution center", "mumbai distribution centre", "mumbai-dc"],
    ),
    ("PUNE-SITE-1", "Pune Site 1", "Pune", "SITE", ["pune site 1", "pune site one", "pune 1", "pune one", "site 1", "site one", "pune-site-1"]),
    ("PUNE-SITE-2", "Pune Site 2", "Pune", "SITE", ["pune site 2", "pune site two", "pune 2", "pune two", "site 2", "site two", "pune-site-2"]),
    ("BENGALURU-HUB", "Bengaluru Hub", "Bengaluru", "HUB", ["bengaluru hub", "bangalore hub", "bengaluru-hub"]),
    ("DELHI-WAREHOUSE", "Delhi Warehouse", "Delhi", "WAREHOUSE", ["delhi warehouse", "delhi-warehouse"]),
]

# City names people actually say. A city resolves to every location in it.
CITY_ALIASES = {
    "mumbai": "Mumbai",
    "bombay": "Mumbai",
    "pune": "Pune",
    "bengaluru": "Bengaluru",
    "bangalore": "Bengaluru",
    "delhi": "Delhi",
    "new delhi": "Delhi",
}

# (location, product): (on_hand, capacity, safety_stock)
INVENTORY = {
    ("MUMBAI-DC", "XR-17"): (2000, 3000, 1200),
    ("PUNE-SITE-1", "XR-17"): (1200, 1500, 600),
    ("PUNE-SITE-2", "XR-17"): (300, 2000, 200),
    ("BENGALURU-HUB", "XR-17"): (900, 1800, 500),
    ("DELHI-WAREHOUSE", "XR-17"): (1500, 2500, 800),
    ("MUMBAI-DC", "XR-17B"): (800, 1500, 400),
    ("PUNE-SITE-1", "XR-17B"): (150, 600, 100),
    ("PUNE-SITE-2", "XR-17B"): (60, 800, 80),
    ("BENGALURU-HUB", "XR-17B"): (400, 1000, 250),
    ("DELHI-WAREHOUSE", "XR-17B"): (700, 1200, 300),
    ("MUMBAI-DC", "AX-77148"): (450, 900, 200),
    ("PUNE-SITE-1", "AX-77148"): (120, 300, 80),
    ("PUNE-SITE-2", "AX-77148"): (90, 400, 60),
    ("BENGALURU-HUB", "AX-77148"): (300, 600, 150),
    ("DELHI-WAREHOUSE", "AX-77148"): (220, 500, 120),
    ("MUMBAI-DC", "CN-220"): (8000, 12000, 3000),
    ("PUNE-SITE-1", "CN-220"): (2500, 4000, 1000),
    ("PUNE-SITE-2", "CN-220"): (1800, 5000, 800),
    ("BENGALURU-HUB", "CN-220"): (4200, 8000, 2000),
    ("DELHI-WAREHOUSE", "CN-220"): (5100, 9000, 2500),
    ("MUMBAI-DC", "PS-410"): (1100, 2000, 600),
    ("PUNE-SITE-1", "PS-410"): (300, 700, 150),
    ("PUNE-SITE-2", "PS-410"): (240, 900, 120),
    ("BENGALURU-HUB", "PS-410"): (650, 1400, 300),
    ("DELHI-WAREHOUSE", "PS-410"): (800, 1600, 400),
}

# Undirected lanes: (a, b, mode, cost_per_unit, lead_time_hours, available)
_LANES = [
    ("MUMBAI-DC", "PUNE-SITE-1", "ROAD", 1.8, 6, True),
    ("MUMBAI-DC", "PUNE-SITE-2", "ROAD", 2.1, 7, True),
    ("PUNE-SITE-1", "PUNE-SITE-2", "ROAD", 0.9, 2, True),
    ("MUMBAI-DC", "BENGALURU-HUB", "RAIL", 3.4, 26, True),
    ("MUMBAI-DC", "DELHI-WAREHOUSE", "RAIL", 3.9, 30, True),
    ("PUNE-SITE-1", "BENGALURU-HUB", "ROAD", 3.1, 20, True),
    ("PUNE-SITE-2", "BENGALURU-HUB", "ROAD", 3.0, 19, True),
    ("PUNE-SITE-1", "DELHI-WAREHOUSE", "RAIL", 4.2, 34, True),
    ("PUNE-SITE-2", "DELHI-WAREHOUSE", "RAIL", 4.3, 35, True),
    # Air freight between Bengaluru and Delhi is suspended in the baseline world.
    ("BENGALURU-HUB", "DELHI-WAREHOUSE", "AIR", 9.5, 6, False),
]

ROUTES = [
    (src, dst, mode, cost, lead, available)
    for a, b, mode, cost, lead, available in _LANES
    for src, dst in ((a, b), (b, a))
]
