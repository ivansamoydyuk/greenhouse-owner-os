# Greenhouse Daily Control read contract

GET `/v1/entities/{entity_id}/greenhouse-daily` is owner-authenticated and read-only. Query: inclusive `date_from`, `date_to`, `currency` (default TRY), optional `greenhouse_id`, `allocation=all|exact|estimated` (Harvest only), `compare_previous=true|false`. The period is bounded to at most 366 days between endpoints, and all sections retain these filters. `greenhouses` contains root greenhouse profiles; `selected` is the requested profile or null. Missing data is not a server error or an invented zero.

## Source authorities

One owned PostgreSQL repeatable-read, read-only transaction composes the existing FinanceAnalyticsService, HarvestJourneyService, WorkersService, FertilizerService and DayWorkersService. The internal Finance snapshot option verifies the current isolation and read-only mode; public routes cannot downgrade it. No projection writes to a ledger, original, attribution, estimate or status. Finance current-active economics, all-date debt and event-period payments remain different measures. It is not a historical balance as of the chosen end date, and D05 remains unresolved.

## Profiles

- `area`: full-period verified owner reference versions. A root's explicit area is used, or all its direct parts must have stable verified area for the entire period. Missing/changed area produces null per-da measures. ADR033 allocation weights do not establish historical physical area.
- `finance`: existing confirmed revenue, operating cost, result, exact cost, virtual GENERAL, estimated revenue and known-finance-only cost/kg. Economic events retain all authoritative allocation/source IDs. There are no greenhouse bank balances. Inventory valuation is coverage and is never added as another expense. `source_count` and `state` distinguish no confirmed economic operations.
- `harvest`: field branches remain separate from depot kg. Confirmed exact allocations and current non-stale estimates are read as saved. Original receipt lines and unknown residuals remain visible. Qualities preserve original label, immutable stored classification, owner receipt-review proof and the confirmation's depot-specific mapping-version context. Legacy classifications without matching review evidence are UNKNOWN for the reviewed quality mix; no latest mapping relabels history. Unknown quality weight remains in shares. Weighted price is selected-currency amount divided by exactly matching selected-currency kg. Months span the selected period, including an October boundary. Stale-estimate-only or unknown-origin source evidence remains visible with null monthly kg/revenue and measured missing-source coverage; it never becomes a zero bar.
- `work`: source-backed tasks/current attention, fertilizer applications and current DayWorkers summary. Current task statuses are explicitly not historical statuses. Open overdue/undated tasks remain visible. Dated applications are selected by the shared period; only actual pending fertilizer states are listed as upcoming. DayWorkers requested/approved plan, reported fact, owner-confirmed fact and accrual remain distinct.

## DayWorkers money

Accrual is the existing Finance cost, selected by its explicit greenhouse attribution. It is not added again to the greenhouse operating cost. Payments are separate current posted allocation histories in the chosen event-date period. Current debt covers linked obligations across all dates.

For a payable spanning multiple greenhouses, Finance's payment allocation does not say which greenhouse's cost portion was paid. An entirely unpaid mixed payable has known debt equal to its exact accrued greenhouse attribution; a fully settled payable has known zero debt. Partial settlement leaves the greenhouse debt unknown. Payments and noncash settlement coverage are evaluated separately for the selected period; unresolved measures are null with explicit coverage and linked documents. The whole payable or its payment is never copied into each greenhouse. Original full documents remain drilldown evidence, not greenhouse totals.

## Comparison and drilldowns

Seasons start October 1 and end exclusively the following October 1. Previous comparison uses equal offsets and equal elapsed day counts, clipped to today and to the common number of season days in leap-season cases. Crossing a season disables comparison, not the main selected-period totals. No real prior sources means `NO_PRIOR_SOURCES` and null previous values, never fabricated baseline bars. Each comparison supplies both actual compared date windows. These remain current-active source readings, not historical closing snapshots.

Profiles include API drilldowns with entity/greenhouse/period/currency/allocation context. Economic sources include original/proposal/evidence and day-actual/request IDs as available. Farm-level unresolved finance, depot origin and field-report coverage is retained separately and is not charged to every greenhouse.
