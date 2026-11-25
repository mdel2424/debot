"""Rebuild the Ottawa sourcing data exports from the checked evidence files."""

import csv
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATE = "2026-11-25"
WEIGHTS = dict(landed_cost=25, measurements=20, selection=15, garment_potential=15,
               automation=10, inventory=5, shipping=5, reliability=5)
FX = {"CAD": 1, "USD": 1.4296, "JPY": 0.008980, "EUR": 1.5679, "GBP": 1.8744,
      "KRW": 0.001064, "TWD": 0.04335, "MYR": 0.3473, "HKD": 0.1823}
FX_URL = "https://www.bankofcanada.ca/rates/exchange/daily-exchange-rates/"
QTY = (1, 5, 10, 25, 50, 100)
YIELDS = (0.25, 0.50, 0.75, 0.90)
POST = "https://www.post.japanpost.jp/send/oversea/charge/list-parcel/zone3_en.html"
THRESHOLDS = "https://cbsa-asfc.gc.ca/services/cusma-aceum/lvs-efv-eng.html"
POST_FEE = "https://www.canadapost-postescanada.ca/cpc/en/support/kb/receiving/delivery-faq/how-to-pay-duty-and-taxes-online-canada-post.page"


def read_evidence(name):
    with (ROOT / name).open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="|"))
    assert all(None not in row and None not in row.values() for row in rows), name
    return [{key: value.strip() for key, value in row.items()} for row in rows]


def write_csv(name, rows):
    with (ROOT / name).open("w", encoding="utf-8", newline="") as f:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        writer = csv.DictWriter(f, columns)
        writer.writeheader()
        writer.writerows({k: "unknown" if v is None else v for k, v in row.items()} for row in rows)


def identifier(name):
    return "-".join("".join(ch if ch.isalnum() else " " for ch in name.lower()).split())


def suppliers():
    profiles = read_evidence("supplier_evidence.psv")
    candidates = read_evidence("directory_leads.psv")
    rows = []
    inferred_fields = ("inventory_size", "restock", "typical_price", "cheapest_observed",
                       "minimum_order", "account", "selection", "photos", "measurements",
                       "condition_material", "canada", "shipping_cost", "free_threshold",
                       "consolidation", "returns", "public_contact", "website_technology",
                       "official_buyer_api", "public_inventory_feed", "csv_export", "rss",
                       "sitemap", "json_ld", "listing_rendering", "stable_ids", "pagination",
                       "filters", "update_timestamps", "sold_indicator", "terms_robots_limits")
    for raw in profiles + candidates:
        details = raw.pop("details", "")
        raw.update(pair.split("=", 1) for pair in details.split("~") if pair)
        for key in inferred_fields:
            raw.setdefault(key, "")
        row = {k: v or "unknown" for k, v in raw.items() if k not in {"ratings", "opportunity"}}
        row = {"supplier_id": identifier(row["supplier"]), **row, "verification_date": DATE}
        if raw.get("ratings"):
            scores = dict(zip(WEIGHTS, map(int, raw["ratings"].split()), strict=True))
            assert all(0 <= rating <= 10 for rating in scores.values())
            row.update({f"rating_{k}_of_10": v for k, v in scores.items()})
            row["score_of_100"] = sum(WEIGHTS[k] * v for k, v in scores.items()) / 10
            row["opportunity_of_10"] = int(raw["opportunity"])
        else:
            row.update({f"rating_{k}_of_10": None for k in WEIGHTS})
            row.update(score_of_100=None, opportunity_of_10=None)
        rows.append(row)
    assert len({row["supplier_id"] for row in rows}) == len(rows), "Duplicate suppliers"
    return sorted(rows, key=lambda row: (row["score_of_100"] is None,
                                         -(row["score_of_100"] or 0), row["supplier"]))


def parcel_rate(weight, method):
    kg = math.ceil(weight)
    if not 1 <= kg <= 30:
        return None
    if method == "surface":
        return 2500 + 600 * (kg - 1) if kg <= 10 else 7900 + 400 * (kg - 10)
    return 3850 + 2150 * (kg - 1) if kg <= 10 else 23200 + 1600 * (kg - 10)


def cost_model():
    # Supplier/route assumptions are modeled, not checkout quotes.
    cases = [
        dict(key="ca_ottawa_bins", route="Lainey's Luck Ottawa in-store bins / pickup comparison",
             currency="CAD", price=4.99 * 0.22 / 0.45359237, ship=[0]*6, method="domestic", minimum=1,
             note="Published CAD 4.99/lb; estimated 220 g per tee. In-person bins, excludes travel and time; not an online unit quote.",
             source="https://laineysluck.ca/info/"),
        dict(key="ca_dollar_bin", route="Canada domestic dollar-bin scenario", currency="CAD",
             price=1, ship=[14, 14, 16, 22, 30, 48], method="domestic", minimum=1,
             note="Black Cat Ko. CAD 1 clearance examples; availability and shipping modeled."),
        dict(key="ca_individual", route="Canada domestic individual thrift basket", currency="CAD",
             price=8, ship=[22, 22, 0, 0, 0, 0], method="domestic", minimum=1,
             note="CAD 8 basket assumption; ItsADeal published Ontario fee below CAD 65.",
             source="https://itsadeal.ca/policies/shipping-policy"),
        dict(key="ca_peoples", route="People's Thrift Ontario combined", currency="CAD",
             price=10.40, ship=[9.99]*6, method="domestic", minimum=1,
             note="One observed sale-price anchor and Ontario flat rate; same price/stock not guaranteed.",
             source="https://www.peoplesthrift.ca/shipping-policy"),
        dict(key="ca_grader", route="Ontario sorter sample", currency="CAD", price=3,
             ship=[14, 16, 18, 24, 34, 55], method="domestic", minimum=25,
             note="Quote target only; no supplier agreement or MOQ verified."),
        dict(key="us_single_direct", route="US low-cost singles direct postal", currency="USD",
             price=1, ship=[16, 24, 35, 55, 90, 150], method="post", minimum=1,
             note="USD 1 is a Thrift2U listing anchor. Shipping assumptions replace unknown Canadian checkout.",
             source="https://thrift2u.com/pages/info"),
        dict(key="us_consolidated", route="US singles via qualifying courier consolidation", currency="USD",
             price=3, ship=[23, 32, 42, 62, 100, 155], handling=8, domestic=[8, 10, 14, 20, 32, 60],
             method="us_courier", minimum=1,
             note="All logistics assumptions CAD; confirm courier eligibility, brokerage, seller fees and consolidation count."),
        dict(key="raghouse_lot", route="Raghouse off-grade benchmark; variable lot size", currency="USD",
             price=68/71, ship=[30, 40, 50, 70, 110, 185], method="post", minimum=25,
             note="Observed 71-piece lot used as a unit-cost anchor, not divisible. Minimum 25 is screening assumption; exact lot below.",
             source="https://raghouse.com/collections/t-shirts/products/graphic-sports-t-shirts-unsorted-71-pcs"),
        dict(key="raghouse_courier", route="Raghouse off-grade benchmark; qualifying US courier",
             currency="USD", price=68/71, ship=[32, 44, 55, 78, 125, 200], handling=15,
             method="us_courier", minimum=25,
             note="Compare with postal; brokerage and checkout carrier not confirmed."),
        dict(key="jp_surface", route="Japan proxy individual tee / surface", currency="JPY", price=600,
             proxy=350, per_item_domestic=120, method="post", rate="surface", packing=500, minimum=1,
             note="JPY 600 purchase target; JPY 350/request Neokyo, packing starts at 500; 120 JPY/item domestic allocation assumes sharing/free sellers; proxy quote required.",
             source="https://neokyo.com/en/fees"),
        dict(key="jp_air", route="Japan proxy individual tee / air", currency="JPY", price=600,
             proxy=350, per_item_domestic=120, method="post", rate="air", packing=500, minimum=1,
             note="Same basket as surface; posted Zone 3 rates, not a proxy quote.",
             source="https://neokyo.com/en/fees"),
        dict(key="jp_same_seller", route="Japan multi-tee lots / surface; shared proxy fee", currency="JPY",
             price=450, proxy=350, lot_size=5, per_item_domestic=100, method="post", rate="surface",
             packing=500, minimum=5,
             note="Illustrative 5-tee auction request (not five stand-alone fees). More sizes/condition risk."),
        dict(key="uk_box", route="UK bundle consolidated", currency="GBP", price=4.5,
             ship=[27, 40, 55, 95, 150, 245], method="post", minimum=10,
             note="Pick Vintage GBP 45/10 observed listing; freight is CAD assumption, UK tax/VAT treatment check.",
             source="https://pickvintagewholesale.com/products/10-x-branded-t-shirts"),
        dict(key="eu_forward", route="EU forwarder for Sellpy-style singles", currency="EUR", price=4,
             ship=[28, 39, 53, 88, 145, 240], handling=12, domestic=[8]*6, method="post", minimum=1,
             note="EUR 4 catalogue anchor; local shipping, handling and freight illustrative; Canada checkout not verified."),
        dict(key="kr_proxy", route="Korea resale proxy", currency="KRW", price=7000,
             proxy=2000, per_item_domestic=800, ship=[28, 44, 59, 106, 185, 310],
             handling=15, method="courier_other", minimum=1,
             note="Screening only. Seller prices, local/proxy costs and broker fee are assumptions."),
        dict(key="sea_export", route="SE Asia export bale / small freight", currency="CAD", price=2,
             ship=[0, 0, 0, 0, 185, 280], handling=50, method="courier_other", minimum=50,
             note="Quote target only. Actual seller MOQ could be a container; no Canadian delivery confirmed."),
    ]
    # Extra source-observed lot size, with transport sensitivity instead of pretending a quote.
    for key, method, shipping, handling in (("raghouse_71_low", "us_courier", 95, 15),
                                             ("raghouse_71_high", "post", 180, None)):
        cases.append(dict(key=key, route="Raghouse observed 71-tee lot sensitivity", currency="USD",
                          price=68/71, ship=[shipping], handling=handling, method=method,
                          minimum=71, quantities=[71], weight=13.154,
                          note="USD 68 actual lot; catalogue variant weight; shipping CAD scenario not a quote.",
                          source="https://raghouse.com/collections/t-shirts/products/graphic-sports-t-shirts-unsorted-71-pcs"))
    rows = []
    for case in cases:
        method = case["method"]
        for i, n in enumerate(case.get("quantities", QTY)):
            fx = FX[case["currency"]]
            value = round(n * case["price"] * fx, 2)
            weight = case.get("weight", n * 0.22 + (0.3 if n <= 10 else 0.75 if n <= 50 else 1.5))
            domestic = case.get("domestic", [0]*6)[i] + n * case.get("per_item_domestic", 0) * fx
            proxy = math.ceil(n / case.get("lot_size", 1)) * case.get("proxy", 0) * fx
            packing = case.get("packing", 0)
            if packing:
                packing += max(0, math.ceil(weight) - 2) * 150
            packing *= fx
            shipping = (parcel_rate(weight, case["rate"]) * fx if "rate" in case else case["ship"][i])
            allowed = n >= case["minimum"]
            taxes_apply = method == "domestic" or value > (40 if method == "us_courier" else 20)
            duty_rate = 0.18 if method != "domestic" and value > (150 if method == "us_courier" else 20) else 0
            duty = round(duty_rate * value, 2)
            hst_base = value + duty + (shipping if method == "domestic" else 0)
            tax = round(hst_base * 0.13, 2) if taxes_apply else 0
            brokerage = (9.95 if taxes_apply else 0) if method == "post" else (case.get("handling") or 0)
            fx_fee = round((value + proxy + packing + shipping + domestic) * 0.025, 2) if fx != 1 else 0
            total = round(value + domestic + proxy + packing + shipping + duty + tax + brokerage + fx_fee, 2)
            for usable in YIELDS:
                rows.append(dict(
                    scenario=case["key"], route=case["route"], order_qty=n,
                    minimum_qty_used_in_model=case["minimum"], orderable_in_model=allowed,
                    usable_fraction=usable, expected_usable_qty=round(n * usable, 2),
                    currency=case["currency"], fx_cad_per_unit=fx, listed_unit_price=round(case["price"], 4),
                    merchandise_cad=value, parcel_weight_kg=round(weight, 2),
                    origin_shipping_cad=round(domestic, 2), proxy_cad=round(proxy, 2),
                    packing_consolidation_cad=round(packing, 2), destination_shipping_cad=round(shipping, 2) if allowed else None,
                    mfn_duty_rate=duty_rate, duty_cad=duty if allowed else None, sales_tax_cad=tax if allowed else None,
                    broker_handling_cad=brokerage if allowed else None, payment_fx_cad=fx_fee if allowed else None,
                    landed_cad=total if allowed else None,
                    landed_per_ordered_cad=round(total/n, 2) if allowed else None,
                    landed_per_usable_cad=round(total/(n*usable), 2) if allowed else None,
                    shipping_method=case.get("rate", method), quote_status="modeled",
                    use_case="Ottawa personal cash-outlay benchmark; see commercial caveats",
                    assumption=case["note"], supplier_or_proxy_source=case.get("source"),
                    rate_source=POST if "rate" in case else None, cbsa_threshold_source=THRESHOLDS,
                    postal_handling_source=POST_FEE if method == "post" else None,
                    fx_source=FX_URL if fx != 1 else None, verification_date=DATE,
                ))
    return rows


def listings(costs):
    output = []
    for row in read_evidence("listing_evidence.psv"):
        price = float(row["listed_price"]) if row["listed_price"] else None
        fx = FX.get(row["currency"])
        qty = int(row["units_in_listing"]) if row["units_in_listing"] else None
        scenario = row.pop("comparison_scenario")
        # Substitute the observed item/lot value into a row for the same order quantity.
        base = next((c for c in costs if c["scenario"] == scenario and c["order_qty"] == qty
                     and c["orderable_in_model"]), None)
        estimated = None
        if base and fx and price is not None:
            value = round(price * fx, 2)
            threshold = 150 if base["shipping_method"] == "us_courier" else 20
            overseas = base["shipping_method"] != "domestic"
            duty = value * 0.18 if overseas and value > threshold else 0
            taxable = not overseas or value > (40 if base["shipping_method"] == "us_courier" else 20)
            shipping = base["destination_shipping_cad"] + base["origin_shipping_cad"]
            tax = (value+duty+(0 if overseas else shipping))*0.13 if taxable else 0
            handling = base["broker_handling_cad"]
            if base["shipping_method"] in {"post", "surface", "air"}:
                handling = 9.95 if taxable else 0
            fees = base["proxy_cad"] + base["packing_consolidation_cad"]
            fx_fee = (value + shipping + fees)*0.025 if row["currency"] != "CAD" else 0
            estimated = round(value+duty+tax+shipping+handling+fees+fx_fee, 2)
        factor = 1/2.54 if row["measurement_unit"] == "cm" else 1
        for key in ("pit_to_pit", "body_length", "shoulder_width", "sleeve_length"):
            row[f"{key}_in"] = round(float(row[key])*factor, 3) if row[key] else None
            row[key] = float(row[key]) if row[key] else None
        cad = round(price*fx, 2) if fx and price is not None else None
        row.update(supplier_id=identifier(row["supplier"]), listed_price=price, units_in_listing=qty,
                   estimated_cad_price=cad,
                   estimated_cad_per_garment=round(cad/qty, 2) if cad is not None and qty else None,
                   estimated_landed_order_cad=estimated,
                   estimated_landed_per_garment_cad=round(estimated/qty, 2) if estimated is not None and qty else None,
                   landed_comparison_scenario=scenario or None,
                   verification_date=DATE)
        output.append({k: None if v == "" else v for k, v in row.items()})
    return output


def main():
    rows = suppliers()
    costs = cost_model()
    examples = listings(costs)
    assert {row["supplier_id"] for row in examples} <= {row["supplier_id"] for row in rows}
    assert len({row["product_url"] for row in examples}) == len(examples)
    write_csv("suppliers.csv", rows)
    (ROOT / "suppliers.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv("example_listings.csv", examples)
    write_csv("landed_costs.csv", costs)
    contacts = read_evidence("contact_evidence.psv")
    names = {row["supplier"] for row in rows}
    assert all(row["supplier"] in names for row in contacts)
    write_csv("supplier_contacts.csv", [{**row, "verification_date": DATE} for row in contacts])
    print(f"{len(rows)} supplier records: {dict(Counter(row['evidence_level'] for row in rows))}")
    print(f"{len(examples)} examples, {len(costs)} model rows and {len(contacts)} contact leads")


if __name__ == "__main__":
    main()
