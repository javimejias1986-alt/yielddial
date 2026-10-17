#!/usr/bin/env python3
"""Fetch cash-yield sources and normalise them into data/rates.json.

Sources
  treasury  US Treasury daily par yield curve CSV (live)
  aave      Aave v3 USDC supply APY, official GraphQL API (live)
  compound  Compound v3 (Comet) USDC supply rate, read on-chain via public RPC (live)
  manual    data/manual.json (hand-maintained: money-market funds, exchange rewards)

Usage:  python3 scripts/fetch_rates.py [--only treasury|defi|manual|all]

Rules: stdlib only; never invents numbers. A source or row that fails validation is
reported on stderr and the script exits non-zero. Rows that could not be refreshed keep
their previous values and old timestamp (so the site's stale check fires); rows that never
existed are simply absent. Good rows are still written.
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "rates.json"
MANUAL = ROOT / "data" / "manual.json"

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
TREASURY_PAGE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve"
AAVE_URL = "https://api.v3.aave.com/graphql"
SECONDS_PER_YEAR = 31_536_000

# (column in CSV, label, row id, shown in table?)
TREASURY_COLS = [
    ("1 Mo", "1M", True), ("2 Mo", "2M", False), ("3 Mo", "3M", True),
    ("4 Mo", "4M", False), ("6 Mo", "6M", True), ("1 Yr", "1Y", True),
    ("2 Yr", "2Y", False), ("5 Yr", "5Y", False), ("10 Yr", "10Y", False),
    ("30 Yr", "30Y", False),
]
TREASURY_NAMES = {"1M": "1-month", "3M": "3-month", "6M": "6-month", "1Y": "1-year"}
CURVE_LABELS = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "30Y"]

# Native USDC per chain (Circle-issued), Aave v3 core market name, Compound v3 USDC Comet
USDC = {
    "Ethereum": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "Base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "Arbitrum": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
    "Optimism": "0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85",
}
AAVE_CHAINS = [  # name, chainId, market name
    ("Ethereum", 1, "AaveV3Ethereum"), ("Base", 8453, "AaveV3Base"),
    ("Arbitrum", 42161, "AaveV3Arbitrum"), ("Optimism", 10, "AaveV3Optimism"),
]
COMPOUND = {  # chain -> (comet address, [rpc urls tried in order])
    "Ethereum": ("0xc3d688B66703497DAA19211EEdff47f25384cdc3",
                 ["https://ethereum-rpc.publicnode.com", "https://ethereum.publicnode.com"]),
    "Base": ("0xb125E6687d4313864e53df431d5425969c15Eb2F",
             ["https://mainnet.base.org", "https://base-rpc.publicnode.com"]),
    "Arbitrum": ("0x9c4ec768c28520B50860ea7a15bd7213a9fF58bf",
                 ["https://arb1.arbitrum.io/rpc", "https://arbitrum-one-rpc.publicnode.com"]),
}
SEL_BASE_TOKEN, SEL_UTILIZATION, SEL_SUPPLY_RATE = "0xc55dae63", "0x7eb71131", "0xd955759d"

errors = []


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def http(url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "yielddial-fetch/1.0", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def spread_pp(apy, base):
    return round(apy - base, 3) if base is not None else None


# ---------------------------------------------------------------- Treasury
def fetch_treasury():
    year = datetime.now(timezone.utc).year
    text = http(TREASURY_URL.format(year=year)).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("Treasury CSV empty")
    parsed = []
    for r in rows:
        d = datetime.strptime(r["Date"], "%m/%d/%Y").date()
        parsed.append((d, r))
    d, latest = max(parsed, key=lambda x: x[0])
    if date.today() - d > timedelta(days=6):
        raise ValueError(f"Treasury latest date {d} is more than 6 days old")
    fetched = now_iso()
    pts, rows_out = {}, []
    for col, label, _ in TREASURY_COLS:
        raw = (latest.get(col) or "").strip()
        if not raw:
            if label in ("1M", "3M", "6M", "1Y"):
                raise ValueError(f"Treasury {col} missing for {d}")
            continue
        v = float(raw)
        if not 0 < v < 20:
            raise ValueError(f"Treasury {col} implausible: {v}")
        pts[label] = v
    for label in ("1M", "3M", "6M", "1Y"):
        rows_out.append({
            "id": f"tsy-{label.lower()}",
            "name": f"US Treasury {TREASURY_NAMES[label]}",
            "type": "Treasury",
            "apy": pts[label],
            "yield_basis": "Par yield (bond-equivalent) from Treasury daily curve",
            "as_of": d.isoformat(),
            "last_checked": fetched,
            "source_kind": "live",
            "source_url": TREASURY_PAGE,
            "risk_tags": ["government-backed"] + (["variable rate"] if label == "1M" else []),
            "risk_note": ("Backed by the full faith and credit of the US government. Rate is locked if held to maturity; "
                          "selling early can gain or lose. Interest is exempt from state income tax."),
            "affiliate_url": "#",
        })
    curve = [{"label": l, "yield": pts[l]} for l in CURVE_LABELS if l in pts]
    return rows_out, {"as_of": d.isoformat(), "points": curve}, pts["3M"]


# ---------------------------------------------------------------- Aave
def fetch_aave():
    q = ("{ markets(request:{chainIds:[%s]}) { name chain{chainId name} reserves { "
         "underlyingToken{symbol address} supplyInfo{apy{value} total{value}} isFrozen isPaused } } }"
         % ",".join(str(c[1]) for c in AAVE_CHAINS))
    body = json.dumps({"query": q}).encode()
    resp = json.loads(http(AAVE_URL, body, {"content-type": "application/json"}))
    if resp.get("errors") or not resp.get("data"):
        raise ValueError(f"Aave GraphQL error: {resp.get('errors')}")
    markets = {m["name"]: m for m in resp["data"]["markets"]}
    out, fetched = [], now_iso()
    for chain, cid, mname in AAVE_CHAINS:
        try:
            m = markets.get(mname)
            if not m:
                raise ValueError(f"market {mname} not returned")
            res = [r for r in m["reserves"] if r["underlyingToken"]["address"].lower() == USDC[chain].lower()]
            if len(res) != 1:
                raise ValueError(f"expected 1 native USDC reserve, found {len(res)}")
            r = res[0]
            if r["isFrozen"] or r["isPaused"]:
                raise ValueError("USDC reserve is frozen or paused")
            frac = float(r["supplyInfo"]["apy"]["value"])
            if not 0 <= frac < 0.5:
                raise ValueError(f"implausible APY fraction {frac}")
            apy = round(frac * 100, 2)
            out.append({
                "id": f"aave-usdc-{chain.lower()}",
                "name": f"Aave v3 USDC ({chain})",
                "type": "DeFi lending",
                "apy": apy,
                "yield_basis": "Variable supply APY, base rate (excludes incentive rewards)",
                "as_of": fetched[:10],
                "last_checked": fetched,
                "source_kind": "live",
                "source_url": "https://app.aave.com/markets/",
                "risk_tags": ["smart-contract risk", "variable rate"],
                "risk_note": ("Lending to onchain borrowers via audited smart contracts; no deposit insurance. "
                              "Risks: contract bugs, oracle or liquidation problems, USDC depeg. Rate moves with utilisation."),
                "affiliate_url": "#",
            })
        except Exception as e:  # per-row failure
            errors.append(f"aave {chain}: {e}")
    return out


# ---------------------------------------------------------------- Compound
def eth_call(rpcs, to, data):
    last = None
    for rpc in rpcs:
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                               "params": [{"to": to, "data": data}, "latest"]}).encode()
            r = json.loads(http(rpc, body, {"content-type": "application/json"}, timeout=20))
            if "result" in r and r["result"] not in (None, "0x"):
                return int(r["result"], 16)
            last = f"{rpc}: {r.get('error') or 'empty result'}"
        except Exception as e:
            last = f"{rpc}: {e}"
    raise RuntimeError(f"all RPCs failed ({last})")


def fetch_compound():
    out, fetched = [], now_iso()
    for chain, (comet, rpcs) in COMPOUND.items():
        try:
            base = eth_call(rpcs, comet, SEL_BASE_TOKEN)
            if base != int(USDC[chain], 16):
                raise ValueError("Comet base token is not native USDC")
            util = eth_call(rpcs, comet, SEL_UTILIZATION)
            if not 0 <= util <= 10**18:
                raise ValueError(f"utilization out of range: {util}")
            rate = eth_call(rpcs, comet, SEL_SUPPLY_RATE + format(util, "064x"))  # per-second, 1e18 scale
            apr = rate / 1e18 * SECONDS_PER_YEAR
            if not 0 <= apr < 0.5:
                raise ValueError(f"implausible APR {apr}")
            apy = round(((1 + apr / 365) ** 365 - 1) * 100, 2)
            out.append({
                "id": f"compound-usdc-{chain.lower()}",
                "name": f"Compound v3 USDC ({chain})",
                "type": "DeFi lending",
                "apy": apy,
                "yield_basis": f"On-chain supply rate (APR {apr*100:.2f}%) shown as APY, daily compounding; excludes COMP rewards",
                "as_of": fetched[:10],
                "last_checked": fetched,
                "source_kind": "live",
                "source_url": f"https://app.compound.finance/markets/usdc-{ {'Ethereum':'mainnet','Base':'basemainnet','Arbitrum':'arb'}[chain] }",
                "risk_tags": ["smart-contract risk", "variable rate"],
                "risk_note": ("Lending to onchain borrowers via Compound v3 smart contracts; no deposit insurance. "
                              "Risks: contract bugs, oracle or liquidation problems, USDC depeg. Rate moves with utilisation."),
                "affiliate_url": "#",
            })
        except Exception as e:
            errors.append(f"compound {chain}: {e}")
    return out


# ---------------------------------------------------------------- Manual
def load_manual():
    data = json.loads(MANUAL.read_text())
    out = []
    for r in data["rows"]:
        for k in ("id", "name", "type", "yield_pct", "as_of", "checked_on", "source_url", "risk_tags", "risk_note"):
            if k not in r:
                raise ValueError(f"manual row {r.get('id')} missing '{k}'")
        if not isinstance(r["yield_pct"], (int, float)) or not 0 <= r["yield_pct"] < 25:
            raise ValueError(f"manual row {r['id']} yield implausible")
        date.fromisoformat(r["as_of"]); date.fromisoformat(r["checked_on"])
        out.append({
            "id": r["id"], "name": r["name"], "type": r["type"], "apy": float(r["yield_pct"]),
            "yield_basis": r.get("yield_basis", ""), "as_of": r["as_of"],
            "last_checked": r["checked_on"], "source_kind": "manual",
            "source_url": r["source_url"], "source_note": r.get("source_note", ""),
            "risk_tags": r["risk_tags"], "risk_note": r["risk_note"],
            "affiliate_url": r.get("affiliate_url", "#"),
        })
    return out


# ---------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["treasury", "defi", "manual", "all"], default="all")
    args = ap.parse_args()
    prev = json.loads(OUT.read_text()) if OUT.exists() else {"rows": [], "curve": None, "sources": {}}
    rows = {r["id"]: r for r in prev.get("rows", [])}
    sources, curve = prev.get("sources", {}), prev.get("curve")
    want = lambda s: args.only in ("all", s)

    if want("treasury"):
        try:
            trows, curve, _ = fetch_treasury()
            for r in trows:
                rows[r["id"]] = r
            sources["treasury"] = {"fetched_at": now_iso(), "as_of": curve["as_of"]}
        except Exception as e:
            errors.append(f"treasury: {e}")
    if want("defi"):
        for fn, key in ((fetch_aave, "aave"), (fetch_compound, "compound")):
            try:
                new = fn()
                for r in new:
                    rows[r["id"]] = r
                if new:
                    sources[key] = {"fetched_at": now_iso()}
            except Exception as e:
                errors.append(f"{key}: {e}")
    if True:  # manual file is cheap and local: always re-read so edits flow into rates.json
        try:
            for r in load_manual():
                rows[r["id"]] = r
            sources["manual"] = {"fetched_at": now_iso()}
        except Exception as e:
            errors.append(f"manual: {e}")

    # recompute spreads against the latest 3-month T-bill
    t3 = rows.get("tsy-3m", {}).get("apy")
    for r in rows.values():
        r["spread_vs_3m"] = spread_pp(r["apy"], t3)

    out = {
        "generated_at": now_iso(),
        "stale_after_hours": 36,
        "tbill_3m": t3,
        "sources": sources,
        "curve": curve,
        "rows": sorted(rows.values(), key=lambda r: -r["apy"]),
        "errors": errors,
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} rows, 3M T-bill = {t3}")
    for r in out["rows"]:
        print(f"  {r['apy']:6.2f}%  {r['source_kind']:6}  {r['name']}")
    if errors:
        print("\nERRORS:", *errors, sep="\n  ", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
