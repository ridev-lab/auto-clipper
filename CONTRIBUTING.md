# Contribuer à Auto Clipper / Contributing

Merci de vouloir améliorer Auto Clipper ! / Thanks for helping improve Auto Clipper!

Auto Clipper est une application **locale** pour Windows : API FastAPI, interface HTML/CSS/JS, pipeline Python de découpage vidéo, FFmpeg, faster-whisper et Ollama. Les contributions doivent préserver l'exécution locale et éviter de transmettre des vidéos ou données utilisateur à un service cloud sans information claire et choix explicite.

## Avant de commencer / Before you start

1. Cherche d'abord si une issue ou une pull request traite déjà le sujet.
2. Ouvre une issue pour une évolution importante afin de discuter de l'approche avant de coder.
3. Travaille sur une branche dédiée et crée une pull request avec un changement précis.
4. Ne joins **aucune vidéo privée**, modèle IA téléchargé, clé, cookie YouTube ou donnée personnelle aux commits, captures ou journaux.

Pour signaler une vulnérabilité, lis [SECURITY.md](SECURITY.md) : n'ouvre pas d'issue publique contenant des détails exploitables.

## Environnement de développement / Development setup

Prérequis pour l'application complète sous Windows : **Python 3.12**, FFmpeg, et Ollama pour les fonctions IA locales. Une carte NVIDIA est recommandée pour accélérer la transcription, mais les modules prévoient également des chemins CPU.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest tests/test_units.py -q
```

Pour l'installation complète et les modèles, utiliser `setup.bat` ; pour démarrer l'interface, `start.bat`. L'interface est normalement disponible sur `http://localhost:8000`. **N'expose pas le serveur sur Internet.**

### Vérifications rapides sans GPU / Lightweight checks

La CI utilise un jeu réduit de dépendances, sans téléchargement de modèle IA ni GPU :

```bash
python -m pip install -r requirements-ci.txt
python -m pytest tests/test_units.py -q
python -m compileall -q clipper backend clipper.py server.py
```

Le test d'intégration [`tests/test_e2e_offline.py`](tests/test_e2e_offline.py) dépend de FFmpeg et d'une configuration multimédia locale. Il est volontairement distinct des vérifications de CI.

## Structure utile / Where to contribute

| Dossier | Responsabilité |
| --- | --- |
| `backend/` | API FastAPI, gestion des tâches, persistance SQLite, sécurité du serveur local |
| `clipper/` | téléchargement, transcription, détection, scoring, recadrage, sous-titres et rendu |
| `frontend/` | interface web locale et éditeur de clips |
| `tests/` | vérifications unitaires et test de bout en bout hors ligne |
| `setup.bat`, `start.bat` | installation et démarrage sous Windows |

## Qualité attendue / Pull request checklist

- [ ] Le changement est ciblé et décrit son usage.
- [ ] Les tests unitaires passent, ou le blocage est expliqué.
- [ ] Les nouveaux comportements sont couverts par des tests lorsque c'est possible.
- [ ] Les chemins Windows, l'utilisation hors ligne et l'absence de GPU ont été pris en compte.
- [ ] Aucun secret, média utilisateur ou modèle volumineux n'a été ajouté.
- [ ] Les captures, résultats ou logs fournis ne contiennent aucune donnée personnelle.

**Langues :** français ou anglais dans les issues et pull requests.

**Licence :** les conditions de réutilisation du code restent à préciser par le propriétaire avant l'ouverture aux contributions externes. L'absence de licence explicite ne signifie pas que le code est librement réutilisable.
