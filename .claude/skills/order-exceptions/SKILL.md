---
name: order-exceptions
description: Reconciles marketplace, warehouse and carrier feeds and triages the resulting order exceptions. Use when asked to run the daily reconciliation, check for stuck or missing orders, investigate a specific order across the three systems, or prepare an exception summary or escalation for the seller, warehouse or carrier team.
---

# Order exception triage

The Python checker in this repository decides what counts as an exception. You operate it and
communicate the result. Do not re-implement its rules, re-rank its severities or reason your way to
a different answer than the report gives. If you think a rule is wrong, say so as an observation and
leave the report as it stands.

## 1. Find the data

The operator names a directory. If they do not, ask, and offer `data/` as the default.

That directory must contain all three files:

- `marketplace_orders.csv`
- `warehouse_status.csv`
- `carrier_tracking.csv`

If any are missing, list exactly which ones and stop. Do not proceed with two of three.

## 2. Run the checker

```bash
python "${CLAUDE_PROJECT_DIR}/main.py" \
  --data-dir "<supplied-directory>" \
  --output "<supplied-directory>/exception_report.csv"
```

Use `"${CLAUDE_PROJECT_DIR}/.venv/bin/python"` instead if that file exists. If neither works because
pandas is missing, tell the operator to run `pip install -r requirements.txt` rather than installing
anything yourself.

The checker exits 1 and prints `Data error: ...` when a file is unreadable or missing a required
column. Report that message verbatim. Never edit, repair, reformat or regenerate a source CSV to get
past it. The operator fixes their feed; you do not.

By default the checker measures against a fixed reference time so the bundled demo stays
reproducible. For a real run against the current clock, add `--now system` and say that you did.

## 3. Read the report

Read the generated `exception_report.csv`. Every number you report comes from that file or from the
checker's own printed summary. Do not estimate, round or recall figures from earlier in the
conversation.

Columns: `order_id`, `marketplace`, `exception_type`, `exception_description`, `marketplace_status`,
`warehouse_status`, `carrier_status`, `age_hours`, `severity`, `escalation_owner`,
`suggested_action`. Rows are already sorted worst first.

## 4. Brief the operator

Lead with data quality if any rows have a `Data quality` exception type ("Source Row Without An
Order ID", "Duplicate Source Record", "Unreadable Timestamp", "Unrecognised Status Value"). These
mean the rest of the report may be incomplete, because an order with a broken timestamp or an
unrecognised status silently stops matching the other rules. Say that plainly before anything else.

Then give, in this order:

1. Totals: orders analysed, exceptions found, counts by category.
2. Every Critical exception, one line each: order id, what is wrong, what to do.
3. Counts by escalation owner.
4. A short list per team of the orders that need action today.

Keep it to what an operations person can act on in a few minutes. Do not repeat the whole CSV back.

When one order appears more than once, say so and keep the rows together. That is usually the most
serious thing in the report, because it means two teams both have to act on the same parcel.

## 5. Draft escalations on request

When asked for a message to a team, ground every sentence in the report. Include the order ids, the
statuses each system holds, the age in hours, and the `suggested_action` already computed. Keep it
short enough to paste into an email or a ticket.

Do not invent order history, promise a delivery date, quote a refund amount or state a cause the
report does not support. "Tracking has not updated in 61 hours" is supported. "The parcel is lost"
is not.

## 6. Boundaries

- Never send an email, open a ticket or post a message without explicit approval in that turn.
- Never modify order statuses, source CSVs or the generated report.
- Never change SLA thresholds, severities or escalation owners to make a number look better. Those
  live in `src/config.py` and changing them is a code review, not a triage decision.
- If the operator asks for something the report cannot answer, say what is missing rather than
  filling the gap with a plausible guess.
