"""Événements « en direct » des sous-agents vers l'interface (flux « custom » de LangGraph). Jamais bloquant, sans effet hors d'un tour."""
from __future__ import annotations


def emit(event: dict) -> None:
    try:
        from langgraph.config import get_stream_writer
        get_stream_writer()(event)
    except Exception:
        pass
