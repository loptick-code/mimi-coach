# Mi Coach — App d'entraînement running avec export Garmin

Application de course à pied pour plusieurs comptes. Chaque personne a les
mêmes droits, gère ses propres séances et connecte son propre compte Garmin.
Les séances peuvent être **exportées en un clic vers Garmin Connect**, puis
synchronisées sur la montre.

## Stack

- **Backend** : FastAPI + SQLModel (SQLite par défaut)
- **Frontend** : Jinja2 + HTMX + Tailwind (via CDN)
- **Garmin** : librairie [`python-garminconnect`](https://github.com/cyberjunky/python-garminconnect) (mobile SSO non-officiel)
- **Crypto** : tokens Garmin chiffrés Fernet (clé en variable d'env)

⚠️ **Limitation connue** : `python-garminconnect` est une réimplémentation
non-officielle du flow d'authentification mobile Garmin. Elle peut casser
quand Garmin change son auth. Le bouton "Télécharger JSON" sur chaque séance
permet de récupérer le payload pour debug ou usage futur.

## Démarrer en local

### 1) Pré-requis

- Python 3.12+
- `git` (optionnel)

### 2) Installation

```bash
cd "D:/Sport/App entrainement"
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

### 3) Configurer `.env`

Copie `.env.example` en `.env` et génère les secrets :

```bash
# Génère une clé Fernet
.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# Génère un secret de session
.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Édite ensuite `.env` :

```
FERNET_KEY=<la clé Fernet générée>
SESSION_SECRET=<le secret de session>
COACH_EMAIL=personne1@example.com
COACH_PASSWORD=<mot de passe du premier compte>
COACH_NAME=Compte principal
ATHLETE_EMAIL=personne2@example.com
ATHLETE_PASSWORD=<mot de passe du deuxième compte>
ATHLETE_NAME=Athlète 1
```

### 4) Lancer le serveur

```bash
.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

Ouvre <http://127.0.0.1:8000>. Connecte-toi avec l'un des comptes configurés.

### 5) Workflow

1. **Chaque personne** : ouvre `Programme` pour créer et modifier ses propres séances.
2. **Chaque personne** : ouvre `/settings/garmin` pour connecter son compte Garmin. Seul un token chiffré est stocké.
3. **Chaque personne** : consulte sa semaine, choisit une séance, puis clique sur **"Exporter vers Garmin"** pour l'envoyer à son compte Garmin Connect.

## Structure du projet

```
app/
├── main.py                  # FastAPI + lifespan
├── config.py                # Settings via .env
├── db.py / models.py        # SQLModel + SQLite
├── auth.py                  # Login + sessions cookie
├── crypto.py                # Fernet pour les tokens Garmin
├── seed.py                  # Crée les comptes configurés avec les mêmes droits
├── templates.py             # Jinja2 + filtres custom (pace, etc.)
├── routes/
│   ├── auth.py              # /login /logout
│   ├── sessions.py          # / /admin /admin/sessions/* /sessions/{id}
│   └── garmin.py            # /sessions/{id}/export, /settings/garmin
├── services/
│   ├── workout_builder.py   # Session DB -> RunningWorkout Garmin
│   ├── garmin_client.py     # Wrapper login + push + token encryption
│   └── fit_export.py        # Export JSON debug
└── templates/               # base, login, week, session_detail, session_form, admin, settings_garmin
tests/
└── test_workout_builder.py  # 11 tests sur la conversion Session->Workout
scripts/
└── reset_all_data.py        # Reset manuel avec aperçu et confirmation explicite
```

## Tests

```bash
.venv/Scripts/python.exe -m pytest -q
```

## Déploiement Vercel + Supabase

Le guide complet est dans [`DEPLOY.md`](DEPLOY.md). Variables à configurer côté
Vercel :

| Variable | Description |
|---|---|
| `FERNET_KEY` | Clé Fernet (génère-la une fois et garde-la) |
| `SESSION_SECRET` | Secret pour signer les cookies de session |
| `DATABASE_URL` | SQLite par défaut. Pour Postgres : `postgresql://...` (et installer `psycopg`) |
| `DEBUG` | `false` en production |
| `HTTPS_ONLY_COOKIES` | `true` en production |
| `COACH_EMAIL` / `COACH_PASSWORD` | Identifiants du premier compte, mêmes droits que les autres |
| `COACH_NAME` | Nom affiché pour le premier compte |
| `ATHLETE_EMAIL` / `ATHLETE_PASSWORD` | Identifiants du deuxième compte, mêmes droits |
| `ATHLETE_NAME` | Nom affiché pour le deuxième compte |

Les comptes configurés ont les mêmes droits. Chaque personne gère ses propres
séances et sa connexion Garmin.

## Calque Campus Coach

- **Vue semaine** : carte d'aperçu (activités / durée / distance) + liste de
  cards cliquables avec pill `EXPORTÉE`, durée, distance, difficulté en éclairs
- **Détail séance** : titre en gros caps + stats + Instructions avec étapes
  colorées (jaune = échauffement, rouge = intervalle, bleu = recup, rose =
  retour calme), bloc "Répéter × N" entouré, bouton sticky bas "Exporter vers
  Garmin"
- **Settings Garmin** : statut `CONNECTÉ en tant que <nom>` ou formulaire
  email + mdp pour lier le compte

## Choix techniques notables

### Pourquoi `python-garminconnect` et pas l'API officielle ?

L'API officielle Garmin Connect Developer Program (Training API) requiert
une approbation business. Pour un usage personnel en petit groupe, ça ne passe pas.
`python-garminconnect` ré-implémente le flow d'auth mobile et expose
`upload_running_workout(workout)` qui pousse une séance dans le calendrier
Garmin Connect comme si l'app mobile l'avait fait. La séance se synchronise
ensuite automatiquement vers la montre.

### Pourquoi Fernet ?

Le token de session Garmin (~1500 caractères, valide ~1 an) est sensible :
quiconque y a accès peut interagir avec le compte Garmin. On le chiffre
symétriquement avec Fernet. La clé reste en variable d'env (jamais en DB).

### Pourquoi pas de gen .FIT ?

L'API Python de `garmin-fit-sdk` (21.x) est principalement orientée décodage.
L'encodage workout direct .FIT n'est pas fourni de manière haut niveau.
Comme le push vers Garmin Connect via `upload_running_workout` est plus
fiable et pratique (Garmin transforme le JSON en .FIT côté serveur et le
sync sur la montre), c'est le seul chemin actif. Le bouton "JSON" télécharge
le payload Garmin Connect au cas où.

## TODO / améliorations possibles

- [ ] OAuth Garmin Connect Developer Program (officiel) si on veut pérenniser
- [ ] Plusieurs programmes par compte
- [ ] Modèles de séances réutilisables ("Séance VMA classique")
- [ ] Partage de programmes entre comptes
- [ ] Vraie gen .FIT via `fit-tool` pour fallback fonctionnel
- [ ] Support 2FA Garmin
