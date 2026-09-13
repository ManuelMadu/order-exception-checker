"""Shared test helpers."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import config  # noqa: E402
from src.models import OrderView  # noqa: E402

NOW = config.REFERENCE_NOW


def ago(hours: float | None):
    """A datetime ``hours`` before the fixed reference time (None passes through)."""
    if hours is None:
        return None
    return NOW - timedelta(hours=hours)


def make_order(
    order_id: str = "T0001",
    marketplace: str = "Amazon",
    placed_hours_ago: float | None = 10,
    marketplace_status: str | None = "Processing",
    warehouse_status: str | None = None,
    picked_hours_ago: float | None = None,
    packed_hours_ago: float | None = None,
    dispatched_hours_ago: float | None = None,
    tracking_number: str | None = None,
    carrier: str | None = None,
    carrier_status: str | None = None,
    tracking_update_hours_ago: float | None = None,
    has_marketplace_record: bool | None = None,
    has_warehouse_record: bool | None = None,
    has_tracking_record: bool | None = None,
) -> OrderView:
    """Build an OrderView directly, so rules can be tested without CSV fixtures."""
    return OrderView(
        order_id=order_id,
        now=NOW,
        marketplace=marketplace,
        order_date=ago(placed_hours_ago),
        customer_name="Test Buyer",
        sku="SKU-TST-0001",
        quantity=1,
        marketplace_status=marketplace_status,
        warehouse_status=warehouse_status,
        picked_at=ago(picked_hours_ago),
        packed_at=ago(packed_hours_ago),
        dispatched_at=ago(dispatched_hours_ago),
        warehouse_location="LDN-1",
        tracking_number=tracking_number,
        carrier=carrier,
        carrier_status=carrier_status,
        last_tracking_update=ago(tracking_update_hours_ago),
        has_marketplace_record=(
            marketplace_status is not None if has_marketplace_record is None else has_marketplace_record
        ),
        has_warehouse_record=(
            warehouse_status is not None if has_warehouse_record is None else has_warehouse_record
        ),
        has_tracking_record=(
            carrier is not None if has_tracking_record is None else has_tracking_record
        ),
    )


def codes(findings) -> set:
    return {finding.code for finding in findings}


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT
