"""Conservative deterministic recovery for clearly tabular OCR/native text.
Only uses explicit source values; it never manufactures missing invoice fields.
"""
from __future__ import annotations
import re
from backend.schemas.invoice_schema import InvoiceData, LineItem

_NUM = re.compile(r"^\(?-?\d[\d,]*(?:\.\d+)?\)?%?$")
def _num(value):
    if value is None: return None
    s=str(value).strip().replace(',', '').replace('₹','').replace('INR','').strip()
    if s in ('', '-', '—'): return None
    pct=s.endswith('%'); s=s.rstrip('%').strip()
    try: return float(s)
    except ValueError: return None

def parse_pipe_items(text: str):
    """Parse explicit pipe-delimited invoice table rows (common PDF text layout)."""
    lines=[x.strip() for x in text.splitlines() if '|' in x]
    header_idx=None; headers=[]
    for idx,line in enumerate(lines):
        cells=[c.strip().lower().replace(' ','_') for c in line.strip('|').split('|')]
        joined=' '.join(cells)
        if ('description' in joined or 'particular' in joined) and ('quantity' in joined or 'qty' in joined):
            header_idx=idx; headers=cells; break
    if header_idx is None: return []
    def col(*names):
        # Prefer exact header matches. A substring-only search can map `tax_amount`
        # into the `line_total` column when column order differs.
        for name in names:
            for i, header in enumerate(headers):
                if header == name:
                    return i
        for name in names:
            for i, header in enumerate(headers):
                if name in header:
                    return i
        return None
    ix={
      'code':col('line_no','item_no','sr_no','s_no','item_code','code'),
      'desc':col('description','particular','item_description'),
      'qty':col('quantity','qty'), 'unit':col('unit'),
      'price':col('unit_price','rate','price_per_unit'),
      'total':col('line_total','amount','total'),
      'taxrate':col('tax_rate','gst_rate','tax_percent'),
      'tax':col('tax_amount','tax_value'), 'hsn':col('hsn','sac')}
    def get(cells,key):
        i=ix[key]
        return cells[i].strip() if i is not None and i<len(cells) else None
    out=[]
    for line in lines[header_idx+1:]:
        cells=[c.strip() for c in line.strip('|').split('|')]
        desc=get(cells,'desc')
        if not desc: continue
        # Reject totals/tax summary rows and rows without numeric quantity/price/total.
        low=desc.lower()
        if any(x in low for x in ('subtotal','grand total','cgst','sgst','igst','tax total','round off','balance due')): continue
        qty=_num(get(cells,'qty')); price=_num(get(cells,'price')); total=_num(get(cells,'total'))
        if qty is None or price is None or total is None: continue
        tax_rate = _num(get(cells, 'taxrate'))
        tax_amount = _num(get(cells, 'tax'))
        # Only calculate a missing tax value when the invoice explicitly provides a rate
        # and a pre-tax line amount. Never overwrite an explicit source tax amount.
        if tax_amount is None and tax_rate is not None:
            tax_base = total
            if tax_base is None and qty is not None and price is not None:
                tax_base = qty * price
            if tax_base is not None:
                tax_amount = round(tax_base * tax_rate / 100.0, 2)
        out.append(LineItem(item_code=get(cells,'code'),description=desc,hsn_sac=get(cells,'hsn'),quantity=qty,
          unit=get(cells,'unit'),unit_price=price,tax_rate=tax_rate,tax=tax_amount,total=total))
    return out

def _label_value(text: str, labels: tuple[str, ...]):
    for label in labels:
        match = re.search(rf"(?im)^\s*(?:{label})\s*[:#-]\s*(.*?)\s*$", text)
        if match:
            value = match.group(1).strip(" \t|:")
            if value and value.lower() not in {"not found", "not provided", "n/a", "null"}:
                return value
    return None

def _label_amount(text: str, labels: tuple[str, ...]):
    value = _label_value(text, labels)
    return _num(value) if value else None

def reconcile(data: InvoiceData, source_text: str) -> InvoiceData:
    """Reconcile AI output with unambiguous values and table cells in source text.

    Source-backed deterministic values take precedence for line-item columns. Missing
    fields are filled only when a clearly labelled source value is present.
    """
    parsed = parse_pipe_items(source_text)
    if parsed:
        data.items = parsed

    text = source_text or ""
    scalar_fields = {
        "invoice_number": ("invoice\\s*(?:number|no\\.?|#)", "bill\\s*(?:number|no\\.?|#)"),
        "invoice_date": ("invoice\\s*date", "bill\\s*date", "date\\s*of\\s*issue"),
        "due_date": ("due\\s*date", "payment\\s*due"),
        "purchase_order_number": ("(?:purchase\\s*order|po)\\s*(?:number|no\\.?|#)" ,),
        "reference_number": ("reference\\s*(?:number|no\\.?|#)",),
        "payment_terms": ("payment\\s*terms",),
    }
    for field, labels in scalar_fields.items():
        if getattr(data, field) is None:
            value = _label_value(text, labels)
            if value:
                setattr(data, field, value)

    if data.currency is None:
        if re.search(r"₹|\bINR\b|\bRs\.?", text, re.I): data.currency = "INR"
        elif "$" in text or re.search(r"\bUSD\b", text, re.I): data.currency = "USD"
        elif "€" in text or re.search(r"\bEUR\b", text, re.I): data.currency = "EUR"
        elif "£" in text or re.search(r"\bGBP\b", text, re.I): data.currency = "GBP"

    fin_labels = {
        "subtotal": ("sub\\s*total",), "taxable_amount": ("taxable\\s*amount",),
        "discount": ("discount",), "cgst": ("cgst(?:\\s*amount)?",),
        "sgst": ("sgst(?:\\s*amount)?",), "igst": ("igst(?:\\s*amount)?",),
        "other_charges": ("other\\s*charges",), "shipping": ("shipping|freight",),
        "tax": ("total\\s*tax|tax\\s*total",), "round_off": ("round\\s*off",),
        "grand_total": ("grand\\s*total|invoice\\s*total|total\\s*payable",),
        "amount_paid": ("amount\\s*paid|paid\\s*amount",), "balance_due": ("balance\\s*due|amount\\s*due",),
    }
    for field, labels in fin_labels.items():
        if getattr(data.financials, field) is None:
            value = _label_amount(text, labels)
            if value is not None:
                setattr(data.financials, field, value)

    # Party contact/tax identifiers can be safely filled from explicit labels.
    for party_name, prefix in (("supplier", "supplier"), ("customer", "customer")):
        party = getattr(data, party_name)
        for field, labels in {
            "name": (rf"{prefix}\s*name",), "address": (rf"{prefix}\s*address",),
            "email": (rf"{prefix}\s*email",), "phone": (rf"{prefix}\s*phone",),
            "tax_id": (rf"{prefix}\s*(?:tax\s*id|gstin|vat\s*number)",),
            "registration_number": (rf"{prefix}\s*registration\s*(?:number|no\.?)",),
        }.items():
            if getattr(party, field) is None:
                value = _label_value(text, labels)
                if value: setattr(party, field, value)
    return data
