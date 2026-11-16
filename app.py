"""International Cost of Capital App.

Compares a project's cost of equity and WACC in USD and in the currency
of a selected country. Formulas follow Case Packet 2026, part 9.

Data sources (fetched live, with a built-in fallback):
  1. Damodaran: unlevered industry betas (betas.xls)
  2. OECD: statutory corporate income tax rates (embedded)
  3. Damodaran: BBB (Baa2) spread, large non-financial service firms
  4. Trading Economics: 10-year government bond yields (live with embedded fallback)
"""

import io
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
URL_BETAS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
URL_OECD = "https://stat.link/0qkxo1"  # OECD Table II.1 Excel file (embedded in this app)
URL_RATINGS = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
URL_BONDS = "https://tradingeconomics.com/bonds"
UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5  # percent
DEFAULT_DEBT = 30  # percent

# Fallback data. Source: Case Packet 2026, part 9. Used only if a fetch fails.
FALLBACK_BETAS = {DEFAULT_INDUSTRY: 0.80}  # illustrative (packet example)
FALLBACK_SPREAD = 1.50  # percent (packet example)

# 10-year government bond yields in percent, Trading Economics (16 Nov 2026).
# Country names match EMBEDDED_TAX; entries absent from EMBEDDED_TAX (Russia,
# Taiwan, Euro area) are filtered out of the dropdown automatically.
EMBEDDED_YIELDS = {
    # Americas
    "United States": 5.1340,
    "Brazil": 15.3350,
    "Canada": 3.8240,
    "Mexico": 9.3080,
    "Peru": 6.2600,
    "Chile": 6.0100,
    "Colombia": 12.6980,
    # Europe
    "United Kingdom": 5.3688,
    "Germany": 3.3897,
    "Russia": 16.7400,
    "Italy": 4.4560,
    "France": 4.9480,
    "Switzerland": 0.5720,
    "Turkey": 33.5200,
    "Finland": 3.7680,
    "Lithuania": 4.3250,
    "Netherlands": 3.4820,
    "Norway": 4.4230,
    "Poland": 6.3250,
    "Portugal": 3.8320,
    "Romania": 7.1200,
    "Slovakia": 4.3100,
    "Slovenia": 3.9540,
    "Spain": 3.9590,
    "Sweden": 3.0980,
    "Greece": 4.2980,
    "Hungary": 5.5250,
    "Iceland": 6.8100,
    "Ireland": 3.5270,
    "Austria": 3.6220,
    "Belgium": 4.3830,
    "Croatia": 3.8668,
    "Czech Republic": 5.2580,
    "Denmark": 3.1820,
    "Euro area": 3.9000,
    # Asia
    "Japan": 2.8880,
    "India": 7.3120,
    "China": 1.6181,
    "Singapore": 2.3296,
    "South Korea": 3.9870,
    "Taiwan": 1.8600,
    "Thailand": 1.9950,
    "Vietnam": 4.5726,
    "Hong Kong": 3.9940,
    "Indonesia": 7.6070,
    "Israel": 4.1830,
    "Malaysia": 3.8628,
    "Pakistan": 12.6040,
    "Philippines": 7.7670,
    # Oceania
    "Australia": 5.1250,
    "New Zealand": 4.7740,
    # Africa
    "South Africa": 8.7150,
    "Zambia": 14.7400,
    "Kenya": 12.5000,
    "Nigeria": 16.5980,
}
YIELDS_FALLBACK_ASOF = "Yields as of 16 Nov 2026 (Trading Economics)"

# Raw data: OECD Corporate Tax Statistics 2024, Table II.1 (Figure 2.1), statutory corporate
# income tax rates for 2024 in percent (combined central and sub-central).
# File: https://stat.link/0qkxo1 (version 1, updated 11-Jul-2024).
EMBEDDED_TAX = {
    "Anguilla": 0, "Bahrain": 0, "Bahamas": 0, "Belize": 0, "Bermuda": 0, "Cayman Islands": 0,
    "Guernsey": 0, "Isle of Man": 0, "Jersey": 0, "Turks and Caicos Islands": 0,
    "British Virgin Islands": 0, "United Arab Emirates": 9, "Barbados": 9, "Hungary": 9,
    "Andorra": 10, "Bulgaria": 10, "Bosnia and Herzegovina": 10, "North Macedonia": 10,
    "Paraguay": 10, "Qatar": 10, "Macau": 12, "Gibraltar": 12.5, "Ireland": 12.5,
    "Liechtenstein": 12.5, "Albania": 15, "Georgia": 15, "Kuwait": 15, "Lithuania": 15,
    "Maldives": 15, "Montenegro": 15, "Mauritius": 15, "Oman": 15, "Serbia": 15, "Tunisia": 15,
    "Uzbekistan": 15, "Romania": 16, "Hong Kong": 16.5, "Singapore": 17, "San Marino": 17,
    "Armenia": 18, "Faroe Islands": 18, "Croatia": 18, "Ukraine": 18, "Brunei": 18.5,
    "Poland": 19, "Switzerland": 19.6081, "Azerbaijan": 20, "Cook Islands": 20, "Estonia": 20,
    "Finland": 20, "Jordan": 20, "Kazakhstan": 20, "Latvia": 20, "Saudi Arabia": 20,
    "Thailand": 20, "Turkey": 20, "Vietnam": 20, "Sweden": 20.6, "Czech Republic": 21,
    "Iceland": 21, "Slovakia": 21, "Aruba": 22, "Botswana": 22, "Curacao": 22, "Denmark": 22,
    "Greece": 22, "Indonesia": 22, "Norway": 22, "Slovenia": 22, "Cabo Verde": 22.44,
    "Egypt": 22.5, "Austria": 23, "Israel": 23, "Malaysia": 24, "Luxembourg": 24.94,
    "Angola": 25, "Antigua and Barbuda": 25, "Belgium": 25, "China": 25, "Ivory Coast": 25,
    "Djibouti": 25, "Dominica": 25, "Spain": 25, "United Kingdom": 25, "Jamaica": 25,
    "Saint Kitts and Nevis": 25, "Liberia": 25, "Monaco": 25, "Mongolia": 25, "Mauritania": 25,
    "Panama": 25, "Philippines": 25, "Sierra Leone": 25, "Seychelles": 25, "Uruguay": 25,
    "India": 25.17, "United States": 25.6314, "Netherlands": 25.8, "France": 25.825,
    "Canada": 26.14, "South Korea": 26.4, "Greenland": 26.5, "Chile": 27,
    "Dominican Republic": 27, "Togo": 27, "Samoa": 27, "South Africa": 27,
    "Burkina Faso": 27.5, "Eswatini": 27.5, "Italy": 27.81, "Congo": 28, "Grenada": 28,
    "New Zealand": 28, "Saint Vincent and the Grenadines": 28, "Pakistan": 29, "Peru": 29.5,
    "Japan": 29.74, "Germany": 29.9271, "Argentina": 30, "Australia": 30, "Benin": 30,
    "Democratic Republic of the Congo": 30, "Costa Rica": 30, "Gabon": 30, "Honduras": 30,
    "Haiti": 30, "Kenya": 30, "Saint Lucia": 30, "Sri Lanka": 30, "Mexico": 30,
    "Montserrat": 30, "Nigeria": 30, "Papua New Guinea": 30, "Senegal": 30,
    "Trinidad and Tobago": 30, "Zambia": 30, "Portugal": 31.5, "Morocco": 32, "Namibia": 32,
    "Cameroon": 33, "Brazil": 34, "Colombia": 35, "Malta": 35,
}
TAX_ASOF = "2024 statutory rates (OECD Table II.1; file updated 11 Jul 2024)"

ALIASES = {
    "u.s.": "united states", "us": "united states", "usa": "united states",
    "united states of america": "united states",
    "u.k.": "united kingdom", "uk": "united kingdom",
    "türkiye": "turkey", "turkiye": "turkey",
    "czechia": "czech republic", "slovak republic": "slovakia",
    "korea, rep.": "south korea", "korea": "south korea",
    "korea, south": "south korea", "republic of korea": "south korea",
    "dominican r.": "dominican republic", "uae": "united arab emirates",
    "hong kong sar, china": "hong kong", "viet nam": "vietnam",
    "russian federation": "russia", "congo, rep.": "congo",
}


def norm(name: str) -> str:
    """Normalize a country name so sources can be matched."""
    s = re.sub(r"[\*\u2020\u2021]+", "", str(name)).strip().lower()
    return ALIASES.get(s, s)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def to_float(x):
    """Convert text such as '1.11%' to a number. Return None on failure."""
    try:
        return float(str(x).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


# ----------------------------------------------------------------------
# Data fetch
#  - No lxml or bs4 needed: a small built-in HTML table parser is used.
#  - A live fetch that fails is NOT cached. The app tries again on the next run.
#  - Successful fetches are cached for a few hours. The refresh button clears them.
# ----------------------------------------------------------------------
URL_BETAS_HTML = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
BROWSER_HEADERS = {
    **UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}


def http_get(url, referer=None, accept=None):
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers["Referer"] = referer
    if accept:
        headers["Accept"] = accept
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r


class _TableParser(HTMLParser):
    """Collect HTML tables as lists of rows of cell text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._stack, self._row, self._cell = [], [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._stack and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._stack:
            if self._row:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse_tables(html):
    p = _TableParser()
    p.feed(html)
    return p.tables


def live_or_fallback(live, fallback):
    """Run a live fetch. On any error, return the fallback with the error text."""
    try:
        return live()
    except Exception as exc:
        out = fallback()
        out.update({"ok": False, "fetched": now_utc(),
                    "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        return out


# ---- 1. Betas --------------------------------