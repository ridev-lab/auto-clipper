# Sécurité

## Signaler une faille

Pour signaler une faille, utilise **Settings → Security → Report a vulnerability** si le signalement privé est activé. Sinon, contacte le mainteneur en privé via les coordonnées de son profil GitHub. **Ne publie pas de détails exploitables dans une issue publique.**

Le projet ne fournit aucune authentification pour un serveur exposé à Internet.

## Protections du dépôt

- Dependabot : alertes sur les dépendances vulnérables + correctifs automatiques ;
- intégration continue : tests unitaires et recherche de secrets (gitleaks) à chaque envoi ;
- historique sans adresse e-mail personnelle (adresse masquée GitHub).

## Modèle de sécurité

Auto Clipper est un outil **local et mono-utilisateur** :

- le serveur écoute uniquement sur `127.0.0.1` (pas d'accès depuis le réseau) ;
- aucune clé d'API, aucun compte, aucun service cloud : rien à voler côté secrets ;
- les requêtes venant d'autres sites web ouverts dans le navigateur sont refusées
  (contrôle des en-têtes `Origin`, `Sec-Fetch-Site` et du type de contenu) ;
- le DNS rebinding est bloqué : seul un en-tête `Host` local est accepté ;
- en-têtes de sécurité sur toutes les réponses (Content-Security-Policy stricte,
  interdiction d'affichage en iframe, `nosniff`) ;
- les noms de fichiers et identifiants reçus par l'API sont validés (pas d'accès
  en dehors des dossiers du projet) ; ffmpeg et yt-dlp sont lancés sans shell ;
- la documentation interactive de l'API (`/docs`) est désactivée.

## Bonnes pratiques

- **N'expose pas le serveur sur Internet ni sur ton réseau local** (`server.host`
  doit rester `127.0.0.1`). Il n'a pas d'authentification.
- Si tu dois quand même l'ouvrir au réseau local, ajoute le nom d'hôte utilisé dans
  `server.allowed_hosts` de `config.yaml`, en connaissance de cause.
- Ne commite jamais de fichier `cookies.txt` (cookies YouTube), `.env` ou clé :
  ils sont exclus par `.gitignore`, mais vérifie avant chaque `git add`.
- Les dépendances sont surveillées par Dependabot (mises à jour hebdomadaires).
