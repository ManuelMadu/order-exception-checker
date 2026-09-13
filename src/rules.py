"""Exception detection rules.

Each rule is a small function that takes one reconciled :class:`OrderView` and
returns the exceptions it finds. Rules are independent, so an order can raise
more than one - that is normal (for example a parcel can be both stale and
stuck in the same tracking status).

Thresholds, severities, owners and suggested actions all come from
``config``; the functions below only decide *whether* a condition is true.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from . import config
from .models import Finding, OrderView

SLA = config.SLA_HOURS


def _breach(order: OrderView, code: str, description: str, age: Optional[float],
            limit: Optional[float] = None) -> Finding:
    """Build a finding, escalating severity when a time limit is badly overshot."""
    spec = config.RULES[code]
    ratio = (age / limit) if (age is not None and limit) else None
    return Finding(
        order=order,
        code=code,
        description=description,
        age_hours=age,
        severity=config.escalate_severity(spec.base_severity, ratio),
    )


# ---------------------------------------------------------------------------
# 1. Missing orders
# ---------------------------------------------------------------------------
def check_missing_warehouse_record(order: OrderView) -> List[Finding]:
    """Marketplace sold it, the warehouse has never heard of it."""
    if not order.has_marketplace_record or order.has_warehouse_record:
        return []
    if order.marketplace_status == "Cancelled":
        return []  # a cancelled order never reaching the warehouse is fine
    age = order.order_age_hours
    return [
        _breach(
            order,
            "MISSING_WAREHOUSE_RECORD",
            f"Order placed {age:.0f}h ago on {order.marketplace} has no warehouse record."
            if age is not None
            else f"Order on {order.marketplace} has no warehouse record.",
            age,
            SLA["warehouse_intake"],
        )
    ]


def check_orphan_warehouse_record(order: OrderView) -> List[Finding]:
    """The warehouse dispatched stock against an order the marketplace never sent."""
    if order.has_marketplace_record or not order.has_warehouse_record:
        return []
    if not order.warehouse_dispatched:
        return []
    age = order.hours_since(order.dispatched_at)
    return [
        _breach(
            order,
            "ORPHAN_WAREHOUSE_RECORD",
            f"Warehouse dispatched this order from {order.warehouse_location} "
            f"but no marketplace order exists.",
            age,
        )
    ]


def check_missing_tracking_record(order: OrderView) -> List[Finding]:
    """Dispatched, but the carrier feed has no row for it at all."""
    if not order.warehouse_dispatched or order.has_tracking_record:
        return []
    age = order.hours_since(order.dispatched_at)
    return [
        _breach(
            order,
            "MISSING_TRACKING_RECORD",
            f"Dispatched {age:.0f}h ago with no carrier tracking record."
            if age is not None
            else "Dispatched with no carrier tracking record.",
            age,
            SLA["tracking_stale"],
        )
    ]


# ---------------------------------------------------------------------------
# 2. Status mismatches
# ---------------------------------------------------------------------------
def check_cancelled_but_fulfilled(order: OrderView) -> List[Finding]:
    """Marketplace says cancelled, the warehouse packed or shipped it anyway."""
    if order.marketplace_status != "Cancelled":
        return []
    if order.warehouse_status not in config.WAREHOUSE_COMMITTED_STATUSES:
        return []
    age = order.hours_since(order.dispatched_at or order.packed_at)
    return [
        _breach(
            order,
            "CANCELLED_BUT_FULFILLED",
            f"Marketplace shows Cancelled but warehouse shows {order.warehouse_status}.",
            age,
        )
    ]


def check_carrier_active_on_cancelled_order(order: OrderView) -> List[Finding]:
    """A cancelled order is moving through the carrier network."""
    if order.marketplace_status != "Cancelled":
        return []
    if order.carrier_status not in config.CARRIER_ACTIVE_STATUSES:
        return []
    age = order.hours_since_tracking_update
    return [
        _breach(
            order,
            "CARRIER_ACTIVE_ON_CANCELLED_ORDER",
            f"Order is cancelled but {order.carrier} reports {order.carrier_status}.",
            age,
        )
    ]


def check_shipped_not_dispatched(order: OrderView) -> List[Finding]:
    """The buyer has been told it shipped while the warehouse is still working on it."""
    if order.marketplace_status not in config.MARKETPLACE_FULFILLED_STATUSES:
        return []
    if not order.has_warehouse_record or order.warehouse_dispatched:
        return []
    if order.warehouse_status == "Cancelled":
        return []  # covered by the cancellation rules
    age = order.order_age_hours
    return [
        _breach(
            order,
            "SHIPPED_NOT_DISPATCHED",
            f"Marketplace shows {order.marketplace_status} but warehouse is still "
            f"{order.warehouse_status}.",
            age,
        )
    ]


def check_dispatch_not_reflected(order: OrderView) -> List[Finding]:
    """Warehouse dispatched it; the marketplace still shows the order as open."""
    if not order.warehouse_dispatched:
        return []
    if order.marketplace_status not in config.MARKETPLACE_OPEN_STATUSES:
        return []
    if order.carrier_status == "Delivered":
        return []  # the stronger "delivered but not updated" rule covers this
    age = order.hours_since(order.dispatched_at)
    return [
        _breach(
            order,
            "DISPATCHED_NOT_UPDATED_ON_MARKETPLACE",
            f"Dispatched {age:.0f}h ago but marketplace still shows "
            f"{order.marketplace_status}."
            if age is not None
            else f"Dispatched but marketplace still shows {order.marketplace_status}.",
            age,
            SLA["warehouse_intake"],
        )
    ]


def check_delivered_but_marketplace_behind(order: OrderView) -> List[Finding]:
    """Carrier delivered it; the marketplace has not caught up."""
    if order.carrier_status != "Delivered":
        return []
    if order.marketplace_status not in config.MARKETPLACE_OPEN_STATUSES:
        return []
    age = order.hours_since_tracking_update
    return [
        _breach(
            order,
            "DELIVERED_BUT_MARKETPLACE_BEHIND",
            f"Carrier confirmed delivery but marketplace still shows "
            f"{order.marketplace_status}.",
            age,
        )
    ]


# ---------------------------------------------------------------------------
# 3. SLA breaches
# ---------------------------------------------------------------------------
def _sla_applies(order: OrderView) -> bool:
    """Fulfilment SLAs only apply to live orders the warehouse has acknowledged."""
    return order.has_warehouse_record and not order.is_cancelled


def check_warehouse_intake_sla(order: OrderView) -> List[Finding]:
    """Order has sat at "Received" longer than the intake SLA allows."""
    if not _sla_applies(order) or order.warehouse_status != "Received":
        return []
    age = order.order_age_hours
    limit = SLA["warehouse_intake"]
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "WAREHOUSE_INTAKE_SLA_BREACH",
            f"Still 'Received' {age:.0f}h after the order was placed "
            f"(SLA {limit}h).",
            age,
            limit,
        )
    ]


def check_dispatch_sla(order: OrderView) -> List[Finding]:
    """Order is past the dispatch SLA and still has not left the warehouse."""
    if not _sla_applies(order) or order.warehouse_dispatched:
        return []
    age = order.order_age_hours
    limit = SLA["dispatch"]
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "DISPATCH_SLA_BREACH",
            f"Not dispatched {age:.0f}h after the order was placed (SLA {limit}h); "
            f"warehouse status is {order.warehouse_status}.",
            age,
            limit,
        )
    ]


def check_stuck_in_picking(order: OrderView) -> List[Finding]:
    """Picking has been open too long (measured from order placement)."""
    if not _sla_applies(order) or order.warehouse_status != "Picking":
        return []
    age = order.order_age_hours
    limit = SLA["max_in_picking"]
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "STUCK_IN_PICKING",
            f"In 'Picking' for {age:.0f}h (SLA {limit}h).",
            age,
            limit,
        )
    ]


def check_stuck_in_packed(order: OrderView) -> List[Finding]:
    """Packed but never collected within the allowed window."""
    if not _sla_applies(order) or order.warehouse_status != "Packed":
        return []
    # Dwell is measured from packing; fall back to order age if packed_at is missing.
    age = order.hours_since(order.packed_at)
    if age is None:
        age = order.order_age_hours
    limit = SLA["max_in_packed"]
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "STUCK_IN_PACKED",
            f"Packed {age:.0f}h ago and still awaiting dispatch (SLA {limit}h).",
            age,
            limit,
        )
    ]


# ---------------------------------------------------------------------------
# 4. Missing or stale parcel tracking
# ---------------------------------------------------------------------------
def check_missing_tracking_number(order: OrderView) -> List[Finding]:
    """A tracking row exists for a dispatched order but carries no tracking number."""
    if not order.warehouse_dispatched or not order.has_tracking_record:
        return []
    if order.tracking_number:
        return []
    age = order.hours_since(order.dispatched_at)
    return [
        _breach(
            order,
            "MISSING_TRACKING_NUMBER",
            f"Dispatched {age:.0f}h ago with no tracking number on the carrier record."
            if age is not None
            else "Dispatched with no tracking number on the carrier record.",
            age,
            SLA["tracking_stale"],
        )
    ]


def check_no_tracking_events(order: OrderView) -> List[Finding]:
    """A tracking number was issued but the parcel has never been scanned."""
    if not order.has_tracking_record or not order.tracking_number:
        return []
    if order.last_tracking_update is not None:
        return []
    age = order.hours_since(order.dispatched_at)
    if age is None:
        age = order.order_age_hours
    return [
        _breach(
            order,
            "NO_TRACKING_EVENTS",
            f"Tracking number {order.tracking_number} has no tracking events "
            f"({order.carrier_status or 'no status'}).",
            age,
            SLA["tracking_stale"],
        )
    ]


def check_stale_tracking(order: OrderView) -> List[Finding]:
    """No tracking movement for longer than the tracking SLA."""
    if not order.has_tracking_record or order.last_tracking_update is None:
        return []
    if order.carrier_status in (None, "Delivered"):
        return []
    age = order.hours_since_tracking_update
    limit = SLA["tracking_stale"]
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "STALE_TRACKING",
            f"No tracking update for {age:.0f}h while status is "
            f"{order.carrier_status} (SLA {limit}h).",
            age,
            limit,
        )
    ]


def check_tracking_status_sla(order: OrderView) -> List[Finding]:
    """Parcel has dwelled in one non-delivered status beyond that status's SLA."""
    limit = config.TRACKING_STATUS_SLA_HOURS.get(order.carrier_status or "")
    if limit is None or order.last_tracking_update is None:
        return []
    age = order.hours_since_tracking_update
    if age is None or age <= limit:
        return []
    return [
        _breach(
            order,
            "TRACKING_STATUS_SLA_BREACH",
            f"Parcel has been '{order.carrier_status}' for {age:.0f}h "
            f"(SLA {limit}h for that status).",
            age,
            limit,
        )
    ]


def check_delivery_failed(order: OrderView) -> List[Finding]:
    """The carrier reported a failed delivery attempt."""
    if order.carrier_status != "Delivery Failed":
        return []
    age = order.hours_since_tracking_update
    return [
        _breach(
            order,
            "DELIVERY_FAILED",
            f"{order.carrier} reported a failed delivery "
            f"{age:.0f}h ago." if age is not None else f"{order.carrier} reported a failed delivery.",
            age,
        )
    ]


# Rules run in this order; the report is sorted afterwards by severity.
RULE_FUNCTIONS: List[Callable[[OrderView], List[Finding]]] = [
    check_missing_warehouse_record,
    check_orphan_warehouse_record,
    check_missing_tracking_record,
    check_cancelled_but_fulfilled,
    check_carrier_active_on_cancelled_order,
    check_shipped_not_dispatched,
    check_dispatch_not_reflected,
    check_delivered_but_marketplace_behind,
    check_warehouse_intake_sla,
    check_dispatch_sla,
    check_stuck_in_picking,
    check_stuck_in_packed,
    check_missing_tracking_number,
    check_no_tracking_events,
    check_stale_tracking,
    check_tracking_status_sla,
    check_delivery_failed,
]


def evaluate(order: OrderView) -> List[Finding]:
    """Run every rule against one order."""
    findings: List[Finding] = []
    for rule in RULE_FUNCTIONS:
        findings.extend(rule(order))
    return findings
