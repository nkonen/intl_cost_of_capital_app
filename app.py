"""International Cost of Capital App.

Compares a project's cost of equity and WACC in USD and in the currency
of a selected country. Formulas follow Case Packet 2026, part 9.

Data sources (fetched live, with a built-in fallback):
  1. Damodaran: unlevered industry betas (betas.xls)
  2. OECD: statutory corporate income tax rates
  3. Damodaran: BBB (Baa2) spread, large non-financial service firms
  4. Trading Economics: 10-year government bond yields
"""

import io
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
URL_BETAS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
URL_OECD = "https://stat.link/0qkxo1"  # OECD Table II.1 Excel file (embedded in this app)
URL_RATINGS = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
URL_BONDS = "https://tradingeconomics.com/bonds"  # source of the embedded yields
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
# Raw data: 10-year government bond yields in percent, Trading Economics,
# as printed in Case Packet 2026, part 9 (yields dated 6 Nov 2026).
# To add a country, add one line: "Country": yield,
EMBEDDED_YIELDS = {
    "Brazil": 15.2, "Colombia": 12.8, "Mexico": 9.1, "India": 7.2,
    "Poland": 6.2, "United Kingdom": 5.2, "Australia": 5.0,
    "United States": 4.9, "France": 4.8, "Slovakia": 4.2, "Portugal": 3.7,
    "Canada": 3.7, "Germany": 3.3, "Japan": 2.6, "China": 1.6,
    "Switzerland": 0.4,
}
YIELDS_ASOF = "Yields as of 6 Nov 2026 (Trading Economics, via Case Packet 2026 part 9)"
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
from html.parser import HTMLParser

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


# ---- 1. Betas --------------------------------------------------------
def _betas_from_xls():
    import xlrd  # noqa: F401  (raises ImportError if the package is missing)
    r = http_get(URL_BETAS)
    raw = pd.read_excel(io.BytesIO(r.content), sheet_name="Industry Averages",
                        header=None, engine="xlrd")
    mask = raw.apply(
        lambda row: row.astype(str).str.strip().eq("Industry Name").any(), axis=1)
    hdr = raw.index[mask][0]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = [str(c).strip() for c in raw.iloc[hdr]]
    df = df.dropna(subset=["Industry Name"])
    df["Industry Name"] = df["Industry Name"].astype(str).str.strip()
    df["Unlevered beta"] = pd.to_numeric(df["Unlevered beta"], errors="coerce")
    df = df.dropna(subset=["Unlevered beta"])
    keep = [c for c in ("Industry Name", "Number of firms", "Beta", "D/E Ratio",
                        "Effective Tax rate", "Unlevered beta") if c in df.columns]
    asof = ""
    for v in raw.iloc[:hdr].astype(str).values.ravel():
        if re.search(r"(date|as of|updated)", v, re.I) and v != "nan":
            asof = v.strip()
            break
    return df[keep].reset_index(drop=True), asof or "see file header", "betas.xls"


def _betas_from_html():
    r = http_get(URL_BETAS_HTML)
    for rows in parse_tables(r.text):
        for i, row in enumerate(rows):
            low = [c.strip().lower() for c in row]
            if "industry name" in low and "unlevered beta" in low:
                idx = {}
                for j, c in enumerate(low):
                    idx.setdefault(c, j)
                names = {"Industry Name": "industry name", "Number of firms": "number of firms",
                         "Beta": "beta", "D/E Ratio": "d/e ratio",
                         "Effective Tax rate": "effective tax rate",
                         "Unlevered beta": "unlevered beta"}
                recs = []
                for data in rows[i + 1:]:
                    rec = {}
                    for out_name, key in names.items():
                        j = idx.get(key)
                        if j is None or j >= len(data):
                            continue
                        rec[out_name] = (data[j].strip() if out_name == "Industry Name"
                                         else to_float(data[j]))
                    if rec.get("Industry Name") and rec.get("Unlevered beta") is not None:
                        recs.append(rec)
                if len(recs) >= 20:
                    m = re.search(r"(Data used is as of[^<\n]{0,40}|Date of [Aa]nalysis[^<\n]{0,60})",
                                  r.text)
                    return pd.DataFrame(recs), (m.group(1).strip() if m else "see page header"), "Betas.html"
    raise ValueError("Industry table not found on the Damodaran beta page.")


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _betas_live():
    errors = []
    for fn in (_betas_from_xls, _betas_from_html):
        try:
            df, asof, via = fn()
            return {"ok": True, "df": df, "asof": f"{asof} (via {via})",
                    "fetched": now_utc(), "error": None}
        except Exception as exc:
            errors.append(f"{fn.__name__}: {type(exc).__name__}: {str(exc)[:150]}")
    raise RuntimeError(" | ".join(errors))


def fetch_betas():
    def fb():
        df = pd.DataFrame({"Industry Name": list(FALLBACK_BETAS),
                           "Unlevered beta": list(FALLBACK_BETAS.values())})
        return {"df": df, "asof": "Case Packet 2026 (fallback)"}
    return live_or_fallback(_betas_live, fb)


# ---- 2. Corporate tax rates (embedded OECD raw data) ----------------
def fetch_tax():
    """Tax rates come from the OECD Excel file embedded in this app."""
    return {"ok": True, "embedded": True,
            "tax": {norm(k): float(v) for k, v in EMBEDDED_TAX.items()},
            "names": list(EMBEDDED_TAX),
            "asof": TAX_ASOF, "fetched": "n/a (embedded raw data)", "error": None}


# ---- 3. BBB spread ---------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _spread_live():
    r = http_get(URL_RATINGS)
    for rows in parse_tables(r.text):
        for row in rows:
            for i, c in enumerate(row):
                if "Baa2/BBB" in c and i + 1 < len(row):
                    val = to_float(row[i + 1])
                    if val is not None:
                        return {"ok": True, "spread": val,
                                "asof": "Large non-financial service firms, Baa2/BBB",
                                "fetched": now_utc(), "error": None}
    raise ValueError("Baa2/BBB row not found on the Damodaran ratings page.")


def fetch_spread():
    return live_or_fallback(
        _spread_live,
        lambda: {"spread": FALLBACK_SPREAD, "asof": "Case Packet 2026 example (fallback)"})


# ---- 4. Government bond yields (embedded raw data) -------------------
def fetch_yields():
    return {"ok": True, "embedded": True,
            "yields": dict(EMBEDDED_YIELDS), "asof": YIELDS_ASOF,
            "fetched": "n/a (embedded raw data)", "error": None}


# ----------------------------------------------------------------------
# Calculation. All rates are decimals (0.05 = 5%).
# ----------------------------------------------------------------------
def compute(beta_u, tax, erp, d, rf_us, rf_fx, spread_us, spread_fx=None):
    """Return USD and foreign-currency cost of equity, debt and WACC."""
    de = d / (1 - d)                               # D/E from debt share
    beta_e = beta_u * (1 + (1 - tax) * de)         # relever beta
    re_usd = rf_us + beta_e * erp                  # CAPM
    rd_usd = rf_us + spread_us                     # pre-tax debt cost
    wacc_usd = (1 - d) * re_usd + d * rd_usd * (1 - tax)

    f = (1 + rf_fx) / (1 + rf_us)                  # conversion factor
    re_fx = (1 + re_usd) * f - 1
    s_fx = spread_us * f if spread_fx is None else spread_fx
    rd_fx = rf_fx + s_fx
    wacc_fx = (1 - d) * re_fx + d * rd_fx * (1 - tax)
    return {
        "D/E": de, "beta_e": beta_e, "factor": f,
        "re_usd": re_usd, "rd_usd": rd_usd,
        "rd_usd_at": rd_usd * (1 - tax), "wacc_usd": wacc_usd,
        "re_fx": re_fx, "s_fx": s_fx, "rd_fx": rd_fx,
        "rd_fx_at": rd_fx * (1 - tax), "wacc_fx": wacc_fx,
    }


def pct(x, nd=2):
    return f"{x * 100:.{nd}f}%"


# ----------------------------------------------------------------------
# App
# ----------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", layout="wide")
st.title("International Cost of Capital")
st.caption("Compare a project's cost of equity and WACC in USD and in a foreign currency.")

betas = fetch_betas()
taxes = fetch_tax()
spread = fetch_spread()
yields = fetch_yields()

# ---- Sidebar ---------------------------------------------------------
sb = st.sidebar
if sb.button("Refresh data", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
sb.caption("Betas and BBB spread fetched: " + betas["fetched"])
for label, src in (("Betas", betas), ("BBB spread", spread)):
    if not src["ok"]:
        sb.warning(f"{label}: live fetch failed. Fallback data is in use.")
sb.caption("Tax rates (OECD 2024 file) and bond yields (6 Nov 2026) are embedded data.")

sb.header("Selections")
sb.caption("Each box starts with the fetched value. Type in any box to change it.")
missing = []


def field(label, auto, key, lo, hi, step, fmt="%.2f", required=True):
    """Text box that starts at the fetched value and can be edited at once.

    The key includes the fetched value, so the box resets when that value changes
    (new industry, new country, or refreshed data).
    """
    box = sb.number_input(label, min_value=lo, max_value=hi,
                          value=None if auto is None else float(auto),
                          step=step, format=fmt, key=f"{key}|{auto}",
                          placeholder="Enter a value")
    if box is None and required:
        missing.append(label)
    return box


# ---- 1. Industry -----------------------------------------------------
sb.subheader("1. Industry")
industries = sorted(betas["df"]["Industry Name"].tolist())
ind_index = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
industry = sb.selectbox("Industry", industries, index=ind_index)
auto_beta = float(betas["df"].loc[betas["df"]["Industry Name"] == industry,
                                  "Unlevered beta"].iloc[0])
beta_u = field("Unlevered beta", auto_beta, f"beta_{industry}", 0.0, 5.0, 0.01, "%.4f")

# ---- 2. Target country ----------------------------------------------
sb.divider()
sb.subheader("2. Target country")
yield_map = {norm(k): v for k, v in yields["yields"].items()}
display_names = {norm(k): k for k in taxes["names"]}
display_names.update({norm(k): k for k in yields["yields"]})
display_names["united states"] = "United States"
countries = sorted(display_names.values())
c_index = countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0
country = sb.selectbox("Target country", countries, index=c_index)
is_us = norm(country) == "united states"

tax_basis = sb.radio("Project tax rate", ["United States", "Selected country"],
                     horizontal=True)
tax_us_pct = field("U.S. tax rate (%)", taxes["tax"].get("united states"), "tax_us",
                   0.0, 60.0, 0.1, "%.2f")
if is_us:
    tax_fx_pct = tax_us_pct
else:
    tax_fx_pct = field(f"{country} tax rate (%)", taxes["tax"].get(norm(country)),
                       f"tax_{country}", 0.0, 60.0, 0.1, "%.2f",
                       required=(tax_basis == "Selected country"))
    yld_fx_pct = field(f"{country} 10-year yield (%)", yield_map.get(norm(country)),
                       f"yld_{country}", -2.0, 100.0, 0.01, "%.2f")

# ---- 3. Risk information --------------------------------------------
sb.divider()
sb.subheader("3. Risk information")
erp_pct = sb.number_input("Equity risk premium (%)", 0.0, 30.0, DEFAULT_ERP, 0.1)
debt_pct = sb.slider("Target debt share (%)", 0, 90, DEFAULT_DEBT, 1)
yld_us_pct = field("U.S. 10-year yield (%)", yield_map.get("united states"), "yld_us",
                   -2.0, 50.0, 0.01, "%.2f")
if is_us:
    yld_fx_pct = yld_us_pct

# ---- 4. Spread information ------------------------------------------
sb.divider()
sb.subheader("4. Spread information")
spread_us_pct = field("USD BBB spread (%)", spread["spread"], "spread_us",
                      0.0, 20.0, 0.01, "%.4f")

if missing:
    st.title("International Cost of Capital")
    st.warning("Enter a value in the sidebar for: " + ", ".join(missing) + ".")
    st.stop()

tax_used_pct = tax_us_pct if tax_basis == "United States" else tax_fx_pct

# Foreign-currency spread: starts at the automatic value, then can be edited.
f_auto = (1 + yld_fx_pct / 100) / (1 + yld_us_pct / 100)
auto_spread_fx_pct = spread_us_pct * f_auto
spread_fx_box = field("Foreign-currency spread (%)", auto_spread_fx_pct, "spread_fx",
                      0.0, 50.0, 0.01, "%.4f")
spread_fx_pct = (None if spread_fx_box is None
                 or abs(spread_fx_box - auto_spread_fx_pct) < 1e-9 else spread_fx_box)
fx_spread_on = spread_fx_pct is not None
sb.caption("Foreign spread starts at USD spread × (1 + foreign yield) / (1 + U.S. yield).")

# ---- Compute ---------------------------------------------------------
params = dict(
    beta_u=beta_u, tax=tax_used_pct / 100, erp=erp_pct / 100,
    d=debt_pct / 100, rf_us=yld_us_pct / 100, rf_fx=yld_fx_pct / 100,
    spread_us=spread_us_pct / 100,
)
spread_fx = None if spread_fx_pct is None else spread_fx_pct / 100
res = compute(**params, spread_fx=spread_fx)

tab_res, tab_assume, tab_ver = st.tabs(
    ["Results", "Assumptions and sources", "Verification"]
)

# ---- Results tab -----------------------------------------------------
with tab_res:
    usd_label = "USD (U.S. dollar)"
    fx_label = f"Foreign currency ({country})"

    st.subheader(f"{industry}: {country}")
    st.write(f"Project tax rate: **{tax_used_pct:.2f}%** ({tax_basis}). "
             f"Debt share: **{debt_pct}%**. Equity risk premium: **{erp_pct:.2f}%**.")
    st.markdown(f"**Left side = {usd_label}. Right side = {fx_label}.**")
    if is_us:
        st.info("The selected country is the United States. Both sides show the same result.")

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown(f"### {usd_label}")
        st.metric("Cost of equity (USD)", pct(res["re_usd"]))
        st.metric("WACC (USD)", pct(res["wacc_usd"]))
    with c2.container(border=True):
        st.markdown(f"### {fx_label}")
        st.metric(f"Cost of equity ({country} currency)", pct(res["re_fx"]))
        st.metric(f"WACC ({country} currency)", pct(res["wacc_fx"]))

    rows = [
        ("Unlevered beta", f"{beta_u:.4f}", f"{beta_u:.4f}"),
        ("Debt / equity", f"{res['D/E']:.4f}", f"{res['D/E']:.4f}"),
        ("Levered (equity) beta", f"{res['beta_e']:.4f}", f"{res['beta_e']:.4f}"),
        ("10-year government yield", pct(params["rf_us"]), pct(params["rf_fx"])),
        ("Equity risk premium", pct(params["erp"]), pct(params["erp"])),
        ("Cost of equity", pct(res["re_usd"], 4), pct(res["re_fx"], 4)),
        ("Credit spread", pct(params["spread_us"], 4), pct(res["s_fx"], 4)),
        ("Pre-tax cost of debt", pct(res["rd_usd"], 4), pct(res["rd_fx"], 4)),
        ("After-tax cost of debt", pct(res["rd_usd_at"], 4), pct(res["rd_fx_at"], 4)),
        ("Debt share", pct(params["d"], 1), pct(params["d"], 1)),
        ("Tax rate", pct(params["tax"], 2), pct(params["tax"], 2)),
        ("WACC", pct(res["wacc_usd"], 4), pct(res["wacc_fx"], 4)),
    ]
    table = pd.DataFrame(rows, columns=["Item", usd_label, fx_label]).set_index("Item")
    st.table(table)
    st.caption(f"Conversion factor F = (1 + {country} yield) / (1 + U.S. yield) = {res['factor']:.6f}")

    # Download
    out = table.reset_index()
    inputs = pd.DataFrame({
        "Input": ["Industry", "Country", "Tax basis", "Beta and spread fetch time", "Foreign spread edited"],
        "Value": [industry, country, tax_basis, betas["fetched"],
                  "yes" if fx_spread_on else "no"],
    })
    csv = out.to_csv(index=False) + "\n" + inputs.to_csv(index=False)
    st.download_button("Download results (CSV)", csv, "cost_of_capital_results.csv", "text/csv")

# ---- Assumptions and sources ----------------------------------------
with tab_assume:
    st.subheader("Sources and dates")
    src_tbl = pd.DataFrame([
        ["Unlevered industry betas", "Damodaran (NYU Stern)", URL_BETAS,
         betas["asof"], betas["fetched"], "Live" if betas["ok"] else "Fallback"],
        ["Corporate tax rates", "OECD", URL_OECD,
         taxes["asof"], taxes["fetched"], "Embedded file"],
        ["BBB (Baa2) USD spread", "Damodaran (NYU Stern)", URL_RATINGS,
         spread["asof"], spread["fetched"], "Live" if spread["ok"] else "Fallback"],
        ["10-year government yields", "Trading Economics", URL_BONDS,
         yields["asof"], yields["fetched"], "Embedded data"],
    ], columns=["Data", "Source", "URL", "Data date", "Fetched", "Status"])
    st.dataframe(src_tbl, use_container_width=True, hide_index=True)
    for label, src in (("Betas", betas), ("Spread", spread)):
        if src["error"]:
            st.caption(f"{label} fetch error: {src['error']}")

    st.subheader("Main assumptions")
    st.markdown(
        """
- **Risk benchmark.** Risk is measured against the U.S. market. U.S. industry betas stand in for project risk.
- **Beta.** Unlevering and relevering assume zero debt beta. The tax shield has zero beta.
- **Relever.** `βE = βU × [1 + (1 − τ) × D/E]`, with `D/E = d / (1 − d)`.
- **Cost of equity (USD).** CAPM: `Re = Rf + βE × ERP`. The U.S. 10-year yield is the risk-free proxy. No country risk premium is added.
- **Cost of debt (USD).** `Rd = Rf + BBB spread`. Interest is fully deductible when paid.
- **WACC.** `(1 − d) × Re + d × Rd × (1 − τ)`. The same tax rate, debt share, and ERP apply in both currencies.
- **Currency conversion.** Equal real rates and relative PPP link yields to expected depreciation. `1 + Re* = (1 + Re) × (1 + Rf*) / (1 + Rf)`.
- **Foreign spread.** The box starts at `s* = s × (1 + Rf*) / (1 + Rf)`. You can type another value. This does not change USD results.
- **Government yields** include liquidity and term premia. They can include sovereign default risk.
- **Currency risk.** Exchange-rate betas are near zero, so currency risk gets no separate premium.
        """
    )

# ---- Verification ----------------------------------------------------
with tab_ver:
    st.subheader("Built-in checks")
    st.caption("These checks run live with the current inputs, except check 1.")
    tol = 1e-12
    checks = []

    # 1. Packet example (Colombia). Expected values come from the Case Packet.
    ex = compute(beta_u=0.80, tax=0.35, erp=0.065, d=0.30, rf_us=0.04,
                 rf_fx=0.12, spread_us=0.015)
    exp = {"re_usd": 0.106486, "wacc_usd": 0.085265, "re_fx": 0.1916, "wacc_fx": 0.160670}
    ok1 = all(abs(ex[k] - v) < 5e-7 for k, v in exp.items())
    checks.append(["1. Packet example (Colombia)", "Re 10.6486%, WACC 8.5265%, Re* 19.16%, WACC* 16.0670%",
                   f"Re {pct(ex['re_usd'], 4)}, WACC {pct(ex['wacc_usd'], 4)}, "
                   f"Re* {pct(ex['re_fx'], 4)}, WACC* {pct(ex['wacc_fx'], 4)}", ok1])

    # 2. Equal yields, spread at automatic value -> equal results.
    eq = compute(**{**params, "rf_fx": params["rf_us"]}, spread_fx=None)
    ok2 = (abs(eq["re_usd"] - eq["re_fx"]) < tol and abs(eq["wacc_usd"] - eq["wacc_fx"]) < tol
           and abs(eq["rd_usd"] - eq["rd_fx"]) < tol)
    checks.append(["2. Equal yields give equal results", "USD = foreign for Re, Rd, WACC",
                   f"WACC {pct(eq['wacc_usd'], 6)} vs {pct(eq['wacc_fx'], 6)}", ok2])

    # 3. Zero debt -> WACC = cost of equity.
    z = compute(**{**params, "d": 0.0}, spread_fx=spread_fx)
    ok3 = abs(z["wacc_usd"] - z["re_usd"]) < tol and abs(z["wacc_fx"] - z["re_fx"]) < tol
    checks.append(["3. Zero debt: WACC = cost of equity", "Both currencies",
                   f"USD {pct(z['wacc_usd'], 6)} / {pct(z['re_usd'], 6)}; "
                   f"foreign {pct(z['wacc_fx'], 6)} / {pct(z['re_fx'], 6)}", ok3])

    # 4. Overriding the foreign spread leaves USD unchanged.
    a = compute(**params, spread_fx=None)
    b = compute(**params, spread_fx=0.0777)
    usd_keys = ["beta_e", "re_usd", "rd_usd", "rd_usd_at", "wacc_usd"]
    ok4 = all(abs(a[k] - b[k]) < tol for k in usd_keys) and abs(a["wacc_fx"] - b["wacc_fx"]) > 0 \
        if params["d"] > 0 else all(abs(a[k] - b[k]) < tol for k in usd_keys)
    checks.append(["4. Changing the foreign spread leaves USD unchanged", "USD values identical",
                   f"USD WACC {pct(a['wacc_usd'], 6)} vs {pct(b['wacc_usd'], 6)}", ok4])

    chk = pd.DataFrame(checks, columns=["Check", "Expected", "Result", "Pass"])
    chk["Pass"] = chk["Pass"].map({True: "PASS", False: "FAIL"})
    st.dataframe(chk, use_container_width=True, hide_index=True)

    st.subheader("Check one example by hand")
    st.markdown(
        """
Use a spreadsheet or calculator. Inputs: βU 0.80, tax 35%, ERP 6.5%, debt share 30%,
U.S. yield 4%, Colombia yield 12%, USD spread 1.5%.

1. D/E = 0.30 / 0.70 = 0.4286
2. βE = 0.80 × [1 + 0.65 × 0.4286] = 1.0229
3. Re = 4% + 1.0229 × 6.5% = 10.6486%
4. Rd = 4% + 1.5% = 5.5%. After tax: 5.5% × 0.65 = 3.575%
5. WACC = 0.70 × 10.6486% + 0.30 × 3.575% = 8.5265%
6. F = 1.12 / 1.04 = 1.076923
7. Re* = 1.106486 × 1.076923 − 1 = 19.16%
8. s* = 1.5% × 1.076923 = 1.6154%. Rd* = 12% + 1.6154% = 13.6154%. After tax: 8.85%
9. WACC* = 0.70 × 19.16% + 0.30 × 8.85% = 16.0670%
        """
    )
