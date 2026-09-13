# Order Exception Checker

[![tests](https://github.com/ManuelMadu/order-exception-checker/actions/workflows/tests.yml/badge.svg)](https://github.com/ManuelMadu/order-exception-checker/actions/workflows/tests.yml)

Reconciles orders across three systems that never quite agree with each other, and turns the
disagreements into a prioritised work queue with an owner attached to every line.

All customer names, order IDs, SKUs, tracking numbers and timestamps in this repository are
**synthetic**. Nothing here comes from a real seller, buyer, warehouse or carrier.

---

## In plain English

Imagine you have a toy shop, and three people tell you about every toy that gets sold.

1. The **shop person** takes the money and tells the customer "your toy is coming!"
2. The **box person** goes to the shelf, finds the toy, and puts it in a box.
3. The **van person** drives the box to the customer's house.

Most of the time all three say the same thing, and there is nothing to do.

But sometimes they disagree, and that is when something has gone wrong.

- The shop person says "we cancelled that order" but the box person already sent it. The toy is
  driving away and nobody is paying for it.
- The shop person told the customer "it's on its way" but the box person has not even found the toy
  yet. The customer is waiting for something that is not moving.
- The van person says "I delivered it three days ago" but the shop person still says "we're getting
  it ready." The customer already has their toy and is being told it is still in the shop.
- The box person put the toy in the van, and then the van went quiet. No "I'm driving", no "I'm
  here", just quiet, for three days. Nobody knows where the toy is.

**This project is the grown-up who listens to all three and spots the arguments.**

It reads what each one says, lines them up side by side, and asks 17 small questions like "does the
shop person agree with the box person?" and "has the van been quiet for too long?"

Then it writes a list. The worst problems go at the top. Next to each one it writes **who needs to
fix it** and **what they should do**, because the shop person cannot fix a quiet van, and the van
person cannot fix a toy that nobody put in a box.

That list is the whole point. Instead of one person reading three long files and trying to spot the
arguments by eye, the computer hands them a short list that says: here are the things that are
wrong, worst first, go and fix these.

The rest of this README is the same idea with the real words. The shop person is a marketplace like
Amazon, eBay or Shopify. The box person is a warehouse. The van person is a carrier like Royal Mail,
DPD or Evri.

---

## The business problem

A seller taking orders on Amazon, eBay and Shopify ends up with three separate views of the same
parcel:

1. the **marketplace**, which is what the buyer sees,
2. the **warehouse**, which is what physically happened,
3. the **carrier**, which is where the parcel actually is.

These three drift apart constantly. A warehouse dispatches an order that was cancelled an hour
earlier. A marketplace still says "Processing" two days after the parcel was delivered. A parcel is
marked "In Transit" and then never scanned again.

Each of those is a customer complaint, an SLA breach or a refund waiting to happen, and each one
belongs to a different team. Spotting them by eye across three spreadsheets does not scale.

This tool does that reconciliation automatically: it joins the three feeds, applies a set of
business rules, and writes one CSV of everything that needs a human, sorted worst first, with the
team that should investigate it and a suggested next action.

---

## Approach

```
data/marketplace_orders.csv ─┐
data/warehouse_status.csv   ─┼─► load + validate ─► outer join on order_id ─► OrderView per order
data/carrier_tracking.csv   ─┘                                                      │
                                                                                     ▼
                                                        17 independent rules (src/rules.py)
                                                                                     │
                                                                                     ▼
                                             output/exception_report.csv (severity, owner, action)
```

Four design decisions worth calling out:

**The join is an outer join.** Records that exist on only one side are not a data problem to be
dropped, they are the "missing order" exceptions the tool is looking for.

**Rules are independent functions.** Each rule takes one reconciled order and answers one question.
That means an order can raise more than one exception, which is the honest outcome: an order that is
both cancelled and already in the carrier network really does need two different teams to act.

**Nothing operational is hard-coded in the rules.** SLA thresholds, severities, escalation owners and
suggested-action templates all live in `src/config.py`. Changing a dispatch SLA from 48 to 24 hours
is a one-line edit, not a code change.

**Ages are always computed from timestamps.** No dataset stores an age. Everything is measured
against a reference "now", which is fixed in config so the demo results stay reproducible.

### The fixed reference time

The sample data is dated relative to `REFERENCE_NOW = 2026-03-16 09:00:00` (naive UTC) in
`src/config.py`. Without that, a demo dataset ages: after a month every order in it would breach
every SLA and the example would stop being meaningful. Run `python main.py --now system` to check
against the real clock instead.

---

## Input datasets

### `data/marketplace_orders.csv` (40 orders)

| Column | Notes |
|---|---|
| `order_id` | Join key |
| `marketplace` | Amazon, eBay, Shopify |
| `order_date` | When the buyer placed the order |
| `customer_name` | Synthetic |
| `sku`, `quantity` | Item detail |
| `order_status` | Pending, Processing, Shipped, Delivered, Cancelled |
| `expected_dispatch_date` | The promise made to the buyer |

### `data/warehouse_status.csv` (39 records)

| Column | Notes |
|---|---|
| `order_id` | Join key |
| `warehouse_status` | Received, Picking, Picked, Packed, Dispatched, Cancelled |
| `picked_at`, `packed_at`, `dispatched_at` | Null until that step completes |
| `warehouse_location` | LDN-1, MAN-2, BHM-3 |

### `data/carrier_tracking.csv` (20 records)

| Column | Notes |
|---|---|
| `order_id` | Join key |
| `tracking_number` | Can be blank, which is itself an exception |
| `carrier` | Royal Mail, DPD, Evri |
| `tracking_status` | Label Created, Collected, In Transit, Out for Delivery, Delivered, Delivery Failed |
| `last_tracking_update` | Last scan event |
| `estimated_delivery_date` | Carrier promise |

The three files do not line up on purpose. Only dispatched orders have tracking rows, two
marketplace orders have no warehouse record, and one warehouse record (`W9001`) has no marketplace
order behind it.

---

## Exception rules

17 rules in four categories. Every one of them fires at least once against the bundled data, which
is asserted by a test.

### Missing orders

| Rule | Fires when | Severity | Owner |
|---|---|---|---|
| Missing Warehouse Record | Marketplace order exists, warehouse has no record of it | High | Warehouse |
| Orphan Warehouse Dispatch | Warehouse dispatched an order the marketplace never sent | Critical | Seller / Marketplace |
| Dispatched Without Tracking Record | Warehouse dispatched it, carrier feed has no row at all | High | Carrier |

### Status mismatches

| Rule | Fires when | Severity | Owner |
|---|---|---|---|
| Cancelled Order Fulfilled | Marketplace says Cancelled, warehouse says Packed or Dispatched | Critical | Warehouse |
| Carrier Moving Cancelled Order | Order is cancelled but the parcel is live in the carrier network | Critical | Carrier |
| Marketplace Shipped Before Dispatch | Marketplace says Shipped or Delivered, warehouse has not dispatched | High | Seller / Marketplace |
| Dispatch Not Reflected On Marketplace | Warehouse dispatched, marketplace still Pending or Processing | Medium | Seller / Marketplace |
| Delivered But Marketplace Not Updated | Carrier delivered, marketplace still Pending or Processing | High | Seller / Marketplace |

The last two overlap by definition, so the delivered rule suppresses the dispatch rule. One order,
one message to the seller.

### SLA breaches

Thresholds come from `SLA_HOURS` in `src/config.py`.

| Rule | Threshold | Severity | Owner |
|---|---|---|---|
| Warehouse Intake SLA Breach | Still "Received" more than 24h after the order | Medium | Warehouse |
| Dispatch SLA Breach | Not dispatched within 48h of the order | High | Warehouse |
| Stuck In Picking | In "Picking" more than 36h after the order | Medium | Warehouse |
| Stuck In Packed | Packed more than 12h ago and still not collected | Medium | Warehouse |

Cancelled orders are exempt from all fulfilment SLAs.

### Missing or stale parcel tracking

| Rule | Threshold | Severity | Owner |
|---|---|---|---|
| Missing Tracking Number | Dispatched, tracking row exists, tracking number blank | High | Carrier |
| No Tracking Events | Tracking number issued but never scanned | High | Carrier |
| Stale Tracking | No tracking update for more than 48h, parcel not delivered | High | Carrier |
| Parcel Stuck In Tracking Status | Dwell beyond that status's own limit | Medium | Carrier |
| Delivery Failed | Carrier reported a failed delivery attempt | High | Carrier |

Per-status dwell limits (`TRACKING_STATUS_SLA_HOURS`): Label Created 24h, Collected 48h, In Transit
72h, Out for Delivery 24h. "Out for Delivery" is tighter than the generic staleness rule on purpose,
because a parcel that has been out for delivery for 30 hours is a problem even though 30 hours is
inside the 48 hour staleness window.

### Severity

Base severity comes from the rule. Time-based breaches then escalate on how badly the SLA was
overshot: 2x the limit adds one level, 3x adds two. So the same rule reports Medium at 26 hours over
and Critical at 66 hours over, without a second rule being written.

---

## How escalation ownership is determined

Ownership is a fixed property of each rule in `src/config.py`, and it follows one question: **who is
the only team that can actually fix this?**

- **Warehouse** owns anything where the physical operation is the problem or the source of truth.
  An order the warehouse never received, a cancelled order it shipped anyway, a pick that has not
  started, a pallet that missed collection.
- **Seller / Marketplace** owns anything where the physical operation is correct and the buyer-facing
  record is wrong. Dispatched but not marked shipped, delivered but still Processing, marked shipped
  before it actually was, and stock dispatched against an order the marketplace feed never sent.
- **Carrier** owns anything that happens after the parcel leaves the building. No consignment raised,
  no tracking number, no scans, no movement, failed delivery, or a cancelled parcel that needs
  intercepting.

Because ownership is attached to the rule rather than to the order, a single order can escalate to
two teams at once. `E2005` does exactly that below.

---

## Short demo

### Order A1007, a carrier exception

| System | Says |
|---|---|
| Marketplace (Amazon) | `Shipped` |
| Warehouse (LDN-1) | `Dispatched`, 76 hours ago |
| Carrier (DPD) | `In Transit`, last scan 61 hours ago |

Nothing here is internally wrong. The warehouse did its job and the marketplace told the buyer the
truth. The parcel has simply gone quiet in the DPD network for longer than the 48 hour tracking SLA.

> **Exception:** Stale Tracking
> **Severity:** High
> **Escalation:** Carrier
> **Suggested action:** Contact the carrier to investigate the lack of tracking updates on DPD9930118841.

### Order E2009, a warehouse exception

| System | Says |
|---|---|
| Marketplace (eBay) | `Processing`, placed 76 hours ago |
| Warehouse (LDN-1) | `Packed` 66 hours ago, never dispatched |
| Carrier | No record |

This raises two exceptions, because two different things have gone wrong at two different scales.

> **Exception:** Dispatch SLA Breach (76h against a 48h SLA)
> **Severity:** High
> **Escalation:** Warehouse
> **Suggested action:** Investigate warehouse fulfilment delay and give the buyer a revised dispatch date.

> **Exception:** Stuck In Packed (66h against a 12h SLA, 5x over, so escalated to Critical)
> **Severity:** Critical
> **Escalation:** Warehouse
> **Suggested action:** Check whether the carrier collection at LDN-1 was missed.

### Order E2005, one order, two teams

| System | Says |
|---|---|
| Marketplace (eBay) | `Cancelled` |
| Warehouse (MAN-2) | `Dispatched` 66 hours ago |
| Carrier (DPD) | `In Transit`, last scan 10 hours ago |

The order was cancelled and shipped anyway, and the parcel is still moving. Two teams have to act,
so two exceptions are raised.

> **Exception:** Cancelled Order Fulfilled, Critical, **Warehouse**
> Confirm cancellation before shipment continues and recover the parcel if it has already left.

> **Exception:** Carrier Moving Cancelled Order, Critical, **Carrier**
> Request a carrier intercept or return to sender for DPD9930121007; the order is cancelled.

---

## Project structure

```
order-exception-checker/
├── .github/workflows/
│   └── tests.yml                  # CI: pytest on 3.10-3.13 + reproducibility check
├── data/
│   ├── marketplace_orders.csv     # 40 synthetic orders across 3 marketplaces
│   ├── warehouse_status.csv       # 39 fulfilment records (+1 orphan, -2 missing)
│   └── carrier_tracking.csv       # 20 tracking records
├── output/
│   └── exception_report.csv       # committed worked example
├── scripts/
│   └── generate_sample_data.py    # regenerates data/ deterministically
├── src/
│   ├── config.py                  # SLAs, severities, owners, action templates
│   ├── loader.py                  # load, validate, reconcile
│   ├── models.py                  # OrderView and Finding
│   ├── rules.py                   # the 17 detection rules
│   └── checker.py                 # pipeline and report builder
├── tests/
│   ├── conftest.py                # order builder used by the rule tests
│   └── test_checker.py            # 34 tests
├── main.py                        # CLI
├── requirements.txt
├── pytest.ini
└── README.md
```

`src/` is split further than a single `checker.py` for one reason: it keeps the business rules in a
file that an operations person can read without meeting any pandas.

---

## Installation

Requires Python 3.10 or later.

```bash
git clone https://github.com/ManuelMadu/order-exception-checker.git
cd order-exception-checker

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

---

## Running the checker

```bash
python main.py
```

Options:

```bash
python main.py --now "2026-03-20 09:00"   # analyse against a different reference time
python main.py --now system               # use the real current time
python main.py --data-dir data --output output/exception_report.csv
python main.py --quiet                    # summary only, no preview table
```

To regenerate the synthetic data (produces byte-identical files every time):

```bash
python scripts/generate_sample_data.py
```

---

## Example terminal output

```text
Reference time: 2026-03-16 09:00 (UTC)
41 orders analysed
21 exceptions detected

  Missing orders:      4
  Status mismatches:   5
  SLA breaches:        5
  Tracking exceptions: 7

  By severity
    Medium:      5
    High:        10
    Critical:    6

  By escalation owner
    Carrier:               9
    Warehouse:             8
    Seller / Marketplace:  4

Top 8 exceptions by severity:
order_id marketplace                 exception_type severity     escalation_owner  age_hours
   S3010     Shopify                 Stale Tracking Critical              Carrier      100.0
   S3011     Shopify       Missing Warehouse Record Critical            Warehouse       70.0
   E2005        eBay      Cancelled Order Fulfilled Critical            Warehouse       66.0
   E2009        eBay                Stuck In Packed Critical            Warehouse       66.0
   W9001     UNKNOWN      Orphan Warehouse Dispatch Critical Seller / Marketplace       22.0
   E2005        eBay Carrier Moving Cancelled Order Critical              Carrier       10.0
   E2009        eBay            Dispatch SLA Breach     High            Warehouse       76.0
   A1007      Amazon                 Stale Tracking     High              Carrier       61.0

Report written to output/exception_report.csv
```

41 orders analysed against 40 marketplace orders is not a bug. The extra one is `W9001`, the
warehouse record with no marketplace order behind it, and finding it is the point.

---

## Example exception report

First six rows of `output/exception_report.csv`:

| order_id | marketplace | exception_type | marketplace_status | warehouse_status | carrier_status | age_hours | severity | escalation_owner |
|---|---|---|---|---|---|---|---|---|
| S3010 | Shopify | Stale Tracking | Shipped | Dispatched | In Transit | 100.0 | Critical | Carrier |
| S3011 | Shopify | Missing Warehouse Record | Processing | MISSING | MISSING | 70.0 | Critical | Warehouse |
| E2005 | eBay | Cancelled Order Fulfilled | Cancelled | Dispatched | In Transit | 66.0 | Critical | Warehouse |
| E2009 | eBay | Stuck In Packed | Processing | Packed | MISSING | 66.0 | Critical | Warehouse |
| W9001 | UNKNOWN | Orphan Warehouse Dispatch | MISSING | Dispatched | In Transit | 22.0 | Critical | Seller / Marketplace |
| E2005 | eBay | Carrier Moving Cancelled Order | Cancelled | Dispatched | In Transit | 10.0 | Critical | Carrier |

Two columns are dropped from the table above for width. The full file also carries
`exception_description` (what went wrong, with the actual hours and the SLA it broke) and
`suggested_action` (what to do about it), for example:

```text
S3010  No tracking update for 100h while status is In Transit (SLA 48h).
       -> Contact the carrier to investigate the lack of tracking updates on EV7714221145.

S3011  Order placed 70h ago on Shopify has no warehouse record.
       -> Contact warehouse to confirm whether order S3011 was received.

E2005  Marketplace shows Cancelled but warehouse shows Dispatched.
       -> Confirm cancellation before shipment continues and recover the parcel if it has already left.
```

---

## Testing

```bash
pytest
```

Tests run in CI on every push and pull request, against Python 3.10, 3.11, 3.12 and 3.13.
A second CI job regenerates the synthetic data and the exception report and fails the build if
either differs from what is committed, so the reproducibility claim above stays honest.

34 tests covering:

- every exception rule, positive and negative case
- the suppression rule between the two marketplace-update exceptions
- severity escalation on SLA overshoot
- cancelled orders being exempt from fulfilment SLAs
- column validation and missing-file handling
- outer-join behaviour for one-sided records
- null and blank handling in the source CSVs
- the end-to-end run: 41 orders, 21 exceptions, all 17 rules triggered, report shape, sort order,
  and that two consecutive runs produce an identical report

---

## Assumptions and limitations

**Assumptions**

- `order_id` is a reliable join key across all three systems, and unique within each file. Where a
  file contains duplicates, the last row wins, on the basis that source systems append corrections.
- All timestamps are naive UTC. Real marketplace feeds are rarely this tidy.
- `picked_at`, `packed_at` and `dispatched_at` record when each step *completed*, so an order in
  "Picking" has a null `picked_at`. Picking dwell is therefore measured from the order date.
- Cancelled orders are exempt from fulfilment SLAs, but a cancelled order that was shipped anyway is
  a Critical exception rather than an exemption.
- A delivered parcel is never "stale", however long ago the last scan was.
- One SLA set applies to every marketplace, carrier and warehouse.

**Limitations**

- The reconciliation is order-level, not line-level. An order with three SKUs shipped in two parcels
  is not modelled.
- There is no state between runs, so the report cannot tell a new exception from one that has been
  open for a week, and nothing is ever marked as resolved.
- SLAs are wall-clock hours. Weekends, bank holidays and carrier cut-off times are ignored, so a
  Friday evening order will look late on Monday morning.
- Severity escalation uses one ratio scale for every rule. Order value, buyer rating risk and
  marketplace-specific penalties are not considered.
- Everything is in memory. It will handle tens of thousands of orders comfortably and is the wrong
  shape for millions.

---

## Possible next steps

- **Persist exception state** so the report can show first-seen, age-of-exception and resolution,
  and so the same open problem stops reappearing as though it were new.
- **Working-hours SLA clock**, with carrier cut-off times and non-working days per warehouse.
- **Per-marketplace and per-carrier SLA overrides**, since Amazon's dispatch expectations and a
  small Shopify store's are not the same number.
- **Push the output where the work happens**: a Slack or email digest per escalation owner, or
  tickets raised straight into a helpdesk, rather than a CSV someone has to remember to open.
- **Value-weighted severity**, so a stale £400 parcel outranks a stale £6 one.
- **A small dashboard** showing exception volume by owner and rule over time, which turns the tool
  from a daily checklist into a way of proving which of the three systems is actually the problem.
- **Real connectors** to marketplace, WMS and carrier APIs to replace the CSV drop.
