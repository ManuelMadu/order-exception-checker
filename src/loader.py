"""Loading, validation and reconciliation of the three source datasets."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config


class DataValidationError(Exception):
    """Raised when a source file is missing, empty or missing required columns."""


def _clean_text(value):
    """Strip surrounding whitespace and turn blanks into nulls.

    Deliberately value-by-value rather than column-by-column. pandas 2 reads a
    ``dtype=str`` CSV back as ``object`` dtype while pandas 3 uses ``str``, so any
    normalisation guarded on the column dtype silently stops running on one of
    them. Both pandas majors are in the CI matrix, so this has to hold for both.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else pd.NA
    return value


def load_dataset(path: str | Path, name: str) -> pd.DataFrame:
    """Read one source CSV, check its columns and parse its timestamps."""
    path = Path(path)
    if not path.exists():
        raise DataValidationError(f"{name}: file not found at {path}")

    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=True)
    except pd.errors.EmptyDataError as error:
        raise DataValidationError(f"{name}: file at {path} is empty") from error
    except pd.errors.ParserError as error:
        raise DataValidationError(f"{name}: could not parse {path}: {error}") from error

    required = config.REQUIRED_COLUMNS[name]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise DataValidationError(f"{name}: missing required column(s): {', '.join(missing)}")

    # Normalise the join key, then drop rows with no usable key. A null key must
    # not survive: two keyless rows from different files would otherwise join to
    # each other and invent an order that exists in neither.
    frame["order_id"] = [
        value.strip() if isinstance(value, str) else "" for value in frame["order_id"]
    ]
    frame = frame[frame["order_id"] != ""].copy()

    for column in config.TIMESTAMP_COLUMNS[name]:
        frame[column] = pd.to_datetime(frame[column], errors="coerce")

    timestamps = set(config.TIMESTAMP_COLUMNS[name])
    for column in frame.columns:
        if column not in timestamps and column != "order_id":
            frame[column] = frame[column].map(_clean_text)

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

    # After the outer join each flag is True or missing. ``eq`` gives a clean
    # boolean column without the dtype downcasting that fillna would trigger.
    for flag in ("_in_marketplace", "_in_warehouse", "_in_tracking"):
        merged[flag] = merged[flag].eq(True)

    return merged.sort_values("order_id").reset_index(drop=True)
