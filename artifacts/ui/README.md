# Agentos — refonte Neon Minimal Premium

Refonte appliquée aux fichiers servis par `cc-web`, sans changement du backend ni des contrats du streaming.

| Avant | Après | Intention |
| --- | --- | --- |
| Règles et variables redéfinies dans plusieurs couches | Tokens et primitives dans `base.css`, composants IDE dans `agentos.css` | Une source de vérité pour chaque composant |
| Messages rectangulaires, bordures verticales continues | Rayon 14 px, surfaces graphite, marque de rôle de 20 px | Alléger les messages et conserver l'identité jaune/rose |
| Cartes workers aux couleurs par rôle et en-têtes variables | Cartes homogènes, modèle sur sa propre ligne, état et durée distincts | Faciliter la comparaison des workers |
| Bash, sorties et diff aux bordures hétérogènes | Conteneurs arrondis, code 13 px, couleurs sémantiques atténuées | Présentation cohérente avec un IDE |
| Inspecteur aux onglets soulignés | En-tête hiérarchisé, onglets groupés, coins libres arrondis | Séparer navigation et contenu |
| Animations décoratives et transitions de largeur | Feedback de couleur court, aucun clignotement ni transition de largeur | Stabilité pendant le streaming |

La sidebar reste à gauche, la conversation reste en pleine largeur et le composer garde sa position. Les identifiants DOM, actions, sessions, confirmations, événements, algorithmes de diff et logique de compaction sont conservés. Le JavaScript ne change que pour retirer les couleurs codées en dur et déplacer le style des titres de conversation dans le CSS.

## Captures reproductibles

Les captures utilisent des données de démonstration injectées uniquement dans un navigateur de test, sans appel à un fournisseur LLM ni modification des conversations utilisateur.

| Vue | Avant | Après |
| --- | --- | --- |
| Conversation et workers | [Avant](before-conversation.png) | [Après](after-conversation.png) |
| Inspecteur | [Avant](before-inspector.png) | [Après](after-inspector.png) |
| Diff | [Avant](before-diff.png) | [Après](after-diff.png) |
| Fichier | [Avant](before-file.png) | [Après](after-file.png) |
| Skills et agents | [Avant](before-studio.png) | [Après](after-studio.png) |
| Mobile | [Avant](before-mobile.png) | [Après](after-mobile.png) |

## Validation

- `node --check claude_clone/static/js/app.js` : syntaxe valide.
- `.venv/bin/python tests/test_browser.py` : workers, inspecteur, autorisation/reprise avec écriture réelle dans un répertoire temporaire, changement de conversation et mobile validés.
- `.venv/bin/python tests/test_visual.py` : onglets, ouverture d'un fichier, diff unifié et côte à côte, panneau Skills, menu mobile, rayons calculés, absence de dégradés, absence de débordement à 390/850/1280/1920 px et réduction du mouvement validés. Aucune erreur JavaScript observée.
- Ressources HTML/CSS/JS : références locales présentes, identifiants uniques, ressources servies sur `127.0.0.1:8765` identiques aux fichiers modifiés.
- `git diff --check` : aucun problème d'espacement.

Navigateur utilisé : Chromium via Playwright. Les polices Google sont remplacées dans la recette hors ligne par la pile système configurée (UI et monospace). Les appels aux modèles sont simulés ; cette recette ne mesure pas les performances d'un fournisseur réel.

## Vérification responsive renforcée

Après signalement d'un tableau difficile à lire, la recette couvre désormais 20 largeurs de 320 à 1920 px et 5 fenêtres courtes (1262×768, 1024×600, 844×390, 390×600 et 320×568). Elle vérifie aussi les limites de chaque contrôle, du composer, des messages et du fil de conversation, ainsi que le défilement local des tableaux sur mobile.

Corrections : largeur minimale des cellules pour conserver les mots lisibles, défilement horizontal local des tableaux, sélecteurs avec une largeur minimale de 112 px et retour à la ligne, composer compact sous 600 px de hauteur. Les tests d'interaction existants passent également.

Captures : [rapport sur ordinateur](responsive-report-desktop.png), [mobile](after-mobile.png), [paysage](responsive-landscape.png).
