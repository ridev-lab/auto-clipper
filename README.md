# Auto Clipper

[**English documentation**](README.en.md) · [Contribuer](CONTRIBUTING.md) · [Sécurité](SECURITY.md)

Outil **100 % local et gratuit** qui transforme une vidéo (lien YouTube ou fichier) en
**clips verticaux 9:16 sous-titrés**, prêts pour TikTok, YouTube Shorts et Instagram Reels.
Pensé pour le gameplay GTA 6 (streams, vidéos de créateurs, gameplay perso), y compris
les vidéos **sans voix**.

- Transcription au mot près sur ta carte graphique (faster-whisper, `large-v3-turbo`)
- Choix des moments forts : IA locale (Ollama) + pics sonores + changements de scène + intensité de l'action
- Recadrage automatique : facecam en haut / fond flou / suivi de l'action
- Sous-titres karaoké animés, mots-clés en couleur, titre hook
- Titres, descriptions et hashtags en français
- Interface web avec aperçu instantané, timeline, édition des sous-titres et re-rendu

Aucune donnée ne quitte ta machine (sauf le téléchargement de la vidéo YouTube elle-même).

---

## 1. Installation (Windows 10/11)

**Prérequis** : Windows 10/11, Python **3.12**, une carte NVIDIA (recommandé, sinon tout
tourne sur le processeur, plus lentement), ~15 Go d'espace disque.

1. Si Python 3.12 n'est pas installé : `winget install -e --id Python.Python.3.12`
2. Double-clique sur **`setup.bat`**.

`setup.bat` fait tout le reste, et peut être relancé sans risque :

| Étape | Détail |
|---|---|
| ffmpeg | installé via `winget install Gyan.FFmpeg` s'il manque (build complète avec NVENC et libass) |
| Ollama | installé via `winget install Ollama.Ollama` s'il manque, puis téléchargement du modèle `qwen2.5:7b` (≈5 Go) |
| Runtime JavaScript | Node.js (déjà présent) ou Deno, nécessaire à yt-dlp pour YouTube |
| Python | création de `.venv` et installation de `requirements.txt` (≈3 Go la première fois) |
| CUDA | **aucun CUDA Toolkit ni PyTorch** : cuBLAS et cuDNN sont installés en paquets pip |
| Ressources | polices (licence OFL), modèle de détection de visage YuNet, modèle Whisper (≈1,6 Go) |
| Diagnostic | `python clipper.py doctor` affiche un résumé `[OK]` / `[!]` / `[ERREUR]` |

## 2. Lancement

Double-clique sur **`start.bat`** :
- met à jour yt-dlp (une fois par jour au maximum) ;
- démarre Ollama si besoin ;
- lance le serveur et ouvre **http://localhost:8000**.

Ferme la fenêtre noire pour arrêter le serveur. Les traitements en cours seront marqués
« interrompus » et pourront être **repris** au prochain lancement : les étapes déjà faites
(téléchargement, transcription…) sont en cache et ne sont jamais refaites.

## 3. Utilisation de l'interface web

### Nouveau projet
1. Onglet **Lien YouTube** : colle le lien, l'aperçu (titre, durée) s'affiche.
   Onglet **Fichier vidéo** : glisse un MP4 / MOV / MKV / WEBM (plusieurs Go acceptés ;
   l'envoi se fait par morceaux et **reprend** si tu resélectionnes le même fichier).
2. Choisis le nombre de clips et la durée (20 à 60 s par défaut).
3. **Options avancées** (facultatif) : layout, langue, style des sous-titres, titre hook,
   **plage à analyser** (ex. `1:20:00 → 1:45:00` pour un stream de 4 h : seule cette
   partie est téléchargée et analysée).
4. **Générer les clips**.

### Traitement
La progression s'affiche en direct, étape par étape, avec le journal. Tu peux fermer
l'onglet et revenir plus tard, ou **Annuler**. Une seule tâche utilise le GPU à la fois :
les suivantes attendent dans la file (visible en haut à droite).

### Résultats
Les clips sont triés par **score viral** (0-100). Chaque carte indique la raison du choix et
le détail des signaux (IA, audio, action, coupes). Survole une miniature pour la lire.
**Tout exporter** télécharge un ZIP avec tous les MP4 et un fichier texte des titres,
descriptions et hashtags.

### Éditeur de clip
- **Aperçu instantané** : le recadrage et les sous-titres sont dessinés en direct dans le
  navigateur, exactement comme dans le rendu final. L'onglet « Rendu final » lit le MP4.
- **Découpe** : poignées sur la timeline (vignettes, énergie sonore, mots, coupes), champs
  numériques et boutons ±0,5 s. Les bornes s'accrochent aux mots. `Ctrl + molette` pour zoomer.
- **Layout** : Facecam / Fond flou / Suivi. « Régler la zone facecam » permet de corriger
  le cadre de la webcam à la souris (valable pour tout le projet).
- **Sous-titres** : clique un mot pour corriger son texte, le passer en mot-clé coloré ou le
  masquer (`Entrée` valide, `Tab` passe au mot suivant).
- **Style** : preset de sous-titres, titre hook (texte et affichage).
- **Publication** : titre, description, hashtags, boutons copier.
- **Re-rendre** : le bouton clignote quand il y a des modifications non rendues.
- `Espace` : lecture / pause.

### Clip manuel
Bouton **Clip manuel** sur la page du projet : choisis une plage sur la timeline de la vidéo
source, le clip est créé puis rendu (titre et hashtags générés automatiquement).

### Historique
Liste des projets avec l'espace disque utilisé ; suppression d'un projet et de tous ses fichiers.

## 4. Ligne de commande (traitement par lot)

```bat
.venv\Scripts\python clipper.py <url_ou_fichier> --clips 5 --layout auto --min 20 --max 60 --lang fr
.venv\Scripts\python clipper.py "D:\Videos\session.mp4" --start 3600 --end 5400 --preset gaming_neon
.venv\Scripts\python clipper.py doctor
```

Options : `--clips`, `--layout auto|facecam|blur|track`, `--min`, `--max`,
`--lang auto|fr|en`, `--preset <nom>`, `--no-hook`, `--start`/`--end` (plage en secondes).
Les projets créés en ligne de commande apparaissent aussi dans l'historique de l'interface
(un fichier local n'est jamais déplacé : il est lié ou copié dans le projet).
Évite de lancer la CLI pendant qu'un traitement tourne dans l'interface (une seule tâche GPU à la fois).

## 5. Comment les moments sont choisis

| Signal | Source | Utile pour |
|---|---|---|
| **IA** | Ollama lit la transcription par fenêtres de 6 min et renvoie des plages de phrases numérotées + accroche + raison + score | vidéos avec voix (réactions, punchlines) |
| **Audio** | pics de volume et d'attaques (librosa), relatifs au niveau habituel de la vidéo | explosions, cris, rires |
| **Scènes** | densité de changements de plan (PySceneDetect) | montages rapides |
| **Mouvement** | différence entre images | poursuites, fusillades (gameplay sans voix) |

Si la vidéo a moins de 12 % de parole, le profil **gameplay sans voix** s'active
automatiquement (audio + scènes + mouvement). Les bornes sont alignées sur les mots et les
phrases (jamais de coupe au milieu d'un mot), avec 0,3 s de marge.

### Layout automatique
En mode **auto**, la facecam est d'abord cherchée sur ~30 images de la vidéo : un visage
qui reste au même endroit, qui **bouge** d'une image à l'autre (une vignette ou une affiche
fixe est ignorée) et qui n'est pas minuscule. Puis le layout est choisi **clip par clip** :

| Ce qu'on voit pendant le clip | Layout choisi |
|---|---|
| la webcam incrustée est visible | **Facecam** (webcam en haut, jeu en bas) |
| un grand visage occupe l'écran (webcam plein écran, IRL) | **Suivi** (recadrage vertical qui suit le visage) |
| sinon (gameplay) | **Fond flou** |

Si le cadre détecté n'est pas parfait, corrige-le dans l'éditeur (« Régler la zone facecam »).

## 6. Réglages : `config.yaml`

Tout est commenté dans le fichier. Redémarre le serveur après une modification.

| Section | Contenu |
|---|---|
| `server` | port (8000), ouverture auto du navigateur |
| `paths` | dossiers `workspace/` (cache par projet), `output/` (clips), `models/`, base SQLite |
| `tools` | chemins de ffmpeg / ffprobe / ollama (`auto` = détection), runtime JS de yt-dlp |
| `defaults` | valeurs par défaut du formulaire |
| `transcription` | modèle Whisper, `cuda`/`cpu`, `compute_type`, seuil de parole du profil sans voix |
| `ollama` | modèle (ex. `qwen3:8b`), température, taille des fenêtres, contexte |
| `scoring` | **poids** des signaux (avec voix / sans voix), pénalité des moments non retenus par l'IA, marge |
| `reframe` | taille de sortie, hauteur de la facecam (35 %), flou, zoom du layout flou, lissage du suivi |
| `render` | encodeur (`auto` = NVENC sinon libx264), débits, loudness (-14 LUFS) |
| `subtitle_presets` | styles de sous-titres (police, taille, couleurs, contour, mots par ligne, position) |
| `hook` | style du titre hook |
| `metadata` | contexte donné à l'IA pour les titres et hashtags |

Pour ajouter une police : dépose le `.ttf` dans `fonts/` et indique-le dans un preset (`font_file`).

## 7. Dépannage

**Le diagnostic** (`start.bat` → voyants en haut à droite de l'interface, ou
`.venv\Scripts\python clipper.py doctor`) indique quoi faire pour chaque point rouge.

- **CUDA / « Could not locate cudnn_ops64_9.dll »** : relance `setup.bat` (réinstalle
  `nvidia-cudnn-cu12`). Solution de secours : copie les DLL cuBLAS 12 / cuDNN 9 dans un
  dossier `bin\` à la racine du projet. En dernier recours, la transcription bascule
  automatiquement sur le CPU (int8), plus lent mais fonctionnel.
- **Mémoire GPU insuffisante** : un seul modèle est chargé à la fois (Whisper dans un
  processus séparé, puis Ollama, déchargé avant le rendu). En cas d'erreur, Whisper réessaie
  automatiquement en `int8_float16` puis sur CPU. Ferme les jeux et logiciels gourmands en VRAM,
  ou choisis un modèle Ollama plus petit (`qwen2.5:3b`).
- **NVENC indisponible** : mets à jour le pilote NVIDIA. Sinon l'encodage passe en libx264
  (CPU), automatiquement.
- **Ollama** : s'il ne démarre pas tout seul, lance l'application Ollama ou `ollama serve`.
  Modèle absent : `ollama pull qwen2.5:7b`. Sans Ollama, les clips sont quand même choisis
  (signaux audio / scènes / mouvement) avec des titres génériques.
- **YouTube** : « protection » ou « signature » → relance `start.bat` (met à jour yt-dlp).
  Vidéo privée, soumise à l'âge ou live en cours : non téléchargeable.
- **Port 8000 occupé** : change `server.port` dans `config.yaml`.
- **Logs détaillés** : `workspace\<projet>\logs\<job>.log`.

## 8. Architecture

```
auto-clipper/
├─ setup.bat / start.bat     installation et lancement
├─ clipper.py                CLI
├─ server.py                 serveur web (python -m backend)
├─ config.yaml               réglages
├─ backend/                  FastAPI : app.py (API + SSE), jobs.py (file), tasks.py (orchestration), db.py (SQLite)
├─ clipper/                  pipeline, modules indépendants partagés par l'API et la CLI
│   download · prepare · transcribe (+ transcribe_worker) · audio_analysis · scenes · motion
│   highlights · llm · facecam · reframe · subtitles · render · metadata · doctor · config
├─ frontend/                 interface (HTML/CSS/JS sans étape de build, fonctionne hors ligne)
├─ fonts/                    polices des sous-titres
├─ tests/                    tests unitaires + test de bout en bout hors ligne
├─ workspace/<projet>/       source, audio, proxys, transcription, analyses (cache)
└─ output/<projet>/          clips finaux {date}_{titre}_{score}.mp4 + miniatures
```

## 9. Tests

```bat
.venv\Scripts\python -m pytest tests\test_units.py
.venv\Scripts\python -m pytest tests\test_e2e_offline.py -v
```

Le test de bout en bout génère une vidéo (mire + voix de synthèse Windows + explosions),
la traite entièrement, vérifie les clips 1080x1920, re-rend un clip en layout « suivi »
et exporte le ZIP.

## 10. Sécurité

Le serveur n'écoute que sur ta machine (`127.0.0.1`) et refuse les requêtes venant
d'autres sites web ouverts dans ton navigateur. Détails et bonnes pratiques dans
[SECURITY.md](SECURITY.md). Ne l'expose jamais sur Internet : il n'a pas de mot de passe.

## 11. Droits d'auteur

L'outil fonctionne sur n'importe quelle vidéo, mais **publier des extraits de vidéos d'autres
créateurs nécessite leur accord** (sinon : retrait, avertissement Content ID, voire
suspension du compte). Pour ton propre gameplay, aucun souci.
