"""International Cost of Capital App.

Compares a project's cost of equity and WACC in USD and in the currency
of a selected country. Formulas follow Case Packet 2026, part 9.

Data sources (fetched live, with a built-in fallback):
  1. Damodaran: unlevered industry betas (betas.xls)
  2. OECD: statutory corporate income tax rates (embedded)
  3. Damodaran: BBB (Baa2) spread, large non-financial service firms
  4. Trading Economics: 10-year government bond yields (embedded)
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

# Embedded 10-year government bond yields in percent, Trading Economics (16 Nov 2026).
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
YIELDS_ASOF = "Yields as of 16 Nov 2026 (Trading Economics)"

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
    "Armenia": 18, "Faroe Islands": 1