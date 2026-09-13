"""Central configuration for the Order Exception Checker.

Everything tunable lives here: SLA thresholds, status vocabularies, and the
exception rule registry (severity, escalation owner and suggested action).
Detection logic in ``rules.py`` reads these values - it never hard-codes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Reference time
# ---------------------------------------------------------------------------
# The bundled synthetic data is dated relative to this fixed instant so the
# demo keeps producing the same exceptions no matter when it is run.
# All timestamps in the CSVs are naive UTC ("YYYY-MM-DD HH:MM:SS").
# Pass --now to main.py to analyse against a different reference time.
REFERENCE_NOW = datetime(2026, 3, 16, 9, 0, 0)

# ---------------------------------------------------------------------------
# File locations (relative to the project root)
# ---------------------------------------------------------------------------
MARKETPLACE_FILE = "data/marketplace_orders.csv"
WAREHOUSE_FILE = "data/warehouse_status.csv"
TRACKING_FILE = "data/carrier_tracking.csv"
REPORT_FILE = "output/exception_report.csv"

# ---------------------------------------------------------------------------
# Required columns - loading fails fast if any of these are missing
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = {
    "marketplace_orders": [
        "order_id",
        "marketplace",
        "order_date",
        "customer_name",
        "sku",
        "quantity",
        "order_status",
        "expected_dispatch_date",
    ],
    "warehouse_status": [
        "order_id",
        "warehouse_status",
        "picked_at",
        "packed_at",
        "dispatched_at",
        "warehouse_location",
    ],
    "carrier_tracking": [
        "order_id",
        "tracking_number",
        "carrier",
        "tracking_status",
        "last_tracking_update",
        "estimated_delivery_date",
    ],
}

# Columns parsed as timestamps when each file is loaded.
TIMESTAMP_COLUMNS = {
    "marketplace_orders": ["order_date", "expected_dispatch_date"],
    "warehouse_status": ["picked_at", "packed_at", "dispatched_at"],
    "carrier_tracking": ["last_tracking_update", "estimated_delivery_date"],
}

# ---------------------------------------------------------------------------
# Status vocabularies
# ---------------------------------------------------------------------------
MARKETPLACE_STATUSES = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled"]
WAREHOUSE_STATUSES = ["Received", "Picking", "Picked", "Packed", "Dispatched", "Cancelled"]
TRACKING_STATUSES = [
    "Label Created",
    "Collected",
    "In Transit",
    "Out for Delivery",
    "Delivered",
    "Delivery Failed",
]

# Marketplace statuses that claim the parcel has left the building.
MARKETPLACE_FULFILLED_STATUSES = ["Shipped", "Delivered"]
# Marketplace statuses that mean the seller has not told the buyer anything yet.
MARKETPLACE_OPEN_STATUSES = ["Pending", "Processing"]
# Warehouse statuses that mean physical fulfilment has happened (or is about to).
WAREHOUSE_COMMITTED_STATUSES = ["Packed", "Dispatched"]
# Carrier statuses that mean a parcel is live in the network.
CARRIER_ACTIVE_STATUSES = ["Collected", "In Transit", "Out for Delivery", "Delivered"]

# ---------------------------------------------------------------------------
# SLA thresholds (hours) - change these, not the rule code
# ---------------------------------------------------------------------------
SLA_HOURS = {
    # An order must move past "Received" (i.e. into picking) within this long.
    "warehouse_intake": 24,
    # An order must be dispatched within this long of being placed.
    "dispatch": 48,
    # Maximum time an order may sit in "Picking" (measured from order placement).
    "max_in_picking": 36,
    # Maximum time an order may sit "Packed" waiting for collection.
    "max_in_packed": 12,
    # Maximum time a live parcel may go without any tracking update.
    "tracking_stale": 48,
}

# Maximum time a parcel may dwell in one non-delivered tracking status.
# "Delivery Failed" is absent on purpose: it is flagged by its own rule.
TRACKING_STATUS_SLA_HOURS = {
    "Label Created": 24,
    "Collected": 48,
    "In Transit": 72,
    "Out for Delivery": 24,
}

# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------
SEVERITY_LEVELS = ["Low", "Medium", "High", "Critical"]

# A time-based breach is escalated when it overshoots its SLA badly:
# (overshoot ratio, number of severity levels to add). Checked most severe first.
SEVERITY_ESCALATION = [(3.0, 2), (2.0, 1)]


def escalate_severity(base: str, ratio: float | None) -> str:
    """Raise ``base`` severity when a breach overshoots its SLA by ``ratio``."""
    if ratio is None:
        return base
    index = SEVERITY_LEVELS.index(base)
    for threshold, steps in SEVERITY_ESCALATION:
        if ratio >= threshold:
            index += steps
            break
    return SEVERITY_LEVELS[min(index, len(SEVERITY_LEVELS) - 1)]


# ---------------------------------------------------------------------------
# Escalation owners
# ---------------------------------------------------------------------------
OWNER_MARKETPLACE = "Seller / Marketplace"
OWNER_WAREHOUSE = "Warehouse"
OWNER_CARRIER = "Carrier"

# ---------------------------------------------------------------------------
# Exception categories (used for the summary counts)
# ---------------------------------------------------------------------------
CATEGORY_MISSING = "Missing orders"
CATEGORY_MISMATCH = "Status mismatches"
CATEGORY_SLA = "SLA breaches"
CATEGORY_TRACKING = "Tracking exceptions"

CATEGORY_ORDER = [CATEGORY_MISSING, CATEGORY_MISMATCH, CATEGORY_SLA, CATEGORY_TRACKING]


# ---------------------------------------------------------------------------
# Exception rule registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RuleSpec:
    """Static metadata for one exception type.

    ``action`` is a template formatted with the order context, so the
    operational recommendation stays configuration rather than code.
    """

    label: str
    category: str
    base_severity: str
    owner: str
    action: str


RULES: dict[str, RuleSpec] = {
    # -- Missing orders -----------------------------------------------------
    "MISSING_WAREHOUSE_RECORD": RuleSpec(
        label="Missing Warehouse Record",
        category=CATEGORY_MISSING,
        base_severity="High",
        owner=OWNER_WAREHOUSE,
        action="Contact warehouse to confirm whether order {order_id} was received.",
    ),
    "ORPHAN_WAREHOUSE_RECORD": RuleSpec(
        label="Orphan Warehouse Dispatch",
        category=CATEGORY_MISSING,
        base_severity="Critical",
        owner=OWNER_MARKETPLACE,
        action=(
            "Stock has left the warehouse with no marketplace order. Check the "
            "marketplace feed for a dropped or deleted order before writing off stock."
        ),
    ),
    "MISSING_TRACKING_RECORD": RuleSpec(
        label="Dispatched Without Tracking Record",
        category=CATEGORY_MISSING,
        base_severity="High",
        owner=OWNER_CARRIER,
        action="Ask the carrier for the consignment raised against order {order_id}; no tracking record exists.",
    ),
    # -- Status mismatches --------------------------------------------------
    "CANCELLED_BUT_FULFILLED": RuleSpec(
        label="Cancelled Order Fulfilled",
        category=CATEGORY_MISMATCH,
        base_severity="Critical",
        owner=OWNER_WAREHOUSE,
        action="Confirm cancellation before shipment continues and recover the parcel if it has already left.",
    ),
    "CARRIER_ACTIVE_ON_CANCELLED_ORDER": RuleSpec(
        label="Carrier Moving Cancelled Order",
        category=CATEGORY_MISMATCH,
        base_severity="Critical",
        owner=OWNER_CARRIER,
        action="Request a carrier intercept or return to sender for {tracking_number}; the order is cancelled.",
    ),
    "SHIPPED_NOT_DISPATCHED": RuleSpec(
        label="Marketplace Shipped Before Dispatch",
        category=CATEGORY_MISMATCH,
        base_severity="High",
        owner=OWNER_MARKETPLACE,
        action=(
            "Roll the marketplace status back until the warehouse confirms dispatch; "
            "the buyer has been told the order is on its way."
        ),
    ),
    "DISPATCHED_NOT_UPDATED_ON_MARKETPLACE": RuleSpec(
        label="Dispatch Not Reflected On Marketplace",
        category=CATEGORY_MISMATCH,
        base_severity="Medium",
        owner=OWNER_MARKETPLACE,
        action="Update marketplace order to dispatched after verifying warehouse confirmation.",
    ),
    "DELIVERED_BUT_MARKETPLACE_BEHIND": RuleSpec(
        label="Delivered But Marketplace Not Updated",
        category=CATEGORY_MISMATCH,
        base_severity="High",
        owner=OWNER_MARKETPLACE,
        action=(
            "Mark order {order_id} as delivered on the marketplace; the carrier has "
            "already delivered it and the listing is still open."
        ),
    ),
    # -- SLA breaches -------------------------------------------------------
    "WAREHOUSE_INTAKE_SLA_BREACH": RuleSpec(
        label="Warehouse Intake SLA Breach",
        category=CATEGORY_SLA,
        base_severity="Medium",
        owner=OWNER_WAREHOUSE,
        action="Chase the warehouse to start picking order {order_id}; it has not entered processing.",
    ),
    "DISPATCH_SLA_BREACH": RuleSpec(
        label="Dispatch SLA Breach",
        category=CATEGORY_SLA,
        base_severity="High",
        owner=OWNER_WAREHOUSE,
        action="Investigate warehouse fulfilment delay and give the buyer a revised dispatch date.",
    ),
    "STUCK_IN_PICKING": RuleSpec(
        label="Stuck In Picking",
        category=CATEGORY_SLA,
        base_severity="Medium",
        owner=OWNER_WAREHOUSE,
        action="Ask the warehouse whether {sku} is short-picked or out of stock at {warehouse_location}.",
    ),
    "STUCK_IN_PACKED": RuleSpec(
        label="Stuck In Packed",
        category=CATEGORY_SLA,
        base_severity="Medium",
        owner=OWNER_WAREHOUSE,
        action="Check whether the carrier collection at {warehouse_location} was missed.",
    ),
    # -- Tracking exceptions ------------------------------------------------
    "MISSING_TRACKING_NUMBER": RuleSpec(
        label="Missing Tracking Number",
        category=CATEGORY_TRACKING,
        base_severity="High",
        owner=OWNER_CARRIER,
        action="Obtain the tracking number for order {order_id} from {carrier} and publish it to the buyer.",
    ),
    "NO_TRACKING_EVENTS": RuleSpec(
        label="No Tracking Events",
        category=CATEGORY_TRACKING,
        base_severity="High",
        owner=OWNER_CARRIER,
        action="A label exists but the parcel was never scanned. Ask {carrier} to confirm collection of {tracking_number}.",
    ),
    "STALE_TRACKING": RuleSpec(
        label="Stale Tracking",
        category=CATEGORY_TRACKING,
        base_severity="High",
        owner=OWNER_CARRIER,
        action="Contact the carrier to investigate the lack of tracking updates on {tracking_number}.",
    ),
    "TRACKING_STATUS_SLA_BREACH": RuleSpec(
        label="Parcel Stuck In Tracking Status",
        category=CATEGORY_TRACKING,
        base_severity="Medium",
        owner=OWNER_CARRIER,
        action="Open a query with {carrier}: {tracking_number} has not moved past its current status.",
    ),
    "DELIVERY_FAILED": RuleSpec(
        label="Delivery Failed",
        category=CATEGORY_TRACKING,
        base_severity="High",
        owner=OWNER_CARRIER,
        action="Arrange redelivery with {carrier} and tell the buyer why the first attempt failed.",
    ),
}

# Columns of the generated exception report, in order.
REPORT_COLUMNS = [
    "order_id",
    "marketplace",
    "exception_type",
    "exception_description",
    "marketplace_status",
    "warehouse_status",
    "carrier_status",
    "age_hours",
    "severity",
    "escalation_owner",
    "suggested_action",
]
