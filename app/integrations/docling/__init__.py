"""Hardened local Docling Serve integration."""

from app.integrations.docling.adapter import DoclingParserAdapter
from app.integrations.docling.client import DoclingClient

__all__ = ["DoclingClient", "DoclingParserAdapter"]
