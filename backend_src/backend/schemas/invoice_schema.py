"""Structured invoice schemas used by the local extractor, database and API."""
from __future__ import annotations

import re
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NULL_STRINGS = {"", "null", "none", "n/a", "na", "nil", "-", "--", "not available", "not found", "unknown"}


def to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if s.lower() in _NULL_STRINGS:
        return None
    negative = (s.startswith("(") and s.endswith(")")) or s.startswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2 and len(parts) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        n = float(s)
    except ValueError:
        return None
    return -n if negative else n


def to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v is not None)
    s = str(value).strip()
    if s.lower() in _NULL_STRINGS:
        return None
    return s


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class Party(_Base):
    name: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    tax_id: Optional[str] = None
    registration_number: Optional[str] = None

    @field_validator("name", "address", "email", "phone", "tax_id", "registration_number", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return to_text(v)


class LineItem(_Base):
    item_code: Optional[str] = None
    description: Optional[str] = None
    hsn_sac: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    unit_price: Optional[float] = None
    discount: Optional[float] = None
    tax_rate: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None

    @field_validator("item_code", "description", "hsn_sac", "unit", mode="before")
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return to_text(v)

    @field_validator("quantity", "unit_price", "discount", "tax_rate", "tax", "total", mode="before")
    @classmethod
    def _num(cls, v: Any) -> Optional[float]:
        return to_number(v)


class Financials(_Base):
    subtotal: Optional[float] = None
    taxable_amount: Optional[float] = None
    discount: Optional[float] = None
    cgst: Optional[float] = None
    sgst: Optional[float] = None
    igst: Optional[float] = None
    other_charges: Optional[float] = None
    shipping: Optional[float] = None
    tax: Optional[float] = None
    round_off: Optional[float] = None
    grand_total: Optional[float] = None
    amount_paid: Optional[float] = None
    balance_due: Optional[float] = None

    @field_validator(
        "subtotal", "taxable_amount", "discount", "cgst", "sgst", "igst", "other_charges",
        "shipping", "tax", "round_off", "grand_total", "amount_paid", "balance_due", mode="before",
    )
    @classmethod
    def _num(cls, v: Any) -> Optional[float]:
        return to_number(v)


class InvoiceData(_Base):
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    purchase_order_number: Optional[str] = None
    reference_number: Optional[str] = None
    currency: Optional[str] = None
    invoice_type: Optional[str] = None
    payment_terms: Optional[str] = None
    supplier: Party = Field(default_factory=Party)
    customer: Party = Field(default_factory=Party)
    items: List[LineItem] = Field(default_factory=list)
    financials: Financials = Field(default_factory=Financials)

    @field_validator(
        "invoice_number", "invoice_date", "due_date", "purchase_order_number", "reference_number",
        "currency", "invoice_type", "payment_terms", mode="before",
    )
    @classmethod
    def _text(cls, v: Any) -> Optional[str]:
        return to_text(v)

    @field_validator("supplier", "customer", "financials", mode="before")
    @classmethod
    def _none_to_default(cls, v: Any) -> Any:
        return {} if v is None else v

    @field_validator("items", mode="before")
    @classmethod
    def _items(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, dict):
            return [v]
        # Preserve already validated LineItem objects as well as raw dictionaries.
        return [i.model_dump() if isinstance(i, LineItem) else i for i in v
                if isinstance(i, (dict, LineItem))]


class ValidationIssue(BaseModel):
    level: str
    code: str
    message: str
    field: Optional[str] = None


class ValidationReport(BaseModel):
    issues: List[ValidationIssue] = Field(default_factory=list)
    needs_review: bool = False
    reasons: List[str] = Field(default_factory=list)
    field_flags: dict = Field(default_factory=dict)


class InvoiceUpdate(BaseModel):
    data: InvoiceData
    mark_reviewed: bool = True


class InvoiceSummary(BaseModel):
    id: int
    invoice_number: Optional[str]
    supplier_name: Optional[str]
    invoice_date: Optional[str]
    grand_total: Optional[float]
    currency: Optional[str]
    status: str
    needs_review: bool
    original_filename: str
    created_at: str


class InvoiceDetail(InvoiceSummary):
    data: InvoiceData
    validation: ValidationReport
    page_count: int
    ocr_engine: Optional[str] = None
    ocr_mean_confidence: Optional[float] = None
    error_message: Optional[str] = None
    updated_at: Optional[str] = None
    manually_edited: bool = False
    processing_times: dict[str, int] = Field(default_factory=dict)
