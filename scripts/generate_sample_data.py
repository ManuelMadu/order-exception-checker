"""Generate the deterministic synthetic datasets in ``data/``.

Every record is written out explicitly and dated relative to
``config.REFERENCE_NOW``, so re-running this script reproduces byte-identical
CSVs and therefore an identical exception report.

All names, addresses, SKUs and tracking numbers are invented.
"""

from __future__ import annotations

import csv
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402

NOW = config.REFERENCE_NOW
TS = "%Y-%m-%d %H:%M:%S"
DATE = "%Y-%m-%d"


def ts(hours_ago: float | None) -> str:
    """Timestamp ``hours_ago`` hours before the fixed reference time."""
    if hours_ago is None:
        return ""
    return (NOW - timedelta(hours=hours_ago)).strftime(TS)


def date(hours_ago: float | None) -> str:
    if hours_ago is None:
        return ""
    return (NOW - timedelta(hours=hours_ago)).strftime(DATE)


# order_id, marketplace, placed_h, customer, sku, qty, marketplace_status,
# warehouse = (status, picked_h, packed_h, dispatched_h, location) or None,
# carrier   = (tracking_number, carrier, tracking_status, last_update_h, eta_h) or None
ORDERS = [
    # ---- Amazon ----------------------------------------------------------
    ("A1001", "Amazon", 120, "Priya Nayar", "SKU-KTC-0012", 1, "Delivered",
     ("Dispatched", 114, 110, 106, "LDN-1"),
     ("RM418820561GB", "Royal Mail", "Delivered", 20, -0)),
    ("A1002", "Amazon", 96, "Tomasz Wierzbicki", "SKU-HME-0207", 2, "Shipped",
     ("Dispatched", 90, 86, 82, "LDN-1"),
     ("DPD9930114772", "DPD", "In Transit", 6, -24)),
    ("A1003", "Amazon", 6, "Ayesha Rahman", "SKU-KTC-0045", 1, "Processing",
     ("Received", None, None, None, "MAN-2"), None),
    ("A1004", "Amazon", 20, "Callum Fraser", "SKU-GRD-0113", 3, "Processing",
     ("Picking", None, None, None, "MAN-2"), None),
    ("A1005", "Amazon", 30, "Ingrid Sollberg", "SKU-HME-0091", 1, "Processing",
     ("Packed", 10, 6, None, "LDN-1"), None),
    ("A1006", "Amazon", 44, "Dele Ogunniyi", "SKU-PET-0033", 1, "Shipped",
     ("Dispatched", 40, 38, 36, "BHM-3"),
     ("EV7714203399", "Evri", "Collected", 30, -20)),
    # Demo case: dispatched and moving, but the carrier has gone quiet.
    ("A1007", "Amazon", 90, "Marta Kalnina", "SKU-KTC-0012", 2, "Shipped",
     ("Dispatched", 84, 80, 76, "LDN-1"),
     ("DPD9930118841", "DPD", "In Transit", 61, -12)),
    ("A1008", "Amazon", 72, "Owen Pritchard", "SKU-STY-0150", 1, "Delivered",
     ("Dispatched", 66, 62, 58, "MAN-2"),
     ("RM418822190GB", "Royal Mail", "Delivered", 24, 20)),
    ("A1009", "Amazon", 12, "Sofia Lindqvist", "SKU-GRD-0113", 1, "Processing",
     ("Picking", None, None, None, "LDN-1"), None),
    ("A1010", "Amazon", 26, "Hassan Farouk", "SKU-HME-0207", 1, "Cancelled",
     ("Cancelled", None, None, None, "MAN-2"), None),
    # Exception: marketplace order the warehouse never received.
    ("A1011", "Amazon", 40, "Rebecca Aldridge", "SKU-PET-0077", 1, "Processing",
     None, None),
    # Exception: dispatched with no carrier record at all.
    ("A1012", "Amazon", 60, "Joon-ho Bae", "SKU-STY-0150", 1, "Shipped",
     ("Dispatched", 56, 54, 52, "BHM-3"), None),
    # Exception: never left "Received".
    ("A1013", "Amazon", 34, "Chiara Bellini", "SKU-KTC-0045", 2, "Processing",
     ("Received", None, None, None, "LDN-1"), None),
    # Exception: packed but not collected.
    ("A1014", "Amazon", 40, "Ade Bankole", "SKU-HME-0091", 1, "Processing",
     ("Packed", 30, 26, None, "MAN-2"), None),
    ("A1015", "Amazon", 2, "Niamh Gallagher", "SKU-GRD-0204", 1, "Pending",
     ("Received", None, None, None, "LDN-1"), None),
    # Exception: warehouse cancelled it, the buyer has not been told.
    ("A1016", "Amazon", 60, "Ravi Chandrasekhar", "SKU-PET-0033", 1, "Processing",
     ("Cancelled", None, None, None, "BHM-3"), None),

    # ---- eBay ------------------------------------------------------------
    ("E2001", "eBay", 140, "Victor Almeida", "SKU-STY-0150", 1, "Delivered",
     ("Dispatched", 134, 130, 126, "LDN-1"),
     ("RM418819004GB", "Royal Mail", "Delivered", 80, 76)),
    ("E2002", "eBay", 60, "Leila Hadid", "SKU-KTC-0012", 1, "Shipped",
     ("Dispatched", 54, 50, 46, "MAN-2"),
     ("DPD9930120115", "DPD", "In Transit", 4, -18)),
    ("E2003", "eBay", 10, "Grzegorz Nowak", "SKU-PET-0033", 2, "Processing",
     ("Picking", None, None, None, "BHM-3"), None),
    ("E2004", "eBay", 36, "Hannah Oyelaran", "SKU-GRD-0113", 1, "Shipped",
     ("Dispatched", 30, 28, 26, "LDN-1"),
     ("EV7714209920", "Evri", "Collected", 20, -16)),
    # Exception: cancelled on the marketplace, shipped by the warehouse anyway.
    ("E2005", "eBay", 80, "Stefan Vogel", "SKU-HME-0207", 1, "Cancelled",
     ("Dispatched", 74, 70, 66, "MAN-2"),
     ("DPD9930121007", "DPD", "In Transit", 10, -6)),
    ("E2006", "eBay", 18, "Bilal Chaudhry", "SKU-KTC-0045", 1, "Processing",
     ("Packed", 12, 8, None, "LDN-1"), None),
    # Exception: delivered by the carrier, still "Processing" on the marketplace.
    ("E2007", "eBay", 100, "Fionnuala Doyle", "SKU-STY-0150", 1, "Processing",
     ("Dispatched", 94, 90, 86, "BHM-3"),
     ("RM418823771GB", "Royal Mail", "Delivered", 12, 8)),
    ("E2008", "eBay", 4, "Mateo Rivas", "SKU-GRD-0204", 2, "Pending",
     ("Received", None, None, None, "MAN-2"), None),
    # Exception: past the dispatch SLA and sitting packed.
    ("E2009", "eBay", 76, "Ruth Chikelu", "SKU-PET-0077", 1, "Processing",
     ("Packed", 70, 66, None, "LDN-1"), None),
    # Exception: dispatched, carrier record exists but has no tracking number.
    ("E2010", "eBay", 55, "Anders Holm", "SKU-HME-0091", 1, "Shipped",
     ("Dispatched", 50, 48, 46, "MAN-2"),
     ("", "Evri", "", None, None)),
    # Exception: parcel stuck "Out for Delivery" well past that status's SLA.
    ("E2011", "eBay", 96, "Suki Tanaka", "SKU-KTC-0012", 1, "Shipped",
     ("Dispatched", 90, 88, 86, "LDN-1"),
     ("DPD9930122483", "DPD", "Out for Delivery", 30, 26)),
    # Exception: failed delivery attempt.
    ("E2012", "eBay", 110, "Kofi Mensah", "SKU-PET-0033", 1, "Shipped",
     ("Dispatched", 104, 100, 96, "BHM-3"),
     ("EV7714215566", "Evri", "Delivery Failed", 20, 16)),

    # ---- Shopify ---------------------------------------------------------
    ("S3001", "Shopify", 110, "Elena Petrova", "SKU-HME-0207", 1, "Delivered",
     ("Dispatched", 104, 100, 96, "LDN-1"),
     ("RM418820998GB", "Royal Mail", "Delivered", 40, 36)),
    ("S3002", "Shopify", 22, "Jamal Idris", "SKU-GRD-0113", 1, "Processing",
     ("Picking", None, None, None, "MAN-2"), None),
    ("S3003", "Shopify", 66, "Clara Mendoza", "SKU-STY-0150", 2, "Shipped",
     ("Dispatched", 60, 56, 52, "LDN-1"),
     ("DPD9930123901", "DPD", "Out for Delivery", 5, -4)),
    # Exception: buyer told it shipped while the warehouse is still picking.
    ("S3004", "Shopify", 30, "Yusuf Demir", "SKU-KTC-0045", 1, "Shipped",
     ("Picking", None, None, None, "BHM-3"), None),
    ("S3005", "Shopify", 14, "Aoife Brennan", "SKU-PET-0077", 1, "Cancelled",
     ("Cancelled", None, None, None, "LDN-1"), None),
    # Exception: dispatched but the marketplace was never updated.
    ("S3006", "Shopify", 60, "Nadia Benali", "SKU-HME-0091", 1, "Processing",
     ("Dispatched", 54, 50, 46, "MAN-2"),
     ("DPD9930124338", "DPD", "In Transit", 8, -10)),
    ("S3007", "Shopify", 46, "Liam Osei", "SKU-GRD-0204", 3, "Shipped",
     ("Dispatched", 42, 40, 38, "LDN-1"),
     ("EV7714218870", "Evri", "In Transit", 12, -14)),
    # Exception: stuck in picking.
    ("S3008", "Shopify", 44, "Margit Halvorsen", "SKU-KTC-0012", 1, "Processing",
     ("Picking", None, None, None, "MAN-2"), None),
    # Exception: label created, parcel never scanned.
    ("S3009", "Shopify", 50, "Tunde Salami", "SKU-STY-0150", 1, "Shipped",
     ("Dispatched", 46, 44, 42, "BHM-3"),
     ("DPD9930125104", "DPD", "Label Created", None, -12)),
    # Exception: very stale tracking and stuck in the same status.
    ("S3010", "Shopify", 150, "Beatriz Carvalho", "SKU-PET-0033", 1, "Shipped",
     ("Dispatched", 144, 142, 140, "LDN-1"),
     ("EV7714221145", "Evri", "In Transit", 100, 80)),
    # Exception: marketplace order the warehouse never received (older).
    ("S3011", "Shopify", 70, "Karl Jorgensen", "SKU-HME-0207", 2, "Processing",
     None, None),
    ("S3012", "Shopify", 3, "Amara Nwosu", "SKU-GRD-0113", 1, "Pending",
     ("Received", None, None, None, "MAN-2"), None),
    ("S3013", "Shopify", 28, "Rory McKenna", "SKU-KTC-0045", 1, "Processing",
     ("Packed", 16, 10, None, "LDN-1"), None),
]

# Warehouse-only record: stock dispatched against an order the marketplace
# feed never delivered.
ORPHAN_WAREHOUSE = [
    ("W9001", "Dispatched", 30, 26, 22, "BHM-3",
     ("DPD9930126077", "DPD", "In Transit", 5, -18)),
]

# Dispatch promise offered to the buyer at checkout, in hours after the order.
DISPATCH_PROMISE_HOURS = 48


def write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"wrote {len(rows):>3} rows -> {path.relative_to(ROOT)}")


def main() -> None:
    marketplace_rows, warehouse_rows, tracking_rows = [], [], []

    for order_id, marketplace, placed_h, customer, sku, qty, status, wh, cr in ORDERS:
        marketplace_rows.append([
            order_id,
            marketplace,
            ts(placed_h),
            customer,
            sku,
            qty,
            status,
            date(placed_h - DISPATCH_PROMISE_HOURS),
        ])

        if wh is not None:
            wh_status, picked_h, packed_h, dispatched_h, location = wh
            warehouse_rows.append([
                order_id, wh_status, ts(picked_h), ts(packed_h), ts(dispatched_h), location,
            ])

        if cr is not None:
            tracking_number, carrier, tracking_status, last_update_h, eta_h = cr
            tracking_rows.append([
                order_id, tracking_number, carrier, tracking_status,
                ts(last_update_h), date(eta_h),
            ])

    for order_id, wh_status, picked_h, packed_h, dispatched_h, location, cr in ORPHAN_WAREHOUSE:
        warehouse_rows.append([
            order_id, wh_status, ts(picked_h), ts(packed_h), ts(dispatched_h), location,
        ])
        tracking_number, carrier, tracking_status, last_update_h, eta_h = cr
        tracking_rows.append([
            order_id, tracking_number, carrier, tracking_status, ts(last_update_h), date(eta_h),
        ])

    write_csv(ROOT / config.MARKETPLACE_FILE,
              config.REQUIRED_COLUMNS["marketplace_orders"], marketplace_rows)
    write_csv(ROOT / config.WAREHOUSE_FILE,
              config.REQUIRED_COLUMNS["warehouse_status"], warehouse_rows)
    write_csv(ROOT / config.TRACKING_FILE,
              config.REQUIRED_COLUMNS["carrier_tracking"], tracking_rows)


if __name__ == "__main__":
    main()
