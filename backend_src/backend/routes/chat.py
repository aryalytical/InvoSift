from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import requests
from backend.config import get_settings
from backend.models import invoice as repo
from backend.services.export_service import invoice_to_dict

router = APIRouter(prefix="/api/chat", tags=["chat"])
class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    invoice_id: int | None = None
    history: list[dict] = Field(default_factory=list, max_length=20)

@router.post("")
def chat(body: ChatRequest):
    s=get_settings()
    context="No invoice is selected. Answer general questions about invoices, accounting document fields, extraction concepts, GST concepts, and how to interpret invoice data. Do not insist that an invoice be selected. If the user asks about a particular document, explain that no document context is attached."
    if body.invoice_id is not None:
        inv=repo.get_invoice(body.invoice_id)
        if inv is None: raise HTTPException(404,"Invoice not found.")
        context=invoice_to_dict(inv)
    prompt = ("You are InvoSift, a careful assistant that helps users understand and extract information from invoices. "
      "When invoice context is supplied, use only that data and do not invent missing values. Without invoice context, answer general questions helpfully using general knowledge, and clearly distinguish general guidance from document-specific facts. "
      "You can summarize, compare fields, answer questions, and return requested subsets as JSON or a compact table. "
      "Invoice context (JSON):\n"+__import__('json').dumps(context,ensure_ascii=False,default=str)+
      "\n\nConversation:\n"+ "\n".join(f"{m.get('role','user')}: {str(m.get('content',''))[:3000]}" for m in body.history[-12:])+
      "\nuser: "+body.message+"\nassistant:")
    try:
        r=requests.post(s.ollama_host+"/api/generate",json={"model":s.ollama_model,"prompt":prompt,"stream":False,"options":{"temperature":0.2}},timeout=s.ollama_timeout)
        r.raise_for_status()
        return {"reply":r.json().get("response","").strip()}
    except Exception:
        raise HTTPException(503,"The AI model is not ready. Use Connect AI in the app first.")
