"""Typed read-only Finance analytics. Decimal strings preserve exact money."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from .commands import StrictModel


class AnalyticsPeriod(StrictModel):
    date_from: date
    date_to: date
    inclusive: Literal[True] = True


class AnalyticsSource(StrictModel):
    kind: str
    id: UUID
    status: str
    description: str | None = None
    source_reference: str | None = None
    original_text: str | None = None
    counterparty_id: UUID | None = None
    counterparty_name: str | None = None
    proposal_id: UUID | None = None
    proposal_version: int | None = None
    original_id: UUID | None = None
    capture_id: UUID | None = None
    harvest_settlement_id: UUID | None = None
    evidence_id: UUID | None = None
    replacement_of_id: UUID | None = None
    day_actual_id: UUID | None = None
    day_request_id: UUID | None = None


class AnalyticsAllocation(StrictModel):
    work_area_id: UUID | None = None
    greenhouse_id: UUID | None = None
    name: str | None = None
    amount: str
    classification: Literal['EXACT', 'GENERAL_VIRTUAL', 'UNRESOLVED', 'ESTIMATED']
    provenance: str
    policy_id: str | None = None
    policy_version: int | None = None
    estimate_revision_id: UUID | None = None


class AvailableAnalyticsAccount(StrictModel):
    money_account_id: UUID
    name: str
    currency: str


class AnalyticsAccount(StrictModel):
    money_account_id: UUID
    name: str
    account_type: str
    currency: str
    active: bool
    current_balance: str
    opening_balance: str | None
    incoming: str
    outgoing: str
    net_change: str
    closing_balance: str | None
    opening_basis: Literal['ZERO_AT_CUTOVER_PLUS_PRIOR_JOURNAL', 'UNKNOWN_BEFORE_CUTOVER']


class CashTotals(StrictModel):
    incoming: str
    outgoing: str
    net_change: str


class AnalyticsMovement(StrictModel):
    posting_id: UUID
    money_account_id: UUID
    account_name: str
    currency: str
    event_date: date
    direction: Literal['in', 'out']
    amount: str
    signed_amount: str
    reversal_of_id: UUID | None = None
    source: AnalyticsSource


class DebtHistory(StrictModel):
    kind: Literal['payment', 'direct_settlement', 'advance_application']
    id: UUID
    status: str
    event_date: date
    amount: str
    currency: str
    money_account_id: UUID | None = None
    advance_id: UUID | None = None
    posting_id: UUID | None = None
    reversal_posting_id: UUID | None = None
    source: str | None = None


class DebtDocument(StrictModel):
    obligation_id: UUID
    counterparty_id: UUID
    counterparty_name: str
    direction: Literal['payable', 'receivable']
    currency: str
    original_amount: str
    allocated_amount: str
    remaining_amount: str
    status: str
    settlement_status: str
    event_date: date
    due_date: date | None = None
    overdue: bool
    obligation_class: str
    economic_source: str
    opening: bool
    opening_date: date | None = None
    opening_source: str | None = None
    source: AnalyticsSource
    attributions: list[AnalyticsAllocation]
    history: list[DebtHistory]


class DebtParty(StrictModel):
    counterparty_id: UUID
    name: str
    receivable: str
    payable: str
    document_count: int


class AnalyticsDebts(StrictModel):
    receivable: str
    payable: str
    parties: list[DebtParty]
    documents: list[DebtDocument]
    scope: Literal['CURRENT_ENTITY_CURRENCY_ALL_DATES'] = 'CURRENT_ENTITY_CURRENCY_ALL_DATES'


class EconomicEvent(StrictModel):
    posting_id: UUID
    entry_id: UUID
    event_date: date
    currency: str
    classification: Literal['REVENUE', 'OPERATING_COST']
    account_class: str
    ledger_account_id: UUID
    ledger_account_name: str
    category: Literal['UNKNOWN'] = 'UNKNOWN'
    amount: str
    selected_amount: str
    source_geography: Literal['EXACT', 'GENERAL', 'UNRESOLVED', 'PARTIAL', 'ESTIMATED']
    source: AnalyticsSource
    allocations: list[AnalyticsAllocation]


class AnalyticsEconomics(StrictModel):
    revenue: str
    operating_cost: str
    operating_result: str
    exact_cost: str
    virtual_general_cost: str
    unresolved_cost: str
    unallocated_revenue: str
    basis: Literal['CURRENT_ACTIVE_POSTED_ECONOMIC_LEDGER'] = 'CURRENT_ACTIVE_POSTED_ECONOMIC_LEDGER'
    completeness: Literal['CONFIRMED_FINANCE_ONLY', 'PARTIAL_SCOPE']


class HarvestDenominatorSource(StrictModel):
    settlement_id: UUID
    event_date: date
    evidence_id: UUID
    exact_kg: str
    estimated_kg: str
    unallocated_kg: str
    estimate_revision_id: UUID | None = None
    stale_estimate_revision_id: UUID | None = None
    source_total_kg: str
    unknown_basis_count: str


class HarvestDenominator(StrictModel):
    confirmed_kg: str | None
    exact_kg: str
    estimated_kg: str
    unallocated_kg: str
    classification: Literal['EXACT', 'ESTIMATED', 'PARTIAL', 'UNAVAILABLE']
    source_count: int
    stale_estimate_count: int
    basis_incomplete: bool
    basis: Literal['CURRENT_CONFIRMED_DEPOT_KG_SAME_PERIOD_SCOPE_ALL_CURRENCIES'] = 'CURRENT_CONFIRMED_DEPOT_KG_SAME_PERIOD_SCOPE_ALL_CURRENCIES'
    sources: list[HarvestDenominatorSource]


class AnalyticsCostPerKg(StrictModel):
    value: str | None
    known_finance_cost_per_kg: str | None
    currency: str
    classification: Literal['KNOWN_FINANCE_ONLY', 'ESTIMATED_DENOMINATOR', 'PARTIAL', 'UNAVAILABLE']
    reason: str


class InventoryCostCoverage(StrictModel):
    event_id: UUID
    product_id: UUID
    product_name: str
    kind: str
    event_date: date
    base_unit: str
    quantity: str
    uncosted_quantity: str
    known_cost: str
    currency: str | None = None
    finance_obligation_ids: list[UUID]
    recognition: Literal['FINANCE_BASIS_ONLY', 'UNRESOLVED_LINK_OR_TIMING']


class AnalyticsCoverage(StrictModel):
    complete_production_cost: Literal[False] = False
    financial_period_policy: Literal['D05_OPEN_NOT_HISTORICAL_AS_OF'] = 'D05_OPEN_NOT_HISTORICAL_AS_OF'
    inventory_cost_rows: list[InventoryCostCoverage]
    unclassified_obligation_ids: list[UUID]
    warnings: list[str]
    source_count: int
    economic_event_count: int
    movement_count: int


class AreaPolicyTarget(StrictModel):
    name: str
    greenhouse_id: UUID | None = None
    numerator: int
    denominator: Literal[70] = 70
    area_da: str
    display_percentage: str


class AnalyticsAreaPolicy(StrictModel):
    id: Literal['ADR033-AREA-2026-10-01'] = 'ADR033-AREA-2026-10-01'
    version: Literal[1] = 1
    effective_from: date = date(2026, 10, 1)
    effective_to: date | None = None
    algorithm: Literal['largest-remainder-cent-v1'] = 'largest-remainder-cent-v1'
    status: Literal['AVAILABLE', 'UNRESOLVED_REFERENCE']
    targets: list[AreaPolicyTarget]
    affects_ledger: Literal[False] = False


class AnalyticsArea(StrictModel):
    greenhouse_id: UUID
    name: str
    revenue: str
    operating_cost: str
    operating_result: str
    exact_cost: str
    virtual_general_cost: str
    estimated_revenue: str
    harvest_denominator: HarvestDenominator
    cost_per_kg: AnalyticsCostPerKg


class FinanceAnalytics(StrictModel):
    schema_version: Literal[1] = 1
    entity_id: UUID
    snapshot_at: datetime
    currency: str
    period: AnalyticsPeriod
    greenhouse_id: UUID | None = None
    money_account_id: UUID | None = None
    cutover_date: date
    finance_opening_policy: str
    cash_scope: Literal['ENTITY_CURRENCY_ACCOUNT_NO_GREENHOUSE_ALLOCATION'] = 'ENTITY_CURRENCY_ACCOUNT_NO_GREENHOUSE_ALLOCATION'
    accounts: list[AnalyticsAccount]
    available_accounts: list[AvailableAnalyticsAccount]
    cash_totals: CashTotals
    movements: list[AnalyticsMovement]
    debts: AnalyticsDebts
    economics: AnalyticsEconomics
    economic_events: list[EconomicEvent]
    areas: list[AnalyticsArea]
    harvest_denominator: HarvestDenominator
    cost_per_kg: AnalyticsCostPerKg
    allocation_policy: AnalyticsAreaPolicy
    coverage: AnalyticsCoverage
