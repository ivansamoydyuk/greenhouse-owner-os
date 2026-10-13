"""Source-backed greenhouse cockpit. A projection, never another ledger."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import Field
from .commands import StrictModel
from .finance_analytics import AnalyticsCostPerKg, EconomicEvent, HarvestDenominator


class DailyFilters(StrictModel):
    date_from: date
    date_to: date
    inclusive: Literal[True] = True
    currency: str
    greenhouse_id: UUID | None = None
    allocation: Literal['all', 'exact', 'estimated'] = 'all'
    season_start_year: int
    season_start: date
    season_end: date
    season_end_exclusive: Literal[True] = True
    basis: Literal['CURRENT_ACTIVE_NOT_HISTORICAL_AS_OF'] = 'CURRENT_ACTIVE_NOT_HISTORICAL_AS_OF'


class DailyArea(StrictModel):
    area_da: str | None
    status: Literal['VERIFIED_FULL_PERIOD', 'UNKNOWN', 'CHANGED_IN_PERIOD']
    reference_ids: list[UUID]
    missing_work_area_ids: list[UUID]
    validity_from: date | None = None
    validity_to: date | None = None
    basis: Literal['OWNER_VERIFIED_REFERENCE_VERSIONS'] = 'OWNER_VERIFIED_REFERENCE_VERSIONS'


class DailyMetric(StrictModel):
    value: str | None
    exact_value: str | None = None
    estimated_value: str | None = None
    unallocated_value: str | None = None
    source_ids: list[UUID] = Field(default_factory=list)
    source_count: int = 0
    missing_count: int = 0
    classification: str


class DailyQuality(StrictModel):
    quality: Literal['HIGHEST', 'FIRST', 'SECOND', 'UNKNOWN']
    net_weight_kg: str
    share_percent: str | None
    priced_kg: str
    amount: str
    weighted_price_per_kg: str | None
    line_ids: list[UUID]


class DailyHarvest(StrictModel):
    branches: DailyMetric
    depot_kg: DailyMetric
    denominator: HarvestDenominator
    weighted_price_per_kg: str | None
    priced_kg: str
    priced_amount: str
    quality_rows: list[DailyQuality]
    events: list[dict]
    field_events: list[dict]
    months: list[dict]
    warnings: list[str]


class DailyFinance(StrictModel):
    revenue: str
    operating_cost: str
    operating_result: str
    exact_cost: str
    virtual_general_cost: str
    estimated_revenue: str
    cost_per_kg: AnalyticsCostPerKg
    economic_events: list[EconomicEvent]
    source_count: int
    state: Literal['CONFIRMED_OPERATIONS', 'NO_CONFIRMED_OPERATIONS']
    per_da: dict[str, str | None]
    basis: Literal['CURRENT_ACTIVE_POSTED_ECONOMIC_LEDGER'] = 'CURRENT_ACTIVE_POSTED_ECONOMIC_LEDGER'
    complete_production_cost: Literal[False] = False


class DailyWork(StrictModel):
    tasks: list[dict]
    attention: list[dict]
    applications: list[dict]
    upcoming_applications: list[dict]
    day_workers: dict
    current_status_not_historical: Literal[True] = True


class DailyComparison(StrictModel):
    status: Literal['AVAILABLE', 'NO_PRIOR_SOURCES', 'UNAVAILABLE']
    reason: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    current_date_from: date | None = None
    current_date_to: date | None = None
    previous: dict | None = None
    current: dict | None = None
    basis: Literal['SAME_ELAPSED_SEASON_DAYS_CURRENT_ACTIVE_SOURCES'] = 'SAME_ELAPSED_SEASON_DAYS_CURRENT_ACTIVE_SOURCES'


class DailyGreenhouse(StrictModel):
    id: UUID
    name: str
    area: DailyArea
    finance: DailyFinance
    harvest: DailyHarvest
    work: DailyWork
    comparison: DailyComparison
    links: dict[str, str]


class GreenhouseDaily(StrictModel):
    schema_version: Literal[1] = 1
    entity_id: UUID
    snapshot_at: datetime
    filters: DailyFilters
    greenhouses: list[DailyGreenhouse]
    selected: DailyGreenhouse | None = None
    coverage: dict
    warnings: list[str]
