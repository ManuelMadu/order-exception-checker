"""Plain data holders shared by the loader, the rules and the report writer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pandas as pd


def _clean(value: Any) -> Optional[Any]:
    """Return ``None`` for NaN/NaT/blank values so rules can test truthiness safely."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # arrays / unsupported types
        pass
    if isinstance(value, str) and not value.strip():
        return None
    return value


@dataclass
class OrderView:
    """One order reconciled across the three source systems.

    Any of the three sides may be absent; the ``has_*`` flags say which.
    """

    order_id: str
    now: datetime

    # Marketplace
    marketplace: Optional[str] = None
    order_date: Optional[datetime] = None
    customer_name: Optional[str] = None
    sku: Optional[str] = None
    quantity: Optional[int] = None
    marketplace_status: Optional[str] = None
    expected_dispatch_date: Optional[datetime] = None

    # Warehouse
    warehouse_status: Optional[str] = None
    picked_at: Optional[datetime] = None
    packed_at: Optional[datetime] = None
    dispatched_at: Optional[datetime] = None
    warehouse_location: Optional[str] = None

    # Carrier
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None
    carrier_status: Optional[str] = None
    last_tracking_update: Optional[datetime] = None
    estimated_delivery_date: Optional[datetime] = None

    has_marketplace_record: bool = False
    has_warehouse_record: bool = False
    has_tracking_record: bool = False

    # Feeds that contradict themselves about this order. While this is non-empty
    # the statuses above came from a record the loader had to choose between, so
    # anything derived from them is unconfirmed rather than wrong.
    unreliable_sources: list = field(default_factory=list)

    @classmethod
    def from_row(cls, row: "pd.Series", now: datetime) -> "OrderView":
        """Build a view from one row of the reconciled dataframe."""
        get = lambda name: _clean(row.get(name))  # noqa: E731 - short local alias
        return cls(
            order_id=str(row["order_id"]),
            now=now,
            marketplace=get("marketplace"),
            order_date=get("order_date"),
            customer_name=get("customer_name"),
            sku=get("sku"),
            quantity=get("quantity"),
            marketplace_status=get("order_status"),
            expected_dispatch_date=get("expected_dispatch_date"),
            warehouse_status=get("warehouse_status"),
            picked_at=get("picked_at"),
            packed_at=get("packed_at"),
            dispatched_at=get("dispatched_at"),
            warehouse_location=get("warehouse_location"),
            tracking_number=get("tracking_number"),
            carrier=get("carrier"),
            carrier_status=get("tracking_status"),
            last_tracking_update=get("last_tracking_update"),
            estimated_delivery_date=get("estimated_delivery_date"),
            has_marketplace_record=bool(row.get("_in_marketplace", False)),
            has_warehouse_record=bool(row.get("_in_warehouse", False)),
            has_tracking_record=bool(row.get("_in_tracking", False)),
        )

    # -- time helpers -------------------------------------------------------
    def hours_since(self, moment: Optional[datetime]) -> Optional[float]:
        """Hours between ``moment`` and the reference time, or None if unknown."""
        if moment is None:
            return None
        return round((self.now - moment).total_seconds() / 3600.0, 1)

    @property
    def order_age_hours(self) -> Optional[float]:
        return self.hours_since(self.order_date)

    @property
    def hours_since_tracking_update(self) -> Optional[float]:
        return self.hours_since(self.last_tracking_update)

    # -- convenience predicates --------------------------------------------
    @property
    def is_unreliable(self) -> bool:
        return bool(self.unreliable_sources)

    @property
    def is_cancelled(self) -> bool:
        return "Cancelled" in (self.marketplace_status, self.warehouse_status)

    @property
    def warehouse_dispatched(self) -> bool:
        return self.warehouse_status == "Dispatched"

    def context(self) -> dict:
        """Values available to the suggested-action templates in config."""
        return {
            "order_id": self.order_id,
            "marketplace": self.marketplace or "Unknown",
            "sku": self.sku or "the item",
            "carrier": self.carrier or "the carrier",
            "tracking_number": self.tracking_number or "the consignment",
            "warehouse_location": self.warehouse_location or "the warehouse",
            "customer_name": self.customer_name or "the customer",
        }


@dataclass
class Finding:
    """A single detected exception."""

    order: OrderView
    code: str
    description: str
    age_hours: Optional[float] = None
    severity: Optional[str] = None  # set by the rule when it escalates
    owner: Optional[str] = None  # set when the owner depends on the data, not the rule
    extras: dict = field(default_factory=dict)  # extra values for the action template


@dataclass
class DataIssue:
    """A problem with a source file, found while loading it.

    These cannot be found later: once ``load_dataset`` has coerced a bad
    timestamp to NaT or dropped a keyless row, the evidence is gone.
    """

    code: str
    source_file: str  # dataset name, e.g. "warehouse_status"
    description: str
    order_id: Optional[str] = None  # None when the row had no usable key
    extras: dict = field(default_factory=dict)
