"""Non-DB, real-OCR benchmark over the frozen Capture Daily Completion corpus.

Run once per independently verified source root in a fresh process. The runner
imports application code only after selecting that root. It exercises the real
LocalOCR and real OwnerCaptureService.prepare with an explicit, read-only
reference facade (not a database substitute or posting test). All other service
calls, DB/authority/retry effects and browser interaction metrics are NOT RUN.
No labels, fixtures, application source, database or CI state are changed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
import traceback
from types import SimpleNamespace
from uuid import UUID, uuid5


NAMESPACE = UUID("4715f2ba-32da-4e5c-bcc4-fa7d3c50e495")
DAY = date(2026, 10, 12)
MONEY_TYPES = {"PAYMENT", "PAYABLE", "DIRECT_EXPENSE"}
ASSESSMENT_VERSION = "cdc-fields-v2-nested-typed-money-candidates"
NOT_RUN = [
    "PostgreSQL effect/authority/transaction rollback and source/job/retry identity",
    "Participant encryption, logout/switch, receipt recovery and persistence",
    "Actual UI prefill visibility, trusted clicks, mandatory inputs and repeated facts",
    "Physical iPhone camera/dictation/offline OS behavior",
]


def uid(label):
    return str(uuid5(NAMESPACE, label))


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def application_hashes(root):
    return {str(p.relative_to(root)): sha(p) for p in sorted((root / "src").rglob("*.py"))}


def references():
    """Fixed TEST references; no observed OCR output influences their content."""
    return {
        "counterparties": [
            {"id": uid("serkan"), "name": "Серкан", "counterparty_type": "supplier", "active": True},
            {"id": uid("depot"), "name": "GÖRGÜN MUZ SARARTMA TESİSİ", "counterparty_type": "depot", "active": True},
            {"id": uid("supplier"), "name": "SYN Gübre Tedarik", "counterparty_type": "supplier", "active": True},
            {"id": uid("other_supplier"), "name": "SYN Another Supplier", "counterparty_type": "supplier", "active": True},
        ],
        "accounts": [
            {"id": uid("cash_try"), "name": "Cash TR", "account_type": "cash", "currency": "TRY", "active": True},
            {"id": uid("bank_try"), "name": "Bank TR", "account_type": "bank", "currency": "TRY", "active": True},
            {"id": uid("cash_usd"), "name": "Cash USD", "account_type": "cash", "currency": "USD", "active": True},
        ],
        "products": [{"id": uid("kno3"), "name": "KNO3", "unit": "kg", "base_unit": "kg", "active": True, "aliases": []}],
        "areas": [], "locations": [], "workers": [],
    }


class ReferenceScalars:
    """The production preparation method is read-only; supply its references."""
    def __init__(self, refs):
        self.refs = refs
        self.calls = []

    def scalars(self, statement):
        model = statement.column_descriptions[0]["entity"]
        table = model.__tablename__
        self.calls.append(table)
        names = {"counterparties": "counterparties", "work_areas": "areas", "money_accounts": "accounts"}
        if table not in names:
            raise AssertionError(f"Unapproved preparation reference read: {table}")
        return self.refs[names[table]]


def prepare_runtime(module, refs, row, raw, recognition):
    """Call the real method with no SQL execution, using the same API on both refs."""
    sql_reads = []
    def reference_rows(_uow, sql, **params):
        sql_reads.append({"sql": sql, "params": params})
        flat = " ".join(sql.split())
        for table, key in (("inventory.products", "products"), ("inventory.locations", "locations"), ("workers.people", "workers")):
            if flat == f"SELECT * FROM {table} WHERE entity_id=:e":
                return refs[key]
        if flat == "SELECT * FROM harvest.depot_quality_mappings WHERE depot_id=:d AND original_label=:l ORDER BY version DESC LIMIT 1":
            return []  # No accepted learned mapping seeded by this benchmark.
        raise AssertionError(f"No DB access allowed; new query requires explicit harness review: {flat}")
    context = "quick_expense" if row["id"].startswith("T") else "depot_receipt" if row["id"].startswith("H") else None
    invoice_intent = None
    if row["id"] == "P02":
        context, invoice_intent = "supplier_invoice", "operating_expense"
    elif row["id"] == "P05":
        context, invoice_intent = "supplier_invoice", "existing_payable"
    binary = not row["id"].startswith("T")
    original_text = row["input"] if not binary else row["input"].get("owner_caption", row["input"].get("caption"))
    source = {"e": uid("entity"), "a": uid("owner"), "day": DAY,
              "binary": b"original-present" if binary else None, "raw": original_text,
              "capture_context": context, "invoice_intent": invoice_intent,
              "invoice_receipt_id": uid("existing_receipt") if invoice_intent else None}
    output = dict(recognition) if recognition else {"text": raw, "preparation_text": raw, "pages": [], "warnings": []}
    if recognition and "error" not in recognition:
        output["recognition_state"] = "SUCCEEDED"
    elif recognition:
        output.update(text="", preparation_text="", recognition_state="FAILED")
    facade = ReferenceScalars(refs)
    old_rows = module.rows
    module.rows = reference_rows
    try:
        slots = module.OwnerCaptureService(None).prepare(SimpleNamespace(session=facade), source, output)
    finally:
        module.rows = old_rows
    return slots, {"method": "OwnerCaptureService.prepare", "capture_context": context,
                   "invoice_intent": invoice_intent,
                   "reference_facade": "read-only explicit synthetic references; no DB executed",
                   "reference_reads": facade.calls, "sql_read_requests_satisfied_in_memory": sql_reads,
                   "not_run": "Reference validity, receipt/debt state, supplier link guards and learned-mapping database validation"}


def serial_slots(slots, dto):
    result = []
    for kind, payload, provenance in slots:
        error = None
        try:
            normalized = dto[kind].model_validate(payload).model_dump(mode="json")
        except Exception as exc:
            normalized = None
            error = f"{type(exc).__name__}: {exc}"
        result.append({"action_type": kind, "payload": payload, "provenance": provenance,
                       "dto": normalized, "dto_error": error})
    return result


def first(slots, kind):
    return next((s["payload"] for s in slots if s["action_type"] == kind), {})


def at(value, path):
    for part in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (IndexError, ValueError):
                return None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def field(name, expected, actual, *, numeric=False, purpose="prefill"):
    equal = expected == actual
    if numeric and expected is not None and actual is not None:
        try:
            equal = Decimal(str(expected)) == Decimal(str(actual))
        except InvalidOperation:
            equal = False
    status = "exact" if equal else "missing" if actual is None or actual == [] or actual == "" else "wrong"
    return {"field": name, "expected": expected, "actual": actual, "status": status, "purpose": purpose}


def inventory_evidence(slots):
    found = []
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get("nonposting_inventory_evidence"), list):
                for row in value["nonposting_inventory_evidence"]:
                    if row not in found:
                        found.append(row)
            for key, child in value.items():
                if key != "nonposting_inventory_evidence":
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(slots)
    return found


def assess(row, slots):
    ident = row["id"]
    kinds = [s["action_type"] for s in slots]
    fields, guards = [], []
    def check(name, ok, detail):
        guards.append({"check": name, "status": "PASS" if ok else "FAIL", "detail": detail})
    check("typed_DTO_validity", all(s["dto_error"] is None for s in slots), [s["dto_error"] for s in slots if s["dto_error"]])
    if ident.startswith("T"):
        mapping = {"T01": ("DIRECT_EXPENSE", "650.00", "TRY"),
                   "T02": ("PAYABLE", "5000.00", "TRY"),
                   "T03": ("PAYMENT", "2000.00", "TRY"),
                   "T05": ("PAYABLE", "5000.00", "TRY"),
                   "T08": ("DIRECT_EXPENSE", "25.00", "USD")}
        if ident in mapping:
            kind, amount, currency = mapping[ident]
            payload = first(slots, kind)
            fields += [field("action_type", kind, kinds[0] if len(kinds) == 1 else kinds),
                       field("amount", amount, payload.get("amount"), numeric=True),
                       field("currency", currency, payload.get("currency")),
                       field("event_date_candidate", DAY.isoformat(), payload.get("event_date"))]
            if ident in ("T02", "T03", "T05"):
                fields.append(field("counterparty_id", uid("serkan"), payload.get("counterparty_id")))
            if ident == "T01":
                fields.append(field("explicit_cash_account_candidate", uid("cash_try"), payload.get("money_account_id")))
            if ident == "T03":
                fields.append(field("payment_purpose", "debt", payload.get("payment_purpose")))
                check("no_invented_account", payload.get("money_account_id") is None, payload.get("money_account_id"))
            if ident == "T08":
                check("explicit_USD_not_TRY", payload.get("currency") in (None, "USD"), payload.get("currency"))
                check("no_guessed_USD_account", payload.get("money_account_id") is None, payload.get("money_account_id"))
        elif ident == "T04":
            candidates = [s["payload"] for s in slots]
            # Representation-only projection: both flat typed money fields and
            # an explicitly UNKNOWN-status review DTO retain the same facts.
            # This does not parse notes or grant financial intent to candidates.
            candidates += [p["money_candidates"] for p in candidates
                           if isinstance(p.get("money_candidates"), dict)]
            for name, expected, numeric in (("amount", "5000.00", True), ("currency", "TRY", False), ("counterparty_id", uid("serkan"), False)):
                actual = next((p.get(name) for p in candidates if p.get(name) is not None), None)
                fields.append(field(name, expected, actual, numeric=numeric))
            check("unknown_paid_status_not_asserted", not any(s["action_type"] in ("PAYMENT", "PAYABLE") for s in slots)
                  and not any(p.get("payment_purpose") == "expense" for p in candidates), kinds)
        else:
            check("future_or_third_party_not_owner_actual", not (set(kinds) & MONEY_TYPES), kinds)
        if ident == "T05":
            check("negated_payment_not_prepared_as_paid", not (set(kinds) & {"PAYMENT", "DIRECT_EXPENSE"}), kinds)
    elif ident.startswith("H"):
        payload = first(slots, "DEPOT_RECEIPT")
        check("no_money_or_stock_sibling", not (set(kinds) & (MONEY_TYPES | {"STOCK_RECEIPT", "STOCK_APPLICATION"})), kinds)
        check("recognition_not_owner_approval", not any(payload.get(k) is True for k in ("quality_reviewed", "arithmetic_reviewed", "document_reviewed", "remember_quality_mapping")), {k: payload.get(k) for k in ("quality_reviewed", "arithmetic_reviewed", "document_reviewed", "remember_quality_mapping")})
        if ident == "H05":
            numeric = {k: payload.get(k) for k in ("lines", "stated_total", "alacak_value", "document_count", "event_date")}
            check("masked_numbers_remain_unknown", all(v in (None, [], "") for v in numeric.values()), numeric)
        else:
            doc = row["input"]["document"]
            fields += [field("action_type", "DEPOT_RECEIPT", kinds[0] if len(kinds) == 1 else kinds),
                       field("depot_id", uid("depot"), payload.get("depot_id")),
                       field("event_date", "2026-10-12", payload.get("event_date")),
                       field("currency", doc["currency"], payload.get("currency")),
                       field("line_count", len(doc["lines"]), len(payload.get("lines", []))),
                       field("stated_total", doc["total_amount"], payload.get("stated_total"), numeric=True),
                       field("alacak_value", doc["alacak"], payload.get("alacak_value"), numeric=True),
                       field("document_number", doc["document_number"], payload.get("document_number"))]
            if doc.get("adet"):
                fields.append(field("document_count", int(doc["adet"]), payload.get("document_count"), numeric=True))
            for i, line in enumerate(doc["lines"]):
                for source_key, key in (("label", "original_quality_name"), ("kg", "net_weight_kg"), ("unit_price", "unit_price"), ("amount", "stated_amount")):
                    fields.append(field(f"lines.{i}.{key}", line[source_key], at(payload, f"lines.{i}.{key}"), numeric=source_key != "label"))
                check(f"line_{i}_count_not_invented", at(payload, f"lines.{i}.count") is None, at(payload, f"lines.{i}.count"))
            if ident == "H08":
                check("unknown_Yerli_quality_not_inferred", all(q is None for q in payload.get("qualities", [])), payload.get("qualities"))
            if ident == "H07":
                check("caption_does_not_override_document", payload.get("event_date") in (None, "2026-10-12") and payload.get("stated_total") in (None, "14000", "14000.00"), payload)
    elif ident.startswith("P"):
        doc = row["input"]["document"]
        payload = first(slots, "PAYABLE")
        check("invoice_does_not_prepare_arrival_or_payment", not (set(kinds) & {"STOCK_RECEIPT", "STOCK_APPLICATION", "PAYMENT", "DIRECT_EXPENSE"}), kinds)
        if ident != "P05":
            fields += [field("invoice_amount", doc["total"], payload.get("amount"), numeric=True),
                       field("invoice_currency", doc["currency"], payload.get("currency")),
                       field("invoice_date", "2026-10-12", payload.get("event_date")),
                       field("invoice_supplier", uid("supplier"), payload.get("counterparty_id"))]
        evidence = inventory_evidence(slots)
        product_line = next((p for p in evidence if p.get("product_id") == uid("kno3")), {})
        if ident != "P05":
            fields.append(field("invoice_evidence.product_id", uid("kno3"), product_line.get("product_id"), purpose="nonposting_structured_evidence"))
            if ident != "P04":
                fields += [field("invoice_evidence.quantity", "100.000", product_line.get("quantity"), numeric=True, purpose="nonposting_structured_evidence"),
                           field("invoice_evidence.unit", "kg", product_line.get("unit"), purpose="nonposting_structured_evidence")]
            else:
                check("unknown_bag_conversion_not_invented", not any(p.get("unit") == "kg" and p.get("quantity") is not None for p in evidence), evidence)
        match = first(slots, "INVOICE_MATCH")
        if ident in ("P02", "P05"):
            fields.append(field("explicit_receipt_context", uid("existing_receipt"), match.get("receipt_id")))
        if ident == "P05":
            check("existing_payable_context_no_new_debt", "PAYABLE" not in kinds, kinds)
    counts = Counter(f["status"] for f in fields)
    useful = "NOT_APPLICABLE_NEGATIVE_CONTROL" if not fields else "COMPLETE_PREFILL" if all(f["status"] == "exact" for f in fields) else "INCOMPLETE_PREFILL"
    return {"assessment_version": ASSESSMENT_VERSION,
            "field_counts": dict(counts), "fields": fields, "preparation_usefulness": useful,
            "preparation_guards": guards,
            "preparation_guard_status": "PASS" if all(g["status"] == "PASS" for g in guards) else "FAIL",
            "overall_authority_safety": "NOT_RUN_REQUIRES_DB_BROWSER",
            "recognition_credit": "NONE_NEGATIVE_CONTROL" if ident == "H05" else "FIELD_LEVEL_ONLY",
            "not_run": NOT_RUN,
            "interaction_measurements": {"trusted_clicks": None, "mandatory_inputs": None, "repeated_facts": None, "status": "NOT_RUN_BROWSER_REQUIRED"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--fixtures-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--label", required=True)
    parser.add_argument("--reference-sha", default=None)
    parser.add_argument("--cases", nargs="*", help="Optional explicitly reported subset; never changes the denominator silently")
    args = parser.parse_args()
    root = args.source_root.resolve()
    sys.path[:0] = [str(root / "src"), str(root)]
    # This worker does not connect to any database. A socket connection attempt
    # fails immediately, even if an unexpected import attempts one.
    def denied_connection(*_args, **_kwargs):
        raise RuntimeError("Benchmark forbids socket/DB connections")
    socket.socket.connect = denied_connection
    socket.create_connection = denied_connection
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE", "PGPASSWORD"):
        os.environ.pop(key, None)
    from greenhouse_owner_os.services.ocr import LocalOCR
    from greenhouse_owner_os.services.owner_preparation import prepare_owner_source
    from greenhouse_owner_os.services import owner_capture as owner_module
    from greenhouse_owner_os.domain.owner_capture import DTO
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("greenhouse_owner_os") and getattr(module, "__file__", None):
            if not Path(module.__file__).resolve().is_relative_to(root):
                raise AssertionError(f"Application module escaped selected source root: {module.__file__}")
    start_hashes = application_hashes(root)
    labels_path = args.fixtures_root / "capture_daily_benchmark.json"
    labels = json.loads(labels_path.read_text())
    assets = args.fixtures_root / "capture_daily_assets"
    manifest = json.loads((assets / "manifest.json").read_text())
    if manifest["benchmark_sha256"] != sha(labels_path):
        raise AssertionError("Frozen benchmark/asset manifest hash mismatch")
    indexed = {e["case_id"]: e for e in manifest["entries"]}
    for entry in indexed.values():
        if sha(assets / entry["filename"]) != entry["sha256"]:
            raise AssertionError(f"Frozen asset changed: {entry['filename']}")
    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / "summary.json").exists():
        raise SystemExit("Refusing to overwrite an existing benchmark result directory")
    refs = references()
    (args.output / "context.json").write_text(dump({"references": refs, "today": DAY, "no_database": True,
        "source_root": root, "reference_sha": args.reference_sha, "label": args.label,
        "benchmark_sha256": sha(labels_path), "asset_manifest_sha256": sha(assets / "manifest.json"),
        "application_hashes_before": start_hashes}), encoding="utf-8")
    results = []
    for row in labels["cases"]:
        ident = row["id"]
        if args.cases and ident not in args.cases:
            continue
        if ident.startswith("R"):
            result = {"id": ident, "status": "NOT_RUN_REQUIRES_DB_BROWSER", "frozen_case": row,
                      "reason": "Source identity, transactions, leases, encryption and response recovery cannot be proven by this non-DB runner."}
        else:
            started = time.monotonic()
            binary = ident in indexed
            recognition, recognition_error = None, None
            if binary:
                entry = indexed[ident]
                content = (assets / entry["filename"]).read_bytes()
                try:
                    recognition = LocalOCR().recognize(content, entry["mime_type"])
                except Exception as exc:
                    recognition_error = f"{type(exc).__name__}: {exc}"
                    recognition = {"error": recognition_error, "text": "", "preparation_text": "", "pages": [], "warnings": ["recognition_failed"]}
                raw = recognition.get("preparation_text", recognition.get("text", ""))
            else:
                raw = row["input"]
            try:
                universal = serial_slots(prepare_owner_source(raw, today=DAY, refs=refs, binary=binary,
                    pages=recognition.get("pages", []) if recognition else []), DTO)
                universal_error = None
            except Exception as exc:
                universal, universal_error = [], f"{type(exc).__name__}: {exc}"
            try:
                slots, invocation = prepare_runtime(owner_module, refs, row, raw, recognition)
                actual = serial_slots(slots, DTO)
                preparation_error = None
            except Exception as exc:
                actual, invocation = [], {}
                preparation_error = f"{type(exc).__name__}: {exc}"
            result = {"id": ident, "status": "EXECUTED_NON_DB", "criterion": row["criterion"],
                      "original_asset": indexed.get(ident), "original_text": row["input"] if not binary else None,
                      "original_caption": row["input"].get("owner_caption", row["input"].get("caption")) if binary else None,
                      "recognition": recognition, "recognition_error": recognition_error,
                      "connected_invocation": invocation, "connected_preparation": actual,
                      "preparation_error": preparation_error, "universal_preparation": universal,
                      "universal_error": universal_error, "assessment": assess(row, actual),
                      "universal_assessment": assess(row, universal),
                      "elapsed_seconds": round(time.monotonic() - started, 4)}
            if recognition_error or preparation_error:
                result["assessment"]["execution_error"] = recognition_error or preparation_error
                result["assessment"]["preparation_usefulness"] = "EXECUTION_FAILED"
                result["assessment"]["preparation_guard_status"] = "UNPROVEN_EXECUTION_FAILED"
        (args.output / f"{ident}.json").write_text(dump(result), encoding="utf-8")
        results.append(result)
        print(json.dumps({"id": ident, "status": result["status"],
                          "kinds": [s["action_type"] for s in result.get("connected_preparation", [])],
                          "counts": result.get("assessment", {}).get("field_counts"),
                          "error": result.get("preparation_error") or result.get("recognition_error")}), flush=True)
    end_hashes = application_hashes(root)
    counts = Counter()
    useful = Counter()
    for row in results:
        if "assessment" in row:
            counts.update(row["assessment"]["field_counts"])
            useful.update([row["assessment"]["preparation_usefulness"]])
    summary = {"label": args.label, "reference_sha": args.reference_sha, "source_root": root,
               "finished_at": datetime.now(timezone.utc), "benchmark_cases_total": len(labels["cases"]),
               "requested_subset": args.cases, "cases_recorded": len(results),
               "non_db_executed": sum(r["status"] == "EXECUTED_NON_DB" for r in results),
               "not_run": [r["id"] for r in results if r["status"] != "EXECUTED_NON_DB"],
               "connected_field_counts": dict(counts), "connected_usefulness": dict(useful),
               "preparation_guard_failures": {r["id"]: [g for g in r["assessment"]["preparation_guards"] if g["status"] == "FAIL"] for r in results if "assessment" in r and r["assessment"]["preparation_guard_status"] == "FAIL"},
               "execution_errors": {r["id"]: r.get("preparation_error") or r.get("recognition_error") for r in results if r.get("preparation_error") or r.get("recognition_error")},
               "application_source_unchanged_during_run": start_hashes == end_hashes,
               "changed_files_during_run": [k for k in set(start_hashes) | set(end_hashes) if start_hashes.get(k) != end_hashes.get(k)],
               "application_hashes_after": end_hashes, "runner_sha256": sha(__file__),
               "assessment_version": ASSESSMENT_VERSION,
               "benchmark_sha256": sha(labels_path), "original_asset_manifest_sha256": sha(assets / "manifest.json"),
               "overall_status": "DIAGNOSTIC_NON_DB_ONLY; NO OVERALL SAFETY/PRODUCT PASS", "unexecuted_dimensions": NOT_RUN}
    (args.output / "summary.json").write_text(dump(summary), encoding="utf-8")
    print(dump({k: v for k, v in summary.items() if k not in ("application_hashes_after", "preparation_guard_failures")}), flush=True)


if __name__ == "__main__":
    main()
