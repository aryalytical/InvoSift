"""Deterministic post-extraction checks. Never invents or silently changes invoice values."""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from backend.config import get_settings
from backend.schemas.invoice_schema import InvoiceData, ValidationIssue, ValidationReport

GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

REQUIRED_FIELDS = [
    ("invoice_number", "Invoice number"),
    ("invoice_date", "Invoice date"),
    ("supplier.name", "Supplier name"),
    ("financials.grand_total", "Grand total"),
]


def _get(data: InvoiceData, path: str):
    obj = data
    for part in path.split("."):
        obj = getattr(obj, part)
    return obj


def _num_in_text(value: float, text: str) -> bool:
    if value is None:
        return False
    candidates = {f"{value:.2f}", f"{value:.0f}", f"{value:g}"}
    flat = re.sub(r"[\s,]", "", text)
    return any(c in flat for c in candidates)


def validate_invoice(
    data: InvoiceData,
    ocr_text: Optional[str] = None,
    ocr_mean_confidence: Optional[float] = None,
    ocr_low_ratio: float = 0.0,
) -> ValidationReport:
    s = get_settings()
    tol = s.amount_tolerance
    issues: List[ValidationIssue] = []
    flags: Dict[str, str] = {}
    reasons: List[str] = []

    def issue(code: str, message: str, field: Optional[str] = None, level: str = "warning") -> None:
        issues.append(ValidationIssue(level=level, code=code, message=message, field=field))
        if field:
            flags[field] = "review"

    def ok(code: str, message: str) -> None:
        issues.append(ValidationIssue(level="ok", code=code, message=message))

    missing = []
    for path, label in REQUIRED_FIELDS:
        if _get(data, path) in (None, ""):
            issue(f"missing_{path}", f"{label} could not be detected.", path)
            missing.append(label)
    if not missing:
        ok("required_fields", "Core invoice fields found")
    else:
        reasons.append("Missing: " + ", ".join(missing))

    # Party format checks.
    for who, party in (("supplier", data.supplier), ("customer", data.customer)):
        tid = (party.tax_id or "").replace(" ", "").upper()
        if tid and len(tid) == 15 and not GSTIN_RE.match(tid):
            issue(f"bad_{who}_gstin", f"{who.title()} GSTIN '{party.tax_id}' does not match the standard format.", f"{who}.tax_id")
        if party.email and not EMAIL_RE.match(party.email):
            issue(f"bad_{who}_email", f"{who.title()} email looks invalid.", f"{who}.email")

    # Line-item arithmetic.
    bad_lines = 0
    complete_lines = 0
    for n, item in enumerate(data.items, start=1):
        if item.quantity is not None and item.unit_price is not None and item.total is not None:
            complete_lines += 1
            base = item.quantity * item.unit_price - (item.discount or 0)
            candidates = [base, base + (item.tax or 0), item.quantity * item.unit_price, item.quantity * item.unit_price + (item.tax or 0)]
            if not any(abs(c - item.total) <= max(tol, 0.005 * abs(item.total)) for c in candidates):
                bad_lines += 1
                issue(
                    f"line_{n}_math",
                    f"Line {n}: quantity × unit price does not reconcile with the extracted line total.",
                    f"items.{n}",
                )
        if item.tax_rate is not None and not (0 <= item.tax_rate <= 100):
            issue(f"line_{n}_tax_rate", f"Line {n}: tax rate {item.tax_rate:g}% is outside 0–100%.", f"items.{n}.tax_rate")

    if not data.items:
        issue("no_items", "No line items were detected.", "items")
        reasons.append("No line items found")
    elif complete_lines and bad_lines == 0:
        ok("line_items", f"{len(data.items)} line item(s) extracted; arithmetic checks passed for {complete_lines} complete row(s)")
    elif bad_lines:
        reasons.append(f"{bad_lines} line item(s) have arithmetic mismatches")

    # Totals checks.
    f = data.financials
    item_totals = [i.total for i in data.items if i.total is not None]
    sum_items = sum(item_totals) if item_totals else None
    if f.grand_total is not None and sum_items is not None and data.items:
        if abs(sum_items - f.grand_total) <= max(tol, 0.01 * abs(f.grand_total)):
            ok("grand_total_items", "Grand total reconciles with line-item totals")
        elif f.tax is not None and abs(sum_items + f.tax - f.grand_total) <= max(tol, 0.01 * abs(f.grand_total)):
            ok("grand_total_items_tax", "Grand total reconciles with line-item totals plus tax")
        else:
            issue("grand_total_math", f"Line-item totals ({sum_items:,.2f}) do not reconcile with grand total ({f.grand_total:,.2f}).", "financials.grand_total")
            reasons.append("Grand total does not reconcile with line items")

    if f.subtotal is not None and sum_items is not None and data.items:
        sum_pre_tax = sum((i.total or 0) - (i.tax or 0) for i in data.items)
        if abs(f.subtotal - sum_items) > max(tol, 0.01 * abs(f.subtotal)) and abs(f.subtotal - sum_pre_tax) > max(tol, 0.01 * abs(f.subtotal)):
            issue("subtotal_math", f"Line items do not reconcile with subtotal ({f.subtotal:,.2f}).", "financials.subtotal")
            reasons.append("Subtotal does not reconcile with line items")

    component_tax = sum(x or 0 for x in (f.cgst, f.sgst, f.igst))
    if f.tax is not None and component_tax and abs(component_tax - f.tax) > max(tol, 0.01 * abs(f.tax)):
        issue("tax_math", f"CGST + SGST + IGST ({component_tax:,.2f}) does not reconcile with total tax ({f.tax:,.2f}).", "financials.tax")
        reasons.append("Tax components do not reconcile")

    if f.grand_total is not None and f.amount_paid is not None and f.balance_due is not None:
        if abs(f.grand_total - f.amount_paid - f.balance_due) > tol:
            issue("balance_math", "Grand total minus amount paid does not reconcile with balance due.", "financials.balance_due")
            reasons.append("Balance due does not reconcile")

    # Grounding checks are deliberately conservative.
    if ocr_text and len(ocr_text.strip()) > 20:
        flat = re.sub(r"[^a-z0-9]", "", ocr_text.lower())
        for path, label in (("invoice_number", "Invoice number"), ("supplier.name", "Supplier name")):
            value = _get(data, path)
            needle = re.sub(r"[^a-z0-9]", "", str(value).lower()) if value else ""
            if needle and len(needle) >= 4 and needle not in flat:
                issue(f"ungrounded_{path}", f"{label} was not found in the extracted document text; please verify it.", path)
        if f.grand_total is not None and not _num_in_text(f.grand_total, ocr_text):
            issue("ungrounded_total", "Grand total was not found in the extracted document text; please verify it.", "financials.grand_total")

    if data.invoice_date and data.due_date:
        a = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", data.invoice_date)
        b = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", data.due_date)
        if a and b and data.due_date < data.invoice_date:
            issue("due_before_invoice", "Due date is earlier than invoice date.", "due_date")

    if ocr_mean_confidence is not None and ocr_mean_confidence < s.ocr_low_confidence:
        issue("low_ocr_quality", f"Average OCR confidence is {ocr_mean_confidence:.0%}; review extracted values carefully.")
        reasons.append("Low OCR confidence")
    elif ocr_low_ratio > 0.35:
        issue("many_uncertain_lines", f"{ocr_low_ratio:.0%} of OCR lines have low confidence.")
        reasons.append("Many low-confidence OCR lines")

    needs_review = any(i.level in ("warning", "error") for i in issues)
    return ValidationReport(issues=issues, needs_review=needs_review, reasons=reasons, field_flags=flags)
