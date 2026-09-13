"""Loading, validation and reconciliation of the three source datasets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config


class DataValidationError(Exception):
    """Raised when a source file is missing, empty or missing required columns."""


def load_dataset(path: str | Path, name: str) -> pd.DataFrame:
    """Read one source CSV, check its columns and parse its timestamps."""
    path = Path(path)
    if not path.exists():
        raise DataValidationError(f"{name}: file not found at {path}")

    frame = pd.read_csv(path, dtype=str, keep_default_na=True)

    required = config.REQUIRED_COLUMNS[name]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError(f"{name}: missing required column(s): {', '.join(missing)}")

    # Normalise the join key and drop rows that have no usable key.
    frame["order_id"] = frame["order_id"].astype(str).str.strip()
    frame = frame[frame["order_id"].ne("") & frame["order_id"].ne("nan")].copy()

    for column in config.TIMESTAMP_COLUMNS[name]:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")

    # Blank strings become NaN so downstream null handling is uniform.
    for column in frame.columns:
        if frame[column].dtype == object:
            frame[column] = frame[column].apply(
                lambda value: value.strip() if isinstance(value, str) else value
            )
            frame[column] = frame[column].replace("", pd.NA)

    if "quantity" in frame.columns:
        frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")

    duplicates = frame["order_id"].duplicated().sum()
    if duplicates:
        # Keep the last record per order - source systems append corrections.
        frame = frame.drop_duplicates(subset="order_id", keep="last")

    return frame.reset_index(drop=True)


def load_all(
    marketplace_path: str | Path = config.MARKETPLACE_FILE,
    warehouse_path: str | Path = config.WAREHOUSE_FILE,
    tracking_path: str | Path = config.TRACKING_FILE,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load all three datasets."""
    return (
        load_dataset(marketplace_path, "marketplace_orders"),
        load_dataset(warehouse_path, "warehouse_status"),
        load_dataset(tracking_path, "carrier_tracking"),
    )


def reconcile(
    orders: pd.DataFrame, warehouse: pd.DataFrame, tracking: pd.DataFrame
) -> pd.DataFrame:
    """Outer-join the three systems on ``order_id``.

    An outer join is deliberate: records that exist on only one side are
    exactly the "missing order" exceptions the checker needs to report.
    """
    orders = orders.assign(_in_marketplace=True)
    warehouse = warehouse.assign(_in_warehouse=True)
    tracking = tracking.assign(_in_tracking=True)

    merged = orders.merge(warehouse, on="order_id", how="outer", validate="one_to_one")
    merged = merged.merge(tracking, on="order_id", how="outer", validate="one_to_one")

    for flag in ("_in_marketplace", "_in_warehouse", "_in_tracking"):
        merged[flag] = merged[flag].fillna(False).astype(bool)

    return merged.sort_values("order_id").reset_index(drop=True)
