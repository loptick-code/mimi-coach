# Déployer Mimi Coach sur Vercel + Supabase (100% gratuit)

Guide étape par étape pour déployer ton app et donner accès à ta copine.

---

## 1) Préparer le repo Git

Depuis ton terminal, dans `D:\Sport\App entrainement` :

```bash
git init
git add .
git commit -m "Mimi Coach - initial deploy"
```

Le `.gitignore` exclut `data/`, `.env`, `.venv/` (les secrets et la DB locale ne partent pas).

## 2) Pousser sur GitHub (repo privé)

1. Va sur https://github.com/new
2. **Nom** : `mimi-coach`
3. ⚠️ **Privé** (important : il y a la logique de ton plan d'entrainement)
4. Crée le repo
5. Suis les instructions GitHub :

```bash
git remote add origin https://github.com/<ton-pseudo>/mimi-coach.git
git branch -M main
git push -u origin main
```

## 3) Créer la base Postgres sur Supabase

1. Connecte-toi sur https://supabase.com (compte gratuit)
2. **New Project** :
   - **Name** : `mimi-coach`
   - **Database password** : génère un mdp fort (note-le quelque part, tu vas en avoir besoin)
   - **Region** : `Europe (Frankfurt)` ou `France` (le plus proche de ta copine)
   - **Plan** : Free
3. Attends ~2 min que le projet soit provisionné
4. Récupère la connection string :
   - Onglet **Project Settings** (icône engrenage) → **Database** → **Connection string**
   - Choisis **Connection pooling** (mode "Transaction") — important pour Vercel serverless
   - Copie l'URI qui ressemble à :
     ```
     postgresql://postgres.xxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-eu-central-1.pooler.supabase.com:5432/postgres
     ```
   - Remplace `[YOUR-PASSWORD]` par le mot de passe que tu as créé à l'étape 2

> 💡 **Pourquoi le pooler** : Vercel lance des fonctions serverless qui ouvrent/ferment des connexions à chaque requête. Le pooler de Supabase gère ça proprement. Si tu prends la connection string directe (port 5432 sans `pooler`), Vercel saturera vite la DB.

## 4) Générer les secrets de production

⚠️ **Ne réutilise JAMAIS tes clés de dev en prod.**

Génère deux nouvelles clés :

```bash
.venv/Scripts/python.exe -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())"
.venv/Scripts/python.exe -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(32))"
```

Note les deux valeurs.

## 5) Déployer sur Vercel

1. Va sur https://vercel.com et crée un compte (login GitHub recommandé)
2. **Add New… → Project** → choisis `mimi-coach` (Vercel détecte ton repo)
3. **Framework Preset** : `Other` (Vercel auto-detect le `vercel.json`)
4. **Root Directory** : `.` (laisser tel quel)
5. **Environment Variables** : ajoute ces variables (clique "Add" pour chacune) :

| Nom | Valeur |
|---|---|
| `DATABASE_URL` | la connection string Supabase de l'étape 3 |
| `FERNET_KEY` | la clé Fernet générée à l'étape 4 |
| `SESSION_SECRET` | le secret session généré à l'étape 4 |
| `HTTPS_ONLY_COOKIES` | `true` |
| `DEBUG` | `false` |
| `COACH_EMAIL` | ton email (ex: `valentin@example.com`) |
| `COACH_PASSWORD` | un **mot de passe fort** pour ton compte coach |
| `ATHLETE_EMAIL` | l'email de ta copine |
| `ATHLETE_PASSWORD` | un mot de passe à lui donner (qu'elle pourra changer si tu ajoutes la fonction plus tard) |
| `ATHLETE_NAME` | son prénom (ex: `Marie`) |

6. **Deploy** — Vercel build le projet (~2-3 min)
7. Une fois "Ready", tu obtiens une URL type `https://mimi-coach-xxx.vercel.app`
8. Ouvre-la — tu devrais voir la page de login Mimi Coach 🎉

## 6) Importer les séances Campus Coach en prod

L'app démarre avec une DB vide (juste les 2 users seedés au lifespan). Pour importer les 24 séances dans la DB Supabase :

**Option A — depuis ton local** (recommandé, le plus simple) :

1. Modifie temporairement ton `.env` local en y mettant la connection string Supabase à la place de l'URL SQLite :
   ```
   DATABASE_URL=postgresql://postgres.xxxx:password@...pooler.supabase.com:5432/postgres
   ```

2. Lance les scripts :
   ```bash
   .venv/Scripts/python.exe -m scripts.migrate_v2
   .venv/Scripts/python.exe -m scripts.import_campus_coach
   .venv/Scripts/python.exe -m scripts.shift_paces
   ```

3. **Remets ton `.env` local sur SQLite** (`DATABASE_URL=sqlite:///./data/app.db`) sinon ton dev local pointera sur la prod.

**Option B — depuis le dashboard Supabase** : tu peux aussi inspecter / éditer les données via l'onglet "Table Editor" pour faire des modifs ad-hoc.

## 7) Donner accès à ta copine

Envoie-lui :
- 🔗 **L'URL** : `https://mimi-coach-xxx.vercel.app`
- 📧 **Email** : ce que tu as mis dans `ATHLETE_EMAIL`
- 🔑 **Mot de passe** : ce que tu as mis dans `ATHLETE_PASSWORD`

À sa première connexion, elle va :
1. Voir le programme dans **Séances**
2. Aller dans **Compte → Garmin Connect** et entrer son email/mdp Garmin (token chiffré stocké, le mdp jamais)
3. Sur chaque séance, cliquer **Exporter** pour pousser sur sa Garmin
4. Après chaque séance, cliquer **Trop facile / Parfait / Trop dur** pour auto-ajuster les allures futures

## 8) Maintenance

Quand tu modifies le code en local et veux pousser en prod :

```bash
git add .
git commit -m "amélioration X"
git push
```

Vercel redéploie automatiquement à chaque push sur `main`. La DB Supabase n'est pas touchée par les redéploiements (elle vit indépendamment).

Pour ajouter de nouvelles séances : utilise l'interface admin (`/admin`) directement en ligne, ou modifie `scripts/import_campus_coach.py` puis relance localement avec `DATABASE_URL` pointant sur Supabase.

---

## Coûts

| Service | Free tier | Limite | Suffisant pour 2 users ? |
|---|---|---|---|
| **Vercel Hobby** | Illimité (100 GB bande passante) | Cold start ~1s, 300s max par fonction. Cette app est configurée à 30s dans `vercel.json` | ✅ Largement |
| **Supabase Free** | 500 MB DB, 5 GB bande passante / mois | DB pause si pas d'activité 7 jours | ✅ Largement (notre DB ~1 MB) |

**Tu paies 0€/mois** tant que tu restes dans ces limites.

---

## Limites connues à savoir

### ⚠️ Cold start

Si l'app n'a pas été utilisée depuis ~10 min, la première requête prendra **1-2s en plus** le temps que Vercel démarre la fonction. Acceptable.

### ⚠️ DB pause Supabase (free tier)

Si **personne** n'utilise l'app pendant **7 jours**, Supabase met la DB en pause. La première requête ensuite la réveille (~30s). Pour éviter ça, ouvre l'app au moins une fois par semaine, ou upgrade en Pro (25$/mo) si ta copine utilise très peu.

### ⚠️ Timeout configuré à 30s

Le bouton "Exporter Garmin" peut prendre 5-10s à cause des retries anti-rate-limit. Le fichier `vercel.json` limite cette fonction à 30s. La documentation Vercel actuelle indique un maximum de 300s sur le plan Hobby, mais le plafond appliqué à cette app reste 30s. Si Garmin rate-limite agressivement ou si l'export dépasse 30s, le push peut échouer ; la lib retentera au prochain clic.

### ⚠️ API Garmin non-officielle

`python-garminconnect` peut casser à chaque update Garmin. Si l'export ne marche plus, mets à jour la lib (`pip install -U garminconnect`) et redéploie. Le bouton "JSON" sur chaque séance permet toujours de récupérer le payload pour debug.

---

## Sécurité

- 🔐 Le repo GitHub doit rester **privé**
- 🔐 Ne commit **jamais** ton `.env` (déjà dans `.gitignore`, vérifie avec `git status` avant le push)
- 🔐 Si tu suspectes une fuite de `FERNET_KEY`, regénères-en une nouvelle ET reconnecte le compte Garmin depuis l'app
- 🔐 Le mot de passe Supabase reste dans Vercel uniquement (jamais dans le code)
