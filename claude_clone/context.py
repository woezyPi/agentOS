"""Jauge de contexte : combien de tokens le superviseur envoie à chaque appel, par rapport à la fenêtre du modèle et au seuil d'auto-compaction.

Estimation (le serveur ne donne les tokens réels qu'après coup, et pas par conversation) : tokens approximatifs des messages
+ du résumé + un surcoût fixe pour le prompt système et les schémas d'outils. Suffisant pour une jauge ; la ligne « tokens in »
de fin de tour reste la valeur exacte du dernier appel.
"""
from __future__ import annotations

from langchain_core.messages.utils import count_tokens_approximately

from claude_clone import config
from claude_clone.introspect import CONTEXT_WINDOWS

SYSTEM_OVERHEAD = 6000  # prompt système du superviseur + schémas des ~25 outils (mesuré : ~5-7 k)


def fmt_tokens(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n / 1_000_000:.1f} M" if n >= 1_000_000 else f"{round(n / 1000)} k"


def context_info(values: dict, settings=None) -> dict:
    msgs, summary = values.get("messages", []), values.get("summary", "") or ""
    tokens = count_tokens_approximately(msgs) + (len(summary) // 3) + SYSTEM_OVERHEAD
    window = CONTEXT_WINDOWS.get(getattr(settings, "supervisor_model", ""))
    return {
        "tokens": tokens, "window": window, "compact_at": config.CONTEXT_COMPACT_TOKENS, "keep": config.CONTEXT_KEEP_TOKENS,
        "messages": len(msgs), "summary_chars": len(summary),
        "pct_window": round(100 * tokens / window) if window else None,
        "pct_compact": round(100 * tokens / config.CONTEXT_COMPACT_TOKENS),
    }


def format_context(info: dict, bar: bool = True) -> str:
    pct = min(100, info["pct_compact"])
    gauge = ("█" * round(pct / 10) + "░" * (10 - round(pct / 10)) + " ") if bar else ""
    win = f" / {fmt_tokens(info['window'])} ({info['pct_window']} %)" if info["window"] else ""
    return (f"{gauge}≈ {fmt_tokens(info['tokens'])}{win} · compaction auto à {fmt_tokens(info['compact_at'])} ({info['pct_compact']} %) · "
            f"{info['messages']} messages · résumé : {'oui, ' + str(info['summary_chars']) + ' car.' if info['summary_chars'] else 'non'}")
