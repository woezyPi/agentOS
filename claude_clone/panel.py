"""Panel de revue : la MÊME relecture confiée en parallèle à plusieurs familles de modèles, puis fusionnée.

Pourquoi : un modèle seul a des angles morts propres à sa famille. Trois relecteurs indépendants (même contexte, aucune
communication entre eux) se contredisent là où l'un d'eux se trompe, et se recoupent sur les vrais défauts.
Garanties :
  * indépendance : chaque relecteur ne voit que le prompt et la tâche, jamais les rapports des autres ;
  * pas de substitution : si un relecteur tombe, les autres comptent (un repli d'une même famille ruinerait la diversité) ;
  * le TABLEAU DES VERDICTS est calculé par le code, pas par un modèle ; la fusion a l'interdiction d'ajouter un point
    absent des rapports ;
  * rien n'est perdu si la fusion échoue : les rapports bruts sont rendus tels quels.
"""
from __future__ import annotations

import asyncio
import re

PANEL_ROLES = ("reviewer", "challenger")
MIN_MEMBERS, MAX_MEMBERS = 2, 4
RAW_EXCERPT = 2500

SYNTH_PROMPT = """Tu fusionnes {n} relectures INDÉPENDANTES (Relecteur {letters}) d'un même objet : code, diff ou décision.
Règles strictes :
- N'ajoute AUCUN point, fait ou fichier qui ne figure pas dans au moins un rapport. Tu fusionnes, tu ne relis pas.
- Conserve pour chaque point le fichier:ligne, la preuve et la correction suggérée donnés par les relecteurs.
- Classe les points en trois sections :
  1. CONSENSUS — signalés par au moins 2 relecteurs (cite lesquels, ex. [A,B]) ; sévérité retenue = la plus haute argumentée.
  2. UN SEUL RELECTEUR — à vérifier à la main (cite lequel) ; garde-les s'ils sont appuyés par une preuve (extrait de code),
     signale-les « non corroboré » sinon.
  3. DÉSACCORDS — quand un relecteur affirme ce qu'un autre contredit : expose les deux positions et ce qui permettrait de trancher.
- Si un seul relecteur signale un point CRITIQUE avec une preuve vérifiable, dis-le explicitement même s'il est seul.
- Termine par « Verdict du panel » : APPROUVÉ, À CORRIGER ou PARTAGÉ (avec la raison en une phrase), puis les 3 points clés.
Reste concis, en français, sans remplissage."""

_VERDICT = re.compile(r"APPROUV[ÉE]|[ÀA] CORRIGER|GO SOUS CONDITIONS|NO-GO|\bGO\b", re.I)


def verdict_of(text: str) -> str:
    """Dernier verdict explicite d'un rapport (normalisé), sinon « non précisé »."""
    found = _VERDICT.findall(text or "")
    if not found:
        return "non précisé"
    v = found[-1].upper().replace("É", "E").replace("À", "A")
    return {"APPROUVE": "APPROUVÉ", "A CORRIGER": "À CORRIGER"}.get(v, v)


async def run_panel(members: list[tuple[str, str]], run_member, synthesize) -> tuple[str, dict]:
    """members : [(lettre, modèle)] ; run_member(lettre) → texte ; synthesize(rapports {lettre: texte}) → texte.
    Renvoie (rapport Markdown, métadonnées)."""
    async def safe(letter: str):
        try:
            return letter, await run_member(letter), None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # un relecteur indisponible ne fait pas tomber le panel
            return letter, None, f"{type(exc).__name__}: {str(exc)[:90]}"

    results = await asyncio.gather(*(safe(l) for l, _ in members))
    models = dict(members)
    reports = {l: t.strip() for l, t, _ in results if t and t.strip()}
    failures = {l: e or "rapport vide" for l, t, e in results if not (t and t.strip())}
    verdicts = {l: verdict_of(t) for l, t in reports.items()}

    rows = ["| Relecteur | Modèle | Verdict |", "|---|---|---|"]
    for l, _ in members:
        rows.append(f"| {l} | {models[l]} | " + (verdicts[l] if l in reports else f"✖ indisponible ({failures[l]})") + " |")
    counts = {v: list(verdicts.values()).count(v) for v in dict.fromkeys(verdicts.values())}
    agree = ("unanime : " + next(iter(counts)) if len(counts) == 1 and len(reports) > 1
             else "partagé : " + ", ".join(f"{n} × {v}" for v, n in counts.items()) if len(reports) > 1 else "un seul rapport exploitable")
    head = [f"## Panel de revue — {len(reports)}/{len(members)} relecteurs indépendants ({len({m.split('/')[0] for m in models.values()})} familles)",
            *rows, f"Accord sur le verdict : **{agree}**", ""]

    meta = {"members": len(members), "ok": len(reports), "verdicts": verdicts, "failures": failures}
    if len(reports) < MIN_MEMBERS:
        body = ["> ⚠ Moins de 2 relecteurs ont répondu : ce n'est pas un panel, seulement un rapport isolé (non recoupé).", ""]
        body += [f"### Relecteur {l}\n{t}" for l, t in reports.items()] or ["Aucun rapport exploitable."]
        return "\n".join(head + body), meta

    try:
        synth = (await synthesize(reports)).strip() or "(synthèse vide)"
    except Exception as exc:
        synth = f"⚠ Fusion indisponible ({type(exc).__name__}) : voici les rapports bruts, à recouper à la main."
    raw = ["", "### Rapports bruts (extraits)"] + [
        f"**Relecteur {l}** — {verdicts[l]}\n{t[:RAW_EXCERPT]}{'…' if len(t) > RAW_EXCERPT else ''}" for l, t in reports.items()]
    return "\n".join(head + ["### Synthèse", synth] + raw), meta
