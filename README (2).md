# International Cost of Capital App

This Streamlit app compares a project's cost of equity and WACC in USD and in the currency of a selected country.

## What the app does

- It fetches four data sets:
  1. Unlevered industry betas (Damodaran, `betas.xls`)
  2. Corporate tax rates (OECD)
  3. BBB (Baa2) USD borrowing spread for large non-financial service firms (Damodaran)
  4. 10-year government bond yields (Trading Economics)
- It shows the source, the data date, and the fetch time for each data set.
- The **Refresh data** button clears the cache and fetches the data again.
- If a fetch fails, the app uses fallback data from Case Packet 2026 and shows a warning. A failed fetch is not cached, so the next run tries again.
- Betas: the app reads `betas.xls` (needs `xlrd`). If that fails, it reads the Damodaran beta web page.
- Tax rates: the app reads the OECD report page. If that fails, it reads the OECD Data Explorer CSV.
- The sidebar has one **Selections** section with four groups:
  1. Industry: industry and unlevered beta (with override).
  2. Target country: country, project tax basis (U.S. or country), tax rates, and country yield (each with override).
  3. Risk information: equity risk premium, debt share, and U.S. 10-year yield (with override).
  4. Spread information: USD BBB spread and foreign-currency spread (each with override). When the foreign override is off, the app shows the automatic value.
- The app shows USD results (left) and foreign-currency results (right) side by side. Each side has a clear label. A CSV download is available.

## Default settings

Software (System & Application), United States, U.S. tax rate, 6.5% equity risk premium, 30% debt share.

## Formulas

- `D/E = d / (1 - d)`
- `βE = βU × [1 + (1 - τ) × D/E]`
- `Re = Rf + βE × ERP`
- `Rd = Rf + spread`
- `WACC = (1 - d) × Re + d × Rd × (1 - τ)`
- `1 + Re* = (1 + Re) × (1 + Rf*) / (1 + Rf)`
- `s* = s × (1 + Rf*) / (1 + Rf)`, unless you overwrite it

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

1. Create a GitHub repository.
2. Upload `app.py`, `requirements.txt`, and `README.md`.
3. Open Streamlit Community Cloud. Select **New app**.
4. Select the repository, the branch, and `app.py`. Select **Deploy**.

## Verification

The **Verification** tab runs four checks:

1. The Colombia example from the Case Packet gives the expected values.
2. Equal yields (spread override off) give equal USD and foreign results.
3. Zero debt makes WACC equal to the cost of equity.
4. A foreign-spread override leaves the USD results unchanged.

Also check one example by hand in a spreadsheet. The steps are in the Verification tab.

## Limits

- Web pages can change. A parser can fail. The fallback data then applies.
- Government yields include liquidity and term premia. They can include default risk.
- The app adds no country risk premium.
