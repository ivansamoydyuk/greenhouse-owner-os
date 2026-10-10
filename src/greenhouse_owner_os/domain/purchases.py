"""Read contracts for physical history and separately confirmed invoice links."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from .commands import StrictModel
from .owner_form_source import OwnerFormSource
from .finance_analytics import DebtDocument


class HistoryReference(StrictModel):
    id: UUID
    name: str
    base_unit: str | None = None
    currency: str | None = None


class HistoryFilters(StrictModel):
    date_from: date | None = None
    date_to: date | None = None
    supplier_id: UUID | None = None
    product_id: UUID | None = None
    location_id: UUID | None = None
    kind: Literal['receipt','application','transfer','writeoff','plan'] | None = None
    inclusive: Literal[True] = True
    date_basis: Literal['SOURCE_EVENT_DATE'] = 'SOURCE_EVENT_DATE'


class InventoryHistoryLine(StrictModel):
    line_no: int
    product_id: UUID | None = None
    product_name: str | None = None
    original_product_name: str
    quantity: str | None = None
    unit: str | None = None
    original_quantity: str | None = None
    original_unit: str | None = None
    base_quantity: str | None = None
    base_unit: str | None = None
    location_id: UUID | None = None
    location_name: str | None = None
    destination_id: UUID | None = None
    destination_name: str | None = None
    greenhouse_id: UUID | None = None
    work_area_id: UUID | None = None
    unit_cost: str | None = None
    currency: str | None = None


class InventoryHistoryMovement(StrictModel):
    seq: int
    event_id: UUID
    role: str
    event_date: date
    product_id: UUID
    product_name: str
    location_id: UUID
    location_name: str
    base_unit: str
    quantity: str
    uncosted_quantity: str
    known_cost: str
    currency: str | None = None
    reversal_of_seq: int | None = None


class InventoryOriginal(StrictModel):
    source: str
    locator: str | None = None
    source_document: str | None = None
    raw_text: str | None = None
    structured_original: OwnerFormSource | None = None
    filename: str | None = None
    mime_type: str | None = None
    has_file: bool
    original_url: str | None = None
    sha256: str | None = None
    hash_basis: Literal['BINARY_BYTES','RAW_TEXT_UTF8','CANONICAL_TYPED_FORM_JSON','UNAVAILABLE']


class InventoryHistoryEvent(StrictModel):
    id: UUID
    kind: Literal['receipt','application','transfer','writeoff','plan']
    event_date: date | None = None
    status: Literal['draft','posted','reversed']
    version: int
    created_at: datetime
    supplier_id: UUID | None = None
    supplier_name: str | None = None
    finance_obligation_id: UUID | None = None
    replacement_of_id: UUID | None = None
    replacement_id: UUID | None = None
    reversal_event_date: date | None = None
    reversal_reason: str | None = None
    lines: list[InventoryHistoryLine]
    movements: list[InventoryHistoryMovement]
    original: InventoryOriginal
    issues: list[str]
    issue_messages: list[str]


class InvoiceLink(StrictModel):
    id: UUID
    original_source_id: UUID
    proposal_id: UUID
    created_at: datetime
    finance_obligation_id: UUID | None = None
    valuation_state: Literal['UNKNOWN_UNCHANGED']
    supplier_consistency: Literal['MATCH','UNKNOWN','LEGACY_MISMATCH']
    original_url: str
    note: str | None = None


class InvoiceProposalState(StrictModel):
    id: UUID
    action_type: Literal['PAYABLE','INVOICE_MATCH']
    state: str
    version: int
    confirmed_effect_id: UUID | None = None
    current_receipt_id: UUID | None = None
    finance_obligation_id: UUID | None = None


class InvoiceWorkflow(StrictModel):
    source_id: UUID
    saved_at: datetime
    capture_context: str | None = None
    invoice_intent: Literal['operating_expense','existing_payable'] | None = None
    invoice_receipt_id: UUID | None = None
    job_state: str
    filename: str | None = None
    text: str | None = None
    has_file: bool
    original_url: str
    proposals: list[InvoiceProposalState]


class PurchaseReceipt(InventoryHistoryEvent):
    invoice_links: list[InvoiceLink]
    pending_invoice_actions: list[InvoiceWorkflow]
    linked_documents: list[DebtDocument]
    valuation_policy: Literal['UNKNOWN_UNCHANGED_ON_INVOICE_LINK'] = 'UNKNOWN_UNCHANGED_ON_INVOICE_LINK'


class HistoryStock(StrictModel):
    product_id: UUID
    product_name: str
    location_id: UUID
    location_name: str
    base_unit: str
    quantity: str
    uncosted_quantity: str
    known_cost: str
    currency: str | None = None


class HistoryPage(StrictModel):
    entity_id: UUID
    snapshot_at: datetime
    filters: HistoryFilters
    offset: int
    limit: int
    total: int
    next_offset: int | None
    available_products: list[HistoryReference]
    available_locations: list[HistoryReference]
    available_suppliers: list[HistoryReference]
    opening_state: Literal['UNINITIALIZED_VERIFY'] = 'UNINITIALIZED_VERIFY'


class PurchaseHistory(HistoryPage):
    items: list[PurchaseReceipt]
    linked_documents: list[DebtDocument]
    document_scope: Literal['CURRENT_PAGE_LINKED_WHOLE_DOCUMENTS'] = 'CURRENT_PAGE_LINKED_WHOLE_DOCUMENTS'


class InventoryHistory(HistoryPage):
    events: list[InventoryHistoryEvent]
    stock: list[HistoryStock]
    stock_scope: Literal['CURRENT_PRODUCT_LOCATION_NOT_PERIOD_OPENING'] = 'CURRENT_PRODUCT_LOCATION_NOT_PERIOD_OPENING'
