"""Loading, validation and reconciliation of the three source datasets."""

from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

from . import config
from .models import DataIssue


class DataValidationError(Exception):
    """Raised when a source file is missing, empty or missing required columns."""


def _is_present(value) -> bool:
    """True when the cell actually held text.

    ``read_csv(dtype=str)`` gives a string or a null, and a null is a float NaN
    which is truthy, so a plain truthiness test treats every empty cell as a
    value. That matters here: an empty timestamp is a step that has not happened
    yet, while a non-empty one that will not parse is a broken feed.
    """
    return isinstance(value, str) and bool(value.strip())


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


def load_dataset(path: str | Path, name: str) -> tuple[pd.DataFrame, list[DataIssue]]:
    """Read one source CSV, check its columns and parse its timestamps.

    Returns the cleaned frame and anything wrong with the file itself. The
    cleaning steps below are all lossy, so each one records what it discarded
    rather than throwing the evidence away silently.
    """
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

    issues: list[DataIssue] = []

    # Normalise the join key, then drop rows with no usable key. A null key must
    # not survive: two keyless rows from different files would otherwise join to
    # each other and invent an order that exists in neither.
    frame["order_id"] = [
        value.strip() if isinstance(value, str) else "" for value in frame["order_id"]
    ]
    keyless = frame.index[frame["order_id"] == ""]
    for position in keyless:
        # +2 puts the number in the same place a spreadsheet would: 1-indexed,
        # past the header, so the operator can open the file and find the row.
        issues.append(DataIssue(
            code="UNIDENTIFIED_SOURCE_ROW",
            source_file=name,
            description=f"Row {position + 2} of {name}.csv has no order_id and was dropped.",
            extras={"row": position + 2, "source_file": f"{name}.csv"},
        ))
    frame = frame[frame["order_id"] != ""].copy()

    for column in config.TIMESTAMP_COLUMNS[name]:
        raw = frame[column]
        with warnings.catch_warnings():
            # One unparseable value stops pandas inferring a single format and it
            # warns about the dateutil fallback. We report that value ourselves on
            # the next line, so the warning is noise on the operator's terminal.
            warnings.filterwarnings("ignore", message="Could not infer format")
            parsed = pd.to_datetime(raw, errors="coerce")
        # A value that was present but would not parse is a broken timestamp.
        # A value that was already blank is simply a step that has not happened.
        # astype(bool) because map on an all-null column keeps the source dtype.
        unparseable = parsed.isna() & raw.map(_is_present).astype(bool)
        for order_id, value in zip(frame.loc[unparseable, "order_id"], raw[unparseable]):
            issues.append(DataIssue(
                code="UNPARSEABLE_TIMESTAMP",
                source_file=name,
                order_id=order_id,
                description=f"{column} on order {order_id} reads {value!r}, which is not a "
                            f"readable timestamp, so age checks skip this order.",
                extras={"column": column, "value": value, "source_file": f"{name}.csv"},
            ))
        frame[column] = parsed

    timestamps = set(config.TIMESTAMP_COLUMNS[name])
    for column in frame.columns:
        if column not in timestamps and column != "order_id":
            frame[column] = frame[column].map(_clean_text)

    if "quantity" in frame.columns:
        frame["quantity"] = pd.to_numeric(frame["quantity"], errors="coerce")

    # Statuses outside the known vocabulary match no rule, so the order would
    # otherwise drop out of the report without anyone noticing.
    for (dataset, column), allowed in config.KNOWN_STATUS_VALUES.items():
        if dataset != name or column not in frame.columns:
            continue
        unknown = frame[column].notna() & ~frame[column].isin(allowed)
        for order_id, value in zip(frame.loc[unknown, "order_id"], frame.loc[unknown, column]):
            issues.append(DataIssue(
                code="UNKNOWN_STATUS_VALUE",
                source_file=name,
                order_id=order_id,
                description=f"{column} on order {order_id} is {value!r}, which is not one of "
                            f"{', '.join(allowed)}.",
                extras={"column": column, "value": value, "source_file": f"{name}.csv"},
            ))

    repeated = frame["order_id"].value_counts()
    for order_id, count in repeated[repeated > 1].items():
        issues.append(DataIssue(
            code="DUPLICATE_SOURCE_RECORD",
            source_file=name,
            order_id=order_id,
            description=f"{name}.csv contains {count} records for order {order_id}; "
                        f"the last one was used.",
            extras={"count": int(count), "source_file": f"{name}.csv"},
        ))
    if len(repeated[repeated > 1]):
        # Keep the last record per order - source systems append corrections.
        frame = frame.drop_duplicates(subset="order_id", keep="last")

    return frame.reset_index(drop=True), issues


def load_all(
    marketplace_path: str | Path = config.MARKETPLACE_FILE,
    warehouse_path: str | Path = config.WAREHOUSE_FILE,
    tracking_path: str | Path = config.TRACKING_FILE,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[DataIssue]]:
    """Load all three datasets, along with any problems found in the files."""
    frames = []
    issues: list[DataIssue] = []
    for path, name in (
        (marketplace_path, "marketplace_orders"),
        (warehouse_path, "warehouse_status"),
        (tracking_path, "carrier_tracking"),
    ):
        frame, file_issues = load_dataset(path, name)
        frames.append(frame)
        issues.extend(file_issues)
    return frames[0], frames[1], frames[2], issues


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
