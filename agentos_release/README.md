# Agentos — version modulaire (prototype intégré)

## Installation

1. Faire une sauvegarde ou une branche Git.
2. Copier `claude_clone/static/index.html`, `claude_clone/static/css/` et `claude_clone/static/js/` dans le projet.
3. Remplacer `claude_clone/server.py` après avoir vérifié le diff. `main.py` est inclus inchangé comme référence et ne nécessite pas de remplacement.
4. Lancer le serveur web selon votre commande habituelle (`cc-web`).

Le layout principal, les routes API, les permissions et les diffs existants sont conservés.

### Inclus
- Design noir + jaune néon + rose néon, sans glow ni dégradé.
- Cartes de workers resserrées, avec inspecteur latéral.
- Panneau Activité/Logs/Fichiers/Performances.
- Mises à jour DOM réconciliées pour le message actif, sans remplacement intégral.
- Temps du premier fragment reçu dans le navigateur étiqueté comme tel (ce n'est PAS un TTFT du fournisseur).
- Context monitor et compaction existants préservés.

### Limites explicites
- Le véritable TTFT par appel LLM, le débit tokens/s réel et le contexte par worker nécessitent des données structurées de `models.py`, `graph.py` et `telemetry.py`. Les vues signalent que ces mesures sont indisponibles.
- L'inspecteur conserve le même agent tant qu'il reste en mémoire sur la page. Après changement de session, le rouvrir pour éviter de présenter un agent périmé.
- Pas de test d'intégration avec modèles réels. Une recette visuelle navigateur et une recette de flux concurrents restent nécessaires.
