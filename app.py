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
URL_OECD = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
URL_RATINGS = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
URL_BONDS = "https://tradingeconomics.com/bonds"
TE_PAGES = [URL_BONDS] + [
    f"{URL_BONDS}?continent={c}"
    for c in ("europe", "asia", "america", "africa", "australia")
]
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
FALLBACK_YIELDS = {
    "Brazil": 15.2, "Colombia": 12.8, "Mexico": 9.1, "India": 7.2,
    "Poland": 6.2, "United Kingdom": 5.2, "Australia": 5.0,
    "United States": 4.9, "France": 4.8, "Slovakia": 4.2, "Portugal": 3.7,
    "Canada": 3.7, "Germany": 3.3, "Japan": 2.6, "China": 1.6,
    "Switzerland": 0.4,
}
FALLBACK_TAX_TEXT = """
France 36.1, Argentina 35.0, Germany 30.1, Australia 30.0, Kenya 30.0,
Mexico 30.0, Nigeria 30.0, Senegal 30.0, Zambia 30.0, Japan 29.7,
Portugal 29.5, Peru 29.5, Pakistan 29.0, Congo 28.0, New Zealand 28.0,
Italy 27.8, South Korea 27.5, Chile 27.0, Dominican Republic 27.0,
South Africa 27.0, Greenland 26.5, Canada 26.0, Netherlands 25.8,
United States 25.5, India 25.2, Angola 25.0, Belgium 25.0, China 25.0,
Spain 25.0, United Kingdom 25.0, Jamaica 25.0, Monaco 25.0, Panama 25.0,
Philippines 25.0, Uruguay 25.0, Turkey 25.0, Malaysia 24.0, Slovakia 24.0,
Luxembourg 23.9, Austria 23.0, Israel 23.0, Egypt 22.5, Botswana 22.0,
Denmark 22.0, Greece 22.0, Indonesia 22.0, Norway 22.0, Slovenia 22.0,
Estonia 22.0, Czech Republic 21.0, Sweden 20.6, Cabo Verde 20.4,
Iceland 20.0, Azerbaijan 20.0, Finland 20.0, Jordan 20.0, Kazakhstan 20.0,
Latvia 20.0, Saudi Arabia 20.0, Thailand 20.0, Vietnam 20.0, Tunisia 20.0,
Switzerland 19.5, Poland 19.0, Brunei 18.5, Armenia 18.0, Croatia 18.0,
Ukraine 18.0, Singapore 17.0, Lithuania 17.0, Hong Kong 16.5, Romania 16.0,
Albania 15.0, Georgia 15.0, Kuwait 15.0, Ireland 12.5, Macau 12.0,
Bulgaria 10.0, Paraguay 10.0, Qatar 10.0, United Arab Emirates 9.0,
Hungary 9.0
"""

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
# Data fetch (cached; the refresh button clears the cache)
# ----------------------------------------------------------------------
@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_betas():
    try:
        r = requests.get(URL_BETAS, headers=UA, timeout=30)
        r.raise_for_status()
        raw = pd.read_excel(
            io.BytesIO(r.content), sheet_name="Industry Averages",
            header=None, engine="xlrd",
        )
        mask = raw.apply(
            lambda row: row.astype(str).str.strip().eq("Industry Name").any(), axis=1
        )
        hdr = raw.index[mask][0]
        df = raw.iloc[hdr + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[hdr]]
        df = df.dropna(subset=["Industry Name"])
        df["Industry Name"] = df["Industry Name"].astype(str).str.strip()
        df["Unlevered beta"] = pd.to_numeric(df["Unlevered beta"], errors="coerce")
        df = df.dropna(subset=["Unlevered beta"])
        keep = [c for c in ("Industry Name", "Number of firms", "Beta",
                            "D/E Ratio", "Effective Tax rate", "Unlevered beta")
                if c in df.columns]
        df = df[keep].reset_index(drop=True)
        asof = ""
        for v in raw.iloc[:hdr].astype(str).values.ravel():
            if re.search(r"(date|as of|updated)", v, re.I) and v != "nan":
                asof = v.strip()
                break
        return {"ok": True, "df": df, "asof": asof or "see file header",
                "fetched": now_utc(), "error": None}
    except Exception as exc:  # network or parse failure
        df = pd.DataFrame(
            {"Industry Name": list(FALLBACK_BETAS),
             "Unlevered beta": list(FALLBACK_BETAS.values())}
        )
        return {"ok": False, "df": df, "asof": "Case Packet 2026 (fallback)",
                "fetched": now_utc(), "error": str(exc)[:200]}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_tax():
    try:
        r = requests.get(URL_OECD, headers=UA, timeout=30)
        r.raise_for_status()
        tables = pd.read_html(io.StringIO(r.text))
        best = None
        for t in tables:
            years = {}
            for c in t.columns:
                m = re.search(r"(20\d\d)", str(c))
                if m:
                    years[c] = int(m.group(1))
            if years and len(t) >= 20:
                col = max(years, key=years.get)
                if best is None or years[col] > best[1]:
                    best = (t, years[col], col)
        if best is None:
            raise ValueError("No tax table found on the OECD page.")
        t, year, col = best
        out = {}
        for _, row in t.iterrows():
            val = to_float(row[col])
            if val is not None:
                out[norm(row.iloc[0])] = val
        if len(out) < 20:
            raise ValueError("OECD table parse gave too few rows.")
        return {"ok": True, "tax": out, "asof": f"Statutory rate, {year}",
                "fetched": now_utc(), "error": None}
    except Exception as exc:
        out = {}
        for item in FALLBACK_TAX_TEXT.replace("\n", " ").split(","):
            item = item.strip()
            if item:
                name, val = item.rsplit(" ", 1)
                out[norm(name)] = float(val)
        return {"ok": False, "tax": out, "asof": "Case Packet 2026, OECD 2026 (fallback)",
                "fetched": now_utc(), "error": str(exc)[:200]}


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def fetch_spread():
    try:
        r = requests.get(URL_RATINGS, headers=UA, timeout=30)
        r.raise_for_status()
        for t in pd.read_html(io.StringIO(r.text)):
            for _, row in t.iterrows():
                cells = [str(c) for c in row.values]
                for i, c in enumerate(cells):
                    if "Baa2/BBB" in c and i + 1 < len(cells):
                        val = to_float(cells[i + 1])
                        if val is not None:
                            return {"ok": True, "spread": val,
                                    "asof": "Large non-financial service firms, Baa2/BBB",
                                    "fetched": now_utc(), "error": None}
        raise ValueError("Baa2/BBB row not found.")
    except Exception as exc:
        return {"ok": False, "spread": FALLBACK_SPREAD,
                "asof": "Case Packet 2026 example (fallback)",
                "fetched": now_utc(), "error": str(exc)[:200]}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_yields():
    out, dates = {}, []
    try:
        for url in TE_PAGES:
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
                tables = pd.read_html(io.StringIO(r.text))
            except Exception:
                continue
            for t in tables:
                cols = [str(c).strip() for c in t.columns]
                if "Yield" not in cols:
                    continue
                ycol = t.columns[cols.index("Yield")]
                name_col = None
                for c in t.columns:
                    if c == ycol:
                        break
                    s = t[c].astype(str)
                    if s.str.contains(r"[A-Za-z]{3}").mean() > 0.8:
                        name_col = c
                        break
                if name_col is None:
                    continue
                dcol = t.columns[cols.index("Date")] if "Date" in cols else None
                for _, row in t.iterrows():
                    name = str(row[name_col]).strip()
                    y = to_float(row[ycol])
                    if y is not None and name and name != "nan":
                        out.setdefault(name, y)
                        if dcol is not None:
                            dates.append(str(row[dcol]))
        if len(out) < 5:
            raise ValueError("Trading Economics parse gave too few countries.")
        return {"ok": True, "yields": out,
                "asof": "Latest quotes on page" + (f" (e.g. {dates[0]})" if dates else ""),
                "fetched": now_utc(), "error": None}
    except Exception as exc:
        return {"ok": False, "yields": dict(FALLBACK_YIELDS),
                "asof": "Case Packet 2026, 6 Nov 2026 (fallback)",
                "fetched": now_utc(), "error": str(exc)[:200]}


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
sb.caption("Data fetch time: " + yields["fetched"])
for label, src in (("Betas", betas), ("Tax rates", taxes),
                   ("BBB spread", spread), ("Bond yields", yields)):
    if not src["ok"]:
        sb.warning(f"{label}: live fetch failed. Fallback data is in use.")

sb.header("Selections")
industries = sorted(betas["df"]["Industry Name"].tolist())
ind_index = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
industry = sb.selectbox("Industry", industries, index=ind_index)

yield_map = {norm(k): v for k, v in yields["yields"].items()}
display_names = {norm(k): k for k in yields["yields"]}
display_names["united states"] = "United States"
countries = sorted(display_names.values())
c_index = countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0
country = sb.selectbox("Target country", countries, index=c_index)
is_us = norm(country) == "united states"

tax_basis = sb.radio("Project tax rate", ["United States", "Selected country"],
                     horizontal=True)
erp_pct = sb.number_input("Equity risk premium (%)", 0.0, 30.0, DEFAULT_ERP, 0.1)
debt_pct = sb.slider("Target debt share (%)", 0, 90, DEFAULT_DEBT, 1)

# ---- Auto values -----------------------------------------------------
auto_beta = float(betas["df"].loc[betas["df"]["Industry Name"] == industry,
                                  "Unlevered beta"].iloc[0])
auto_tax_us = taxes["tax"].get("united states")
auto_tax_fx = taxes["tax"].get(norm(country))
auto_spread_us = spread["spread"]
auto_yld_us = yield_map.get("united states")
auto_yld_fx = yield_map.get(norm(country))

# ---- Manual overrides ------------------------------------------------
sb.header("Manual overrides")
sb.caption("Switch on a box to replace the fetched value.")


def override(label, auto, key, lo, hi, step, fmt="%.4f", default_if_missing=0.0):
    """Number input with an on/off switch. Returns the value in use."""
    missing = auto is None
    on = sb.checkbox(f"Override {label}", value=missing, key=f"on_{key}",
                     disabled=missing)
    base = default_if_missing if missing else auto
    if on or missing:
        return sb.number_input(label, lo, hi, float(base), step, format=fmt,
                               key=f"in_{key}")
    sb.caption(f"{label}: {base:{fmt[1:]}} (fetched)")
    return float(base)


beta_u = override("unlevered beta", auto_beta, f"beta_{industry}", 0.0, 5.0, 0.01, "%.4f")
tax_us_pct = override("U.S. tax rate (%)", auto_tax_us, "tax_us", 0.0, 60.0, 0.1, "%.2f", 25.0)
if is_us:
    tax_fx_pct = tax_us_pct
else:
    tax_fx_pct = override(f"{country} tax rate (%)", auto_tax_fx, f"tax_{country}",
                          0.0, 60.0, 0.1, "%.2f", 25.0)
spread_us_pct = override("USD BBB spread (%)", auto_spread_us, "spread_us", 0.0, 20.0, 0.01, "%.4f", FALLBACK_SPREAD)
yld_us_pct = override("U.S. 10-year yield (%)", auto_yld_us, "yld_us", -2.0, 50.0, 0.01, "%.2f", 4.0)
if is_us:
    yld_fx_pct = yld_us_pct
else:
    yld_fx_pct = override(f"{country} 10-year yield (%)", auto_yld_fx, f"yld_{country}",
                          -2.0, 100.0, 0.01, "%.2f", 5.0)

tax_used_pct = tax_us_pct if tax_basis == "United States" else tax_fx_pct

# Foreign-currency spread: automatic unless the user overrides it.
f_auto = (1 + yld_fx_pct / 100) / (1 + yld_us_pct / 100)
auto_spread_fx_pct = spread_us_pct * f_auto
fx_spread_on = sb.checkbox("Override foreign-currency spread", value=False)
if fx_spread_on:
    spread_fx_pct = sb.number_input(
        "Foreign-currency spread (%)", 0.0, 50.0, round(auto_spread_fx_pct, 4), 0.01,
        format="%.4f", key=f"in_spreadfx_{round(auto_spread_fx_pct, 4)}")
else:
    sb.number_input("Foreign-currency spread (%)", value=round(auto_spread_fx_pct, 4),
                    format="%.4f", disabled=True,
                    key=f"dis_spreadfx_{round(auto_spread_fx_pct, 4)}")
    spread_fx_pct = None

# ---- Compute ---------------------------------------------------------
params = dict(
    beta_u=beta_u, tax=tax_used_pct / 100, erp=erp_pct / 100,
    d=debt_pct / 100, rf_us=yld_us_pct / 100, rf_fx=yld_fx_pct / 100,
    spread_us=spread_us_pct / 100,
)
spread_fx = None if spread_fx_pct is None else spread_fx_pct / 100
res = compute(**params, spread_fx=spread_fx)

tab_res, tab_all, tab_assume, tab_ver = st.tabs(
    ["Results", "All industries", "Assumptions and sources", "Verification"]
)

# ---- Results tab -----------------------------------------------------
with tab_res:
    st.subheader(f"{industry}: {country}")
    st.write(f"Project tax rate: **{tax_used_pct:.2f}%** ({tax_basis}). "
             f"Debt share: **{debt_pct}%**. Equity risk premium: **{erp_pct:.2f}%**.")
    if is_us:
        st.info("The selected country is the United States. Both columns show the same result.")

    c1, c2 = st.columns(2)
    c1.metric("WACC (USD)", pct(res["wacc_usd"]))
    c2.metric(f"WACC ({country} currency)", pct(res["wacc_fx"]))
    c1.metric("Cost of equity (USD)", pct(res["re_usd"]))
    c2.metric(f"Cost of equity ({country} currency)", pct(res["re_fx"]))

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
    table = pd.DataFrame(rows, columns=["Item", "USD", f"{country} currency"]).set_index("Item")
    st.table(table)
    st.caption(f"Conversion factor F = (1 + {country} yield) / (1 + U.S. yield) = {res['factor']:.6f}")

    # Download
    out = table.reset_index()
    inputs = pd.DataFrame({
        "Input": ["Industry", "Country", "Tax basis", "Fetch time", "Foreign spread override"],
        "Value": [industry, country, tax_basis, yields["fetched"],
                  "on" if fx_spread_on else "off"],
    })
    csv = out.to_csv(index=False) + "\n" + inputs.to_csv(index=False)
    st.download_button("Download results (CSV)", csv, "cost_of_capital_results.csv", "text/csv")

# ---- All industries tab ---------------------------------------------
with tab_all:
    st.subheader("All industries, current settings")
    st.caption("Each row uses the fetched unlevered beta for that industry. "
               "The beta override applies only to the selected industry.")
    recs = []
    for _, row in betas["df"].iterrows():
        name = row["Industry Name"]
        bu = beta_u if name == industry else float(row["Unlevered beta"])
        r = compute(**{**params, "beta_u": bu}, spread_fx=spread_fx)
        recs.append({
            "Industry": name, "Unlevered beta": bu, "Levered beta": r["beta_e"],
            "Cost of equity USD (%)": r["re_usd"] * 100, "WACC USD (%)": r["wacc_usd"] * 100,
            "Cost of equity foreign (%)": r["re_fx"] * 100, "WACC foreign (%)": r["wacc_fx"] * 100,
        })
    alldf = pd.DataFrame(recs).sort_values("WACC USD (%)", ascending=False).reset_index(drop=True)
    st.dataframe(alldf.round(4), use_container_width=True, hide_index=True)
    st.bar_chart(alldf.set_index("Industry")[["WACC USD (%)", "WACC foreign (%)"]])
    st.download_button("Download all industries (CSV)", alldf.round(6).to_csv(index=False),
                       "cost_of_capital_all_industries.csv", "text/csv")

# ---- Assumptions and sources ----------------------------------------
with tab_assume:
    st.subheader("Sources and dates")
    src_tbl = pd.DataFrame([
        ["Unlevered industry betas", "Damodaran (NYU Stern)", URL_BETAS,
         betas["asof"], betas["fetched"], "Live" if betas["ok"] else "Fallback"],
        ["Corporate tax rates", "OECD", URL_OECD,
         taxes["asof"], taxes["fetched"], "Live" if taxes["ok"] else "Fallback"],
        ["BBB (Baa2) USD spread", "Damodaran (NYU Stern)", URL_RATINGS,
         spread["asof"], spread["fetched"], "Live" if spread["ok"] else "Fallback"],
        ["10-year government yields", "Trading Economics", URL_BONDS,
         yields["asof"], yields["fetched"], "Live" if yields["ok"] else "Fallback"],
    ], columns=["Data", "Source", "URL", "Data date", "Fetched", "Status"])
    st.dataframe(src_tbl, use_container_width=True, hide_index=True)
    for label, src in (("Betas", betas), ("Tax", taxes), ("Spread", spread), ("Yields", yields)):
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
- **Foreign spread.** By default `s* = s × (1 + Rf*) / (1 + Rf)`. You can override it. The override does not change USD results.
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

    # 2. Equal yields, spread override off -> equal results.
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
    checks.append(["4. Foreign spread override leaves USD unchanged", "USD values identical",
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
