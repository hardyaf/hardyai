"""Least-privilege Paperless archive adapters."""

from app.integrations.paperless.adapter import PaperlessArchiveAdapter, PaperlessReadAdapter
from app.integrations.paperless.client import PaperlessClient

__all__ = ["PaperlessArchiveAdapter", "PaperlessClient", "PaperlessReadAdapter"]
