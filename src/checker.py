"""Orchestration: load -> reconcile -> apply rules -> build the exception report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from . import config, loader, rules
from .models import DataIssue, Finding, OrderView


@dataclass
class CheckResult:
    """Everything a caller needs after a run."""

    orders_analysed: int
    findings: List[Finding]
    report: pd.DataFrame
    reference_now: datetime

    @property
    def exception_count(self) -> int:
        return len(self.findings)

    def counts_by_category(self) -> dict:
        """Exception counts per category, in the configured display order."""
        counts = {category: 0 for category in config.CATEGORY_ORDER}
        for finding in self.findings:
            counts[config.RULES[finding.code].category] += 1
        return counts

    def counts_by_severity(self) -> dict:
        counts = {level: 0 for level in config.SEVERITY_LEVELS}
        for finding in self.findings:
            counts[finding.severity or config.RULES[finding.code].base_severity] += 1
        return counts

    def counts_by_owner(self) -> dict:
        counts: dict = {}
        for finding in self.findings:
            owner = finding.owner or config.RULES[finding.code].owner
            counts[owner] = counts.get(owner, 0) + 1
        return counts


def build_order_views(merged: pd.DataFrame, now: datetime) -> List[OrderView]:
    """Turn the reconciled dataframe into one :class:`OrderView` per order."""
    return [OrderView.from_row(row, now) for _, row in merged.iterrows()]


def mark_unreliable_orders(orders: List[OrderView], issues: List[DataIssue]) -> None:
    """Flag orders whose source feeds contradict themselves.

    Done before the rules run, so every finding raised against one of these
    orders carries the warning with it rather than reading as confirmed fact.
    """
    by_id = {order.order_id: order for order in orders}
    for issue in issues:
        if issue.code != "CONFLICTING_SOURCE_RECORD" or not issue.order_id:
            continue
        order = by_id.get(issue.order_id)
        if order is not None and issue.extras["source_file"] not in order.unreliable_sources:
            order.unreliable_sources.append(issue.extras["source_file"])


def detect_exceptions(orders: List[OrderView]) -> List[Finding]:
    """Apply every rule to every order."""
    findings: List[Finding] = []
    for order in orders:
        findings.extend(rules.evaluate(order))
    return findings


def _suggested_action(finding: Finding) -> str:
    """Render the configured action template with this order's context."""
    template = config.RULES[finding.code].action
    return template.format(**{**finding.order.context(), **finding.extras})


def data_issues_to_findings(
    issues: List[DataIssue], orders: List[OrderView], now: datetime
) -> List[Finding]:
    """Turn load-time file problems into findings the report can carry.

    Each is attached to its real order where one survived the load, so the
    report still shows that order's statuses next to the problem.
    """
    by_id = {order.order_id: order for order in orders}
    findings = []
    for issue in issues:
        order = by_id.get(issue.order_id) if issue.order_id else None
        if order is None:
            order = OrderView(order_id=issue.order_id or "UNKNOWN", now=now)
        findings.append(Finding(
            order=order,
            code=issue.code,
            description=issue.description,
            owner=config.FEED_OWNER[issue.source_file],
            extras=issue.extras,
        ))
    return findings


def build_report(findings: List[Finding]) -> pd.DataFrame:
    """Flatten findings into the exception report, worst first."""
    rows = []
    for finding in findings:
        spec = config.RULES[finding.code]
        order = finding.order
        rows.append(
            {
                "order_id": order.order_id,
                "marketplace": order.marketplace or "UNKNOWN",
                "exception_type": spec.label,
                "exception_description": finding.description,
                "marketplace_status": order.marketplace_status or "MISSING",
                "warehouse_status": order.warehouse_status or "MISSING",
                "carrier_status": order.carrier_status or "MISSING",
                "age_hours": finding.age_hours,
                "severity": finding.severity or spec.base_severity,
                "escalation_owner": finding.owner or spec.owner,
                "suggested_action": _suggested_action(finding),
                "unreliable_source": ", ".join(order.unreliable_sources),
            }
        )

    report = pd.DataFrame(rows, columns=config.REPORT_COLUMNS)
    if report.empty:
        return report

    # Most severe first, then oldest first, so the top of the file is the work queue.
    severity_rank = {level: index for index, level in enumerate(config.SEVERITY_LEVELS)}
    report["_severity_rank"] = report["severity"].map(severity_rank)
    report = report.sort_values(
        by=["_severity_rank", "age_hours", "order_id"],
        ascending=[False, False, True],
        na_position="last",
    )
    return report.drop(columns="_severity_rank").reset_index(drop=True)


def run_checks(
    marketplace_path: str | Path = config.MARKETPLACE_FILE,
    warehouse_path: str | Path = config.WAREHOUSE_FILE,
    tracking_path: str | Path = config.TRACKING_FILE,
    now: Optional[datetime] = None,
) -> CheckResult:
    """Full pipeline: load the three CSVs and produce the exception report."""
    now = now or config.REFERENCE_NOW
    orders_df, warehouse_df, tracking_df, issues = loader.load_all(
        marketplace_path, warehouse_path, tracking_path
    )
    merged = loader.reconcile(orders_df, warehouse_df, tracking_df)

    order_views = build_order_views(merged, now)
    mark_unreliable_orders(order_views, issues)
    findings = detect_exceptions(order_views)
    findings.extend(data_issues_to_findings(issues, order_views, now))

    return CheckResult(
        orders_analysed=len(order_views),
        findings=findings,
        report=build_report(findings),
        reference_now=now,
    )


def write_report(report: pd.DataFrame, path: str | Path = config.REPORT_FILE) -> Path:
    """Write the exception report CSV, creating the output directory if needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report.to_csv(path, index=False)
    return path
