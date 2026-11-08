# Déployer Mi Coach sur Vercel + Supabase (100% gratuit)

Guide étape par étape pour déployer l'application et configurer plusieurs comptes aux mêmes droits.

---

## 1) Préparer le repo Git

Depuis ton terminal, dans `D:\Sport\App entrainement` :

```bash
git init
git add .
git commit -m "Mi Coach - initial deploy"
```

Le `.gitignore` exclut `data/`, `.env`, `.venv/` (les secrets et la DB locale ne partent pas).

## 2) Pousser sur GitHub

1. Va sur https://github.com/new
2. **Nom** : `mimi-coach`
3. Choisis la visibilité du dépôt. Un dépôt public expose son code et tout son historique à tout le monde. Ne committe jamais de secrets ni de données personnelles.
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
   - **Region** : `Europe (Frankfurt)` ou `France` (la plus proche de toi)
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
| `COACH_EMAIL` | email du premier compte |
| `COACH_PASSWORD` | mot de passe fort du premier compte |
| `COACH_NAME` | nom affiché pour le premier compte |
| `ATHLETE_EMAIL` | email du deuxième compte |
| `ATHLETE_PASSWORD` | mot de passe fort du deuxième compte |
| `ATHLETE_NAME` | nom affiché pour le deuxième compte |
| `ATHLETE2_EMAIL` / `ATHLETE2_PASSWORD` | compte supplémentaire optionnel, les deux valeurs doivent être définies |
| `ATHLETE2_NAME` | nom affiché pour le compte supplémentaire |

6. **Deploy** — Vercel build le projet (~2-3 min)
7. Une fois "Ready", tu obtiens une URL type `https://mimi-coach-xxx.vercel.app`
8. Ouvre-la, tu devrais voir la page de login Mi Coach 🎉

## 6) Importer les séances Campus Coach en prod

L'app démarre avec une DB vide et crée les comptes configurés. Tous les comptes ont les mêmes droits et ne peuvent gérer que leurs propres séances. Pour importer les séances dans la DB Supabase :

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

## Réinitialiser les comptes et toutes les données

Cette opération supprime les comptes, séances, étapes, ressentis et connexions Garmin enregistrés. Elle recrée ensuite les comptes configurés dans les variables d'environnement. Les identifiants de compte gardent leurs adresses et mots de passe configurés. Chaque personne devra reconnecter Garmin.

1. Fais une sauvegarde Supabase si tu veux garder un retour arrière.
2. Récupère la variable `DATABASE_URL` de Vercel/Supabase dans ton `.env` local. Ne la partage pas et ne la committe pas.
3. Depuis la racine du dépôt, affiche le nombre d'éléments qui seront supprimés :
   ```bash
   python -m scripts.reset_all_data
   ```
4. Vérifie que la sortie indique `postgresql` et que les comptes et volumes sont corrects. Puis exécute le reset :
   ```bash
   python -m scripts.reset_all_data --confirm-reset RESET-MI-COACH
   ```

Sans la phrase de confirmation exacte, le script n'efface rien. Il refuse aussi les emails `example.com`, les mots de passe par défaut et les emails de compte dupliqués. Mets à jour les variables d'environnement avant le reset si tu veux de nouveaux identifiants.

**Option B — depuis le dashboard Supabase** : tu peux aussi inspecter / éditer les données via l'onglet "Table Editor" pour faire des modifs ad-hoc.

## 7) Configurer les comptes

Chaque compte a les mêmes droits et ne voit que ses propres séances. Les identifiants viennent des variables `COACH_EMAIL` / `COACH_PASSWORD`, `ATHLETE_EMAIL` / `ATHLETE_PASSWORD` et, si besoin, `ATHLETE2_EMAIL` / `ATHLETE2_PASSWORD`.

À la première connexion, chaque personne peut ouvrir **Programme**, puis connecter son propre Garmin depuis **Compte → Garmin Connect**. Le mot de passe Garmin n'est pas stocké ; seul un token chiffré est conservé.

## 8) Maintenance

Quand tu modifies le code en local et veux pousser en prod :

```bash
git add .
git commit -m "amélioration X"
git push
```

Vercel redéploie automatiquement à chaque push sur `main`. La DB Supabase n'est pas touchée par les redéploiements (elle vit indépendamment).

Pour ajouter de nouvelles séances : utilise l'interface **Programme** (`/admin`) ou modifie `scripts/import_campus_coach.py`, puis relance-le localement avec `DATABASE_URL` pointant sur Supabase.

---

## Coûts

| Service | Free tier | Limite | Suffisant pour plusieurs comptes ? |
|---|---|---|---|
| **Vercel Hobby** | Illimité (100 GB bande passante) | Cold start ~1s, 300s max par fonction. Cette app est configurée à 30s dans `vercel.json` | ✅ Largement |
| **Supabase Free** | 500 MB DB, 5 GB bande passante / mois | DB pause si pas d'activité 7 jours | ✅ Largement (notre DB ~1 MB) |

**Tu paies 0€/mois** tant que tu restes dans ces limites.

---

## Limites connues à savoir

### ⚠️ Cold start

Si l'app n'a pas été utilisée depuis ~10 min, la première requête prendra **1-2s en plus** le temps que Vercel démarre la fonction. Acceptable.

### ⚠️ DB pause Supabase (free tier)

Si **personne** n'utilise l'app pendant **7 jours**, Supabase met la DB en pause. La première requête ensuite la réveille (~30s). Pour éviter ça, ouvre l'app au moins une fois par semaine, ou passe en Pro (25$/mo) si les comptes sont rarement utilisés.

### ⚠️ Timeout configuré à 30s

Le bouton "Exporter Garmin" peut prendre 5-10s à cause des retries anti-rate-limit. Le fichier `vercel.json` limite cette fonction à 30s. La documentation Vercel actuelle indique un maximum de 300s sur le plan Hobby, mais le plafond appliqué à cette app reste 30s. Si Garmin rate-limite agressivement ou si l'export dépasse 30s, le push peut échouer ; la lib retentera au prochain clic.

### ⚠️ API Garmin non-officielle

`python-garminconnect` peut casser à chaque update Garmin. Si l'export ne marche plus, mets à jour la lib (`pip install -U garminconnect`) et redéploie. Le bouton "JSON" sur chaque séance permet toujours de récupérer le payload pour debug.

---

## Sécurité

- Un dépôt public expose son code et tout son historique à tout le monde. Garde-le privé si tu ne veux pas rendre les programmes ou informations personnelles accessibles.
- Ne committe jamais `.env` ni aucun mot de passe, token ou secret. Vérifie aussi l'historique Git avant de changer la visibilité.
- Si `FERNET_KEY` a été exposée, remplace-la et reconnecte chaque compte Garmin.
- 🔐 Le mot de passe Supabase reste dans Vercel uniquement (jamais dans le code)
