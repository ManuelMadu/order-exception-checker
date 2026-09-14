"""Tests for the exception rules and the end-to-end pipeline."""

from __future__ import annotations

import pandas as pd
import pytest

from conftest import codes, make_order
from src import checker, config, rules
from src.loader import DataValidationError, load_dataset, reconcile


# ---------------------------------------------------------------------------
# Missing orders
# ---------------------------------------------------------------------------
def test_marketplace_order_without_warehouse_record_is_flagged():
    order = make_order(placed_hours_ago=40, warehouse_status=None)
    findings = rules.check_missing_warehouse_record(order)
    assert codes(findings) == {"MISSING_WAREHOUSE_RECORD"}
    assert config.RULES[findings[0].code].owner == config.OWNER_WAREHOUSE


def test_cancelled_order_without_warehouse_record_is_not_flagged():
    order = make_order(marketplace_status="Cancelled", warehouse_status=None)
    assert rules.check_missing_warehouse_record(order) == []


def test_warehouse_dispatch_without_marketplace_order_is_flagged():
    order = make_order(
        marketplace_status=None,
        marketplace=None,
        placed_hours_ago=None,
        warehouse_status="Dispatched",
        dispatched_hours_ago=20,
        has_marketplace_record=False,
    )
    findings = rules.check_orphan_warehouse_record(order)
    assert codes(findings) == {"ORPHAN_WAREHOUSE_RECORD"}
    assert findings[0].severity == "Critical"


def test_dispatched_order_without_tracking_record_is_flagged():
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched",
        dispatched_hours_ago=30, carrier=None,
    )
    assert codes(rules.check_missing_tracking_record(order)) == {"MISSING_TRACKING_RECORD"}


# ---------------------------------------------------------------------------
# Status mismatches
# ---------------------------------------------------------------------------
def test_cancelled_but_dispatched_is_critical_and_owned_by_warehouse():
    order = make_order(
        marketplace_status="Cancelled", warehouse_status="Dispatched", dispatched_hours_ago=12,
    )
    findings = rules.check_cancelled_but_fulfilled(order)
    assert codes(findings) == {"CANCELLED_BUT_FULFILLED"}
    assert findings[0].severity == "Critical"
    assert config.RULES[findings[0].code].owner == config.OWNER_WAREHOUSE


def test_shipped_while_warehouse_still_picking_escalates_to_marketplace():
    order = make_order(marketplace_status="Shipped", warehouse_status="Picking", placed_hours_ago=20)
    findings = rules.check_shipped_not_dispatched(order)
    assert codes(findings) == {"SHIPPED_NOT_DISPATCHED"}
    assert config.RULES[findings[0].code].owner == config.OWNER_MARKETPLACE


def test_dispatch_not_reflected_on_marketplace_is_flagged():
    order = make_order(
        marketplace_status="Processing", warehouse_status="Dispatched",
        dispatched_hours_ago=config.SLA_HOURS["marketplace_update"] + 6,
        carrier="DPD", carrier_status="In Transit",
        tracking_update_hours_ago=4, tracking_number="DPD1",
    )
    assert codes(rules.check_dispatch_not_reflected(order)) == {
        "DISPATCHED_NOT_UPDATED_ON_MARKETPLACE"
    }


def test_marketplace_is_given_a_window_to_reflect_a_dispatch():
    """A dispatch from 30 minutes ago has not had a chance to propagate yet."""
    order = make_order(
        marketplace_status="Processing", warehouse_status="Dispatched",
        placed_hours_ago=6, dispatched_hours_ago=0.5, carrier="DPD",
        carrier_status="In Transit", tracking_update_hours_ago=0.2, tracking_number="DPD1",
    )
    assert rules.check_dispatch_not_reflected(order) == []


def test_delivered_parcel_supersedes_the_dispatch_mismatch_rule():
    """A delivered parcel raises the stronger rule only, not both."""
    order = make_order(
        marketplace_status="Processing", warehouse_status="Dispatched",
        dispatched_hours_ago=40, carrier="Royal Mail", carrier_status="Delivered",
        tracking_update_hours_ago=6, tracking_number="RM1",
    )
    assert rules.check_dispatch_not_reflected(order) == []
    assert codes(rules.check_delivered_but_marketplace_behind(order)) == {
        "DELIVERED_BUT_MARKETPLACE_BEHIND"
    }


def test_matching_statuses_raise_nothing():
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", placed_hours_ago=20,
        picked_hours_ago=16, packed_hours_ago=14, dispatched_hours_ago=12,
        carrier="DPD", carrier_status="In Transit", tracking_update_hours_ago=3,
        tracking_number="DPD2",
    )
    assert rules.evaluate(order) == []


# ---------------------------------------------------------------------------
# SLA breaches
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("age,expected", [(20, False), (34, True)])
def test_warehouse_intake_sla_uses_the_configured_threshold(age, expected):
    order = make_order(warehouse_status="Received", placed_hours_ago=age)
    assert bool(rules.check_warehouse_intake_sla(order)) is expected


def test_dispatch_sla_breach_is_flagged_once_past_the_threshold():
    limit = config.SLA_HOURS["dispatch"]
    order = make_order(warehouse_status="Packed", placed_hours_ago=limit + 5,
                       picked_hours_ago=limit, packed_hours_ago=limit - 2)
    assert codes(rules.check_dispatch_sla(order)) == {"DISPATCH_SLA_BREACH"}


def test_sla_rules_ignore_cancelled_orders():
    order = make_order(marketplace_status="Cancelled", warehouse_status="Cancelled",
                       placed_hours_ago=200)
    assert rules.check_dispatch_sla(order) == []
    assert rules.check_warehouse_intake_sla(order) == []


def test_stuck_in_picking_and_packed():
    picking = make_order(warehouse_status="Picking",
                         placed_hours_ago=config.SLA_HOURS["max_in_picking"] + 8)
    packed = make_order(warehouse_status="Packed", placed_hours_ago=40,
                        picked_hours_ago=30,
                        packed_hours_ago=config.SLA_HOURS["max_in_packed"] + 14)
    assert codes(rules.check_stuck_in_picking(picking)) == {"STUCK_IN_PICKING"}
    assert codes(rules.check_stuck_in_packed(packed)) == {"STUCK_IN_PACKED"}


def test_severity_escalates_when_an_sla_is_badly_overshot():
    limit = config.SLA_HOURS["max_in_packed"]
    mild = make_order(warehouse_status="Packed", placed_hours_ago=20, packed_hours_ago=limit + 1)
    severe = make_order(warehouse_status="Packed", placed_hours_ago=80, packed_hours_ago=limit * 4)
    assert rules.check_stuck_in_packed(mild)[0].severity == "Medium"
    assert rules.check_stuck_in_packed(severe)[0].severity == "Critical"


# ---------------------------------------------------------------------------
# Tracking exceptions
# ---------------------------------------------------------------------------
def test_dispatched_without_tracking_number_is_carrier_owned():
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", dispatched_hours_ago=20,
        carrier="Evri", tracking_number=None,
    )
    findings = rules.check_missing_tracking_number(order)
    assert codes(findings) == {"MISSING_TRACKING_NUMBER"}
    assert config.RULES[findings[0].code].owner == config.OWNER_CARRIER


def test_tracking_number_with_no_events_is_flagged():
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", dispatched_hours_ago=20,
        carrier="DPD", tracking_number="DPD3", carrier_status="Label Created",
        tracking_update_hours_ago=None,
    )
    assert codes(rules.check_no_tracking_events(order)) == {"NO_TRACKING_EVENTS"}


@pytest.mark.parametrize("hours,expected", [(12, False), (61, True)])
def test_stale_tracking_threshold(hours, expected):
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", dispatched_hours_ago=70,
        carrier="DPD", tracking_number="DPD4", carrier_status="In Transit",
        tracking_update_hours_ago=hours,
    )
    assert bool(rules.check_stale_tracking(order)) is expected


def test_delivered_parcels_are_never_stale():
    order = make_order(
        marketplace_status="Delivered", warehouse_status="Dispatched", dispatched_hours_ago=200,
        carrier="Royal Mail", tracking_number="RM2", carrier_status="Delivered",
        tracking_update_hours_ago=150,
    )
    assert rules.check_stale_tracking(order) == []


def test_parcel_stuck_in_one_status_beyond_its_own_sla():
    """Out for Delivery has a tighter SLA than the generic staleness rule."""
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", dispatched_hours_ago=50,
        carrier="DPD", tracking_number="DPD5", carrier_status="Out for Delivery",
        tracking_update_hours_ago=30,
    )
    assert codes(rules.check_tracking_status_sla(order)) == {"TRACKING_STATUS_SLA_BREACH"}
    assert rules.check_stale_tracking(order) == []  # 30h < 48h staleness SLA


def test_failed_delivery_is_flagged():
    order = make_order(
        marketplace_status="Shipped", warehouse_status="Dispatched", dispatched_hours_ago=60,
        carrier="Evri", tracking_number="EV1", carrier_status="Delivery Failed",
        tracking_update_hours_ago=10,
    )
    assert codes(rules.check_delivery_failed(order)) == {"DELIVERY_FAILED"}


# ---------------------------------------------------------------------------
# Loading, validation and reconciliation
# ---------------------------------------------------------------------------
def test_loader_rejects_a_file_with_missing_columns(tmp_path):
    bad = tmp_path / "marketplace_orders.csv"
    bad.write_text("order_id,marketplace\nA1,Amazon\n", encoding="utf-8")
    with pytest.raises(DataValidationError, match="missing required column"):
        load_dataset(bad, "marketplace_orders")


def test_loader_rejects_a_missing_file(tmp_path):
    with pytest.raises(DataValidationError, match="file not found"):
        load_dataset(tmp_path / "nope.csv", "warehouse_status")


def test_reconcile_keeps_records_that_exist_on_only_one_side():
    orders = pd.DataFrame([{"order_id": "A1", "marketplace": "Amazon"}])
    warehouse = pd.DataFrame([{"order_id": "W9", "warehouse_status": "Dispatched"}])
    tracking = pd.DataFrame([{"order_id": "A1", "tracking_number": "T1"}])
    merged = reconcile(orders, warehouse, tracking)

    assert set(merged["order_id"]) == {"A1", "W9"}
    orphan = merged.loc[merged["order_id"] == "W9"].iloc[0]
    assert orphan["_in_warehouse"] and not orphan["_in_marketplace"]


def test_blank_values_are_loaded_as_nulls(project_root):
    frame, _ = load_dataset(project_root / config.TRACKING_FILE, "carrier_tracking")
    blank_number = frame.loc[frame["order_id"] == "E2010"].iloc[0]
    assert pd.isna(blank_number["tracking_number"])
    assert pd.isna(blank_number["last_tracking_update"])


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def result(request):
    root = request.config.rootpath
    return checker.run_checks(
        root / config.MARKETPLACE_FILE,
        root / config.WAREHOUSE_FILE,
        root / config.TRACKING_FILE,
    )


def test_sample_data_is_analysed_end_to_end(result):
    assert result.orders_analysed == 42  # 41 marketplace orders + 1 orphan warehouse record
    assert result.exception_count == 22


def test_sample_data_exercises_every_rule(result):
    triggered = {finding.code for finding in result.findings}
    expected = set(config.RULES) - config.DATA_QUALITY_CODES
    assert triggered == expected, f"never triggered: {expected - triggered}"


def test_every_detection_category_is_represented(result):
    counts = result.counts_by_category()
    detection = [c for c in config.CATEGORY_ORDER if c != config.CATEGORY_DATA]
    assert all(counts[category] > 0 for category in detection)


def test_the_shipped_sample_data_is_itself_clean(result):
    """The demo files should not trip the data-quality checks."""
    assert result.counts_by_category()[config.CATEGORY_DATA] == 0


def test_report_has_the_expected_shape(result):
    report = result.report
    assert list(report.columns) == config.REPORT_COLUMNS
    assert len(report) == result.exception_count
    assert report["severity"].isin(config.SEVERITY_LEVELS).all()
    assert report["escalation_owner"].isin(
        [config.OWNER_MARKETPLACE, config.OWNER_WAREHOUSE, config.OWNER_CARRIER]
    ).all()
    assert report["suggested_action"].str.len().gt(10).all()
    assert not report["suggested_action"].str.contains("{").any()  # templates rendered


def test_report_is_sorted_worst_first(result):
    rank = {level: index for index, level in enumerate(config.SEVERITY_LEVELS)}
    ranks = result.report["severity"].map(rank).tolist()
    assert ranks == sorted(ranks, reverse=True)


def test_an_order_can_raise_more_than_one_exception(result):
    per_order = result.report.groupby("order_id").size()
    assert per_order.max() > 1
    assert per_order["E2005"] == 2  # cancelled but dispatched, and still moving


def test_report_is_written_to_disk(result, tmp_path):
    path = checker.write_report(result.report, tmp_path / "report.csv")
    assert path.exists()
    assert len(pd.read_csv(path)) == result.exception_count


def test_results_are_deterministic(result, request):
    root = request.config.rootpath
    again = checker.run_checks(
        root / config.MARKETPLACE_FILE, root / config.WAREHOUSE_FILE, root / config.TRACKING_FILE
    )
    pd.testing.assert_frame_equal(result.report, again.report)


# ---------------------------------------------------------------------------
# Regression tests for issues found in review
# ---------------------------------------------------------------------------
def test_surrounding_whitespace_is_stripped_from_statuses(tmp_path):
    """pandas 2 reads str CSVs as object dtype and pandas 3 as str.

    Normalisation used to be guarded on the column dtype, so it silently stopped
    running on pandas 3 and a status of "Cancelled " matched nothing.
    """
    path = tmp_path / "marketplace_orders.csv"
    path.write_text(
        "order_id,marketplace,order_date,customer_name,sku,quantity,order_status,"
        "expected_dispatch_date\n"
        "A1,Amazon,2026-03-14 09:00:00,Buyer, SKU-1 ,1,Cancelled ,2026-03-16\n",
        encoding="utf-8",
    )
    frame, _ = load_dataset(path, "marketplace_orders")
    assert frame.loc[0, "order_status"] == "Cancelled"
    assert frame.loc[0, "sku"] == "SKU-1"


def test_rows_without_an_order_id_are_dropped(tmp_path):
    """Two keyless rows from different files must not join into a phantom order."""
    path = tmp_path / "warehouse_status.csv"
    path.write_text(
        "order_id,warehouse_status,picked_at,packed_at,dispatched_at,warehouse_location\n"
        ",Dispatched,,,2026-03-15 09:00:00,LDN-1\n"
        "   ,Picking,,,,MAN-2\n"
        "W1,Dispatched,,,2026-03-15 09:00:00,LDN-1\n",
        encoding="utf-8",
    )
    frame, issues = load_dataset(path, "warehouse_status")
    assert frame["order_id"].tolist() == ["W1"]
    # Dropping them silently was the old behaviour; each one is now reported.
    assert [issue.code for issue in issues] == ["UNIDENTIFIED_SOURCE_ROW"] * 2
    assert "Row 2" in issues[0].description and "Row 3" in issues[1].description


def test_empty_source_file_raises_a_clean_error(tmp_path):
    path = tmp_path / "carrier_tracking.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(DataValidationError, match="is empty"):
        load_dataset(path, "carrier_tracking")


def test_timezone_aware_now_is_converted_to_utc():
    """An ISO offset must not blow up against the naive timestamps in the CSVs."""
    from main import resolve_now

    assert resolve_now("2026-03-16T10:00:00+01:00") == config.REFERENCE_NOW
    assert resolve_now("2026-03-16 09:00:00") == config.REFERENCE_NOW


def test_warehouse_cancellation_the_marketplace_has_not_reflected_is_caught():
    """This state used to fall through all rules: SLAs exempt it, mismatches missed it."""
    order = make_order(
        placed_hours_ago=200, marketplace_status="Processing", warehouse_status="Cancelled",
    )
    assert codes(rules.evaluate(order)) == {"WAREHOUSE_CANCELLED_NOT_ON_MARKETPLACE"}
    assert config.RULES["WAREHOUSE_CANCELLED_NOT_ON_MARKETPLACE"].owner == config.OWNER_MARKETPLACE


def test_a_live_parcel_is_intercepted_whichever_side_cancelled():
    """Same physical situation, so the carrier must be escalated either way."""
    warehouse_side = make_order(
        placed_hours_ago=80, marketplace_status="Processing", warehouse_status="Cancelled",
        carrier="DPD", tracking_number="T1", carrier_status="Out for Delivery",
        tracking_update_hours_ago=3,
    )
    marketplace_side = make_order(
        placed_hours_ago=80, marketplace_status="Cancelled", warehouse_status="Dispatched",
        dispatched_hours_ago=60, carrier="DPD", tracking_number="T1",
        carrier_status="Out for Delivery", tracking_update_hours_ago=3,
    )
    for order in (warehouse_side, marketplace_side):
        assert "CARRIER_ACTIVE_ON_CANCELLED_ORDER" in codes(rules.evaluate(order))

    # The description names the system that actually cancelled it.
    assert "the warehouse" in rules.check_carrier_active_on_cancelled_order(
        warehouse_side)[0].description
    assert "the marketplace" in rules.check_carrier_active_on_cancelled_order(
        marketplace_side)[0].description


def test_a_matching_cancellation_on_both_sides_raises_nothing():
    order = make_order(
        placed_hours_ago=200, marketplace_status="Cancelled", warehouse_status="Cancelled",
    )
    assert rules.evaluate(order) == []


def test_feeds_are_given_a_grace_period_before_missing_records_are_flagged():
    """A record that has not arrived yet is not an exception."""
    just_placed = make_order(placed_hours_ago=0.25, warehouse_status=None)
    assert rules.evaluate(just_placed) == []

    just_dispatched = make_order(
        placed_hours_ago=20, marketplace_status="Shipped", warehouse_status="Dispatched",
        dispatched_hours_ago=0.25, carrier=None,
    )
    assert rules.evaluate(just_dispatched) == []

    # Past the grace period the same orders do get flagged.
    stale = make_order(placed_hours_ago=40, warehouse_status=None)
    assert codes(rules.evaluate(stale)) == {"MISSING_WAREHOUSE_RECORD"}


def test_marketplace_update_lag_escalates_on_its_own_threshold():
    """It used to borrow the warehouse intake SLA, coupling two unrelated rules."""
    limit = config.SLA_HOURS["marketplace_update"]
    order = make_order(
        marketplace_status="Processing", warehouse_status="Dispatched",
        placed_hours_ago=limit * 3, dispatched_hours_ago=limit * 2.5,
        carrier="DPD", tracking_number="D1", carrier_status="In Transit",
        tracking_update_hours_ago=2,
    )
    finding = rules.check_dispatch_not_reflected(order)[0]
    assert finding.severity == "High"  # 2.5x the marketplace update threshold


def test_future_dated_timestamps_are_not_swallowed_by_the_grace_periods():
    """A negative age means a broken feed, which is exactly what this tool is for.

    The grace guards were an unbounded ``age <= limit``, which is also true for
    every negative age, so bad timestamps silently vanished from the report.
    """
    future_order = make_order(placed_hours_ago=-48, warehouse_status=None)
    assert codes(rules.evaluate(future_order)) == {"MISSING_WAREHOUSE_RECORD"}

    future_dispatch = make_order(
        placed_hours_ago=20, marketplace_status="Shipped", warehouse_status="Dispatched",
        dispatched_hours_ago=-10, carrier=None,
    )
    assert codes(rules.evaluate(future_dispatch)) == {"MISSING_TRACKING_RECORD"}

    no_number = make_order(
        placed_hours_ago=20, marketplace_status="Shipped", warehouse_status="Dispatched",
        dispatched_hours_ago=-10, carrier="Evri", tracking_number=None,
    )
    assert "MISSING_TRACKING_NUMBER" in codes(rules.evaluate(no_number))


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------
def _write(path, name, rows):
    header = ",".join(config.REQUIRED_COLUMNS[name])
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_unparseable_timestamp_is_reported(tmp_path):
    path = _write(tmp_path / "m.csv", "marketplace_orders",
                  ["B1,Amazon,not-a-date,Buyer,SKU-1,1,Processing,2026-03-16"])
    frame, issues = load_dataset(path, "marketplace_orders")
    assert [i.code for i in issues] == ["UNPARSEABLE_TIMESTAMP"]
    assert issues[0].order_id == "B1"
    assert "not-a-date" in issues[0].description
    assert pd.isna(frame.loc[0, "order_date"])


def test_an_empty_timestamp_is_not_a_data_quality_problem(tmp_path):
    """A blank packed_at means packing has not happened, not a broken feed."""
    path = _write(tmp_path / "w.csv", "warehouse_status", ["B1,Picking,,,,LDN-1"])
    _, issues = load_dataset(path, "warehouse_status")
    assert issues == []


def test_unknown_status_value_is_reported(tmp_path):
    path = _write(tmp_path / "w.csv", "warehouse_status", ["B1,Dispatchd,,,,LDN-1"])
    _, issues = load_dataset(path, "warehouse_status")
    assert [i.code for i in issues] == ["UNKNOWN_STATUS_VALUE"]
    assert "Dispatchd" in issues[0].description


def test_identical_repeated_records_are_reported_as_low(tmp_path):
    """Rows that agree are a feed hygiene problem, not a risk to the result."""
    row = "B1,Amazon,2026-03-14 09:00:00,Buyer,SKU-1,1,Processing,2026-03-16"
    frame, issues = load_dataset(_write(tmp_path / "m.csv", "marketplace_orders", [row, row]),
                                 "marketplace_orders")
    assert [i.code for i in issues] == ["DUPLICATE_SOURCE_RECORD"]
    assert issues[0].extras["count"] == 2
    assert config.RULES["DUPLICATE_SOURCE_RECORD"].base_severity == "Low"
    assert len(frame) == 1


def test_records_that_disagree_raise_a_high_severity_conflict(tmp_path):
    """Last-row-wins is only defensible when the rows agree."""
    frame, issues = load_dataset(_write(tmp_path / "m.csv", "marketplace_orders", [
        "B1,Amazon,2026-03-14 09:00:00,Buyer,SKU-1,1,Processing,2026-03-16",
        "B1,Amazon,2026-03-15 09:00:00,Buyer,SKU-1,2,Shipped,2026-03-16",
    ]), "marketplace_orders")

    assert [i.code for i in issues] == ["CONFLICTING_SOURCE_RECORD"]
    assert config.RULES["CONFLICTING_SOURCE_RECORD"].base_severity == "High"
    # The operator is told exactly which fields are contested.
    contested = issues[0].extras["columns"]
    assert set(contested.split(", ")) == {"order_date", "quantity", "order_status"}
    # Something still has to be carried forward so reconciliation can run.
    assert len(frame) == 1
    assert frame.loc[0, "order_status"] == "Shipped"


def test_rows_agreeing_on_nulls_are_not_a_conflict(tmp_path):
    """Two records both missing packed_at agree about it."""
    row = "B1,Picking,,,,LDN-1"
    _, issues = load_dataset(_write(tmp_path / "w.csv", "warehouse_status", [row, row]),
                             "warehouse_status")
    assert [i.code for i in issues] == ["DUPLICATE_SOURCE_RECORD"]


def test_a_conflict_marks_the_order_and_every_finding_it_raises(tmp_path):
    """The contested field drives other rules, so the warning has to travel."""
    _write(tmp_path / "marketplace_orders.csv", "marketplace_orders", [
        "B1,eBay,2026-03-13 09:00:00,Buyer,SKU-1,1,Processing,2026-03-15",
        "B1,eBay,2026-03-13 09:00:00,Buyer,SKU-1,1,Cancelled,2026-03-15",
    ])
    _write(tmp_path / "warehouse_status.csv", "warehouse_status",
           ["B1,Dispatched,2026-03-13 12:00:00,2026-03-13 14:00:00,2026-03-13 16:00:00,LDN-1"])
    _write(tmp_path / "carrier_tracking.csv", "carrier_tracking", [])

    outcome = checker.run_checks(
        tmp_path / "marketplace_orders.csv",
        tmp_path / "warehouse_status.csv",
        tmp_path / "carrier_tracking.csv",
    )
    report = outcome.report
    # Every row for this order names the feed that cannot be trusted, including
    # the Critical one that only exists because the loader picked "Cancelled".
    assert (report["unreliable_source"] == "marketplace_orders.csv").all()
    assert "Cancelled Order Fulfilled" in set(report["exception_type"])
    assert "Conflicting Source Records" in set(report["exception_type"])


def test_orders_from_clean_feeds_are_not_marked_unreliable(result):
    assert (result.report["unreliable_source"] == "").all()


def test_data_quality_findings_are_owned_by_the_feed_that_produced_them(tmp_path):
    """Whoever produces the file owns what is wrong inside it."""
    for name, filename, row, owner in [
        ("marketplace_orders", "marketplace_orders.csv",
         "B1,Amazon,bad,Buyer,SKU-1,1,Processing,2026-03-16", config.OWNER_MARKETPLACE),
        ("warehouse_status", "warehouse_status.csv", "B1,Picking,bad,,,LDN-1",
         config.OWNER_WAREHOUSE),
        ("carrier_tracking", "carrier_tracking.csv", "B1,T1,DPD,In Transit,bad,2026-03-16",
         config.OWNER_CARRIER),
    ]:
        _write(tmp_path / filename, name, [row])
    _, _, _, issues = __import__("src.loader", fromlist=["loader"]).load_all(
        tmp_path / "marketplace_orders.csv",
        tmp_path / "warehouse_status.csv",
        tmp_path / "carrier_tracking.csv",
    )
    findings = checker.data_issues_to_findings(issues, [], config.REFERENCE_NOW)
    owners = {f.order.order_id: f.owner for f in findings}
    assert set(f.owner for f in findings) == {
        config.OWNER_MARKETPLACE, config.OWNER_WAREHOUSE, config.OWNER_CARRIER
    }
    assert owners  # every finding carried an owner override


def test_data_quality_reaches_the_report_with_a_rendered_action(tmp_path):
    for name, filename, rows in [
        ("marketplace_orders", "marketplace_orders.csv",
         ["B1,Amazon,not-a-date,Buyer,SKU-1,1,Processing,2026-03-16",
          ",Amazon,2026-03-14 09:00:00,Buyer,SKU-2,1,Processing,2026-03-16"]),
        ("warehouse_status", "warehouse_status.csv", ["B1,Picking,,,,LDN-1"]),
        ("carrier_tracking", "carrier_tracking.csv", []),
    ]:
        _write(tmp_path / filename, name, rows)

    outcome = checker.run_checks(
        tmp_path / "marketplace_orders.csv",
        tmp_path / "warehouse_status.csv",
        tmp_path / "carrier_tracking.csv",
    )
    report = outcome.report
    data_rows = report[report["exception_type"].isin(
        [config.RULES[code].label for code in config.DATA_QUALITY_CODES]
    )]
    assert len(data_rows) == 2
    assert not data_rows["suggested_action"].str.contains("{").any()
    assert "marketplace_orders.csv" in " ".join(data_rows["suggested_action"])
