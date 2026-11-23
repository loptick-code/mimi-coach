# MCP Mi Coach

Mi Coach expose un serveur MCP distant à l'adresse :

```text
https://<ton-deploiement-mi-coach>/mcp
```

## Connexion OAuth recommandée

Les clients MCP compatibles découvrent normalement les métadonnées OAuth depuis :

```text
https://<ton-deploiement-mi-coach>/.well-known/oauth-protected-resource/mcp
```

Ils peuvent ensuite enregistrer leur client, ouvrir la page de consentement Mi Coach et obtenir un token OAuth 2.1 protégé par PKCE. L'athlète se connecte avec son compte Mi Coach dans le navigateur, puis choisit les permissions accordées.

Les permissions sont séparées :

- `profile:read`, lire le profil et les allures ;
- `plan:read`, lire le programme et les séances ;
- `activity:read`, lire les ressentis et activités ;
- `plan:write`, proposer et appliquer des changements de programme.

## Outils disponibles

Tous les tokens peuvent utiliser :

- `get_my_profile`, pour lire le profil, les allures et le plan actif ;
- `get_my_training_plan`, pour lire les séances, les étapes, les ressentis et les informations Garmin disponibles.

Un token OAuth qui possède `plan:write` peut aussi utiliser :

- `preview_program_change`, pour valider un fichier Markdown et créer une proposition ;
- `apply_program_change`, pour appliquer une proposition après confirmation explicite.

## Écriture sécurisée

Une modification ne se fait jamais en un seul appel :

1. l'assistant appelle `preview_program_change` ;
2. Mi Coach valide le format Markdown et calcule les changements ;
3. Mi Coach crée une proposition temporaire ;
4. l'athlète confirme explicitement la proposition dans l'assistant ;
5. l'assistant appelle `apply_program_change` avec `confirm=true` et le `proposal_id` ;
6. Mi Coach vérifie que le programme n'a pas changé depuis l'aperçu ;
7. Mi Coach applique exactement la proposition, ou refuse si elle a expiré ou est devenue obsolète.

Les séances passées ou validées restent protégées. Les séances futures non validées ne sont remplacées que si la proposition l'autorise explicitement.

## Token manuel de lecture

Pour les clients MCP qui ne proposent pas encore la connexion OAuth :

1. ouvre **Connecter une IA** dans Mi Coach ;
2. crée un token personnel ;
3. copie-le immédiatement, car il n'est affiché qu'une seule fois ;
4. configure l'URL `/mcp` et le token dans ton client MCP ;
5. révoque le token depuis Mi Coach si nécessaire.

Les tokens manuels sont limités à la lecture. Ils ne peuvent pas modifier le programme.

## Sécurité

- Chaque token est associé à un seul compte Mi Coach.
- Les tokens sont stockés uniquement sous forme de hash.
- Les tokens OAuth expirent après une heure.
- Les codes d'autorisation expirent après cinq minutes et ne peuvent être utilisés qu'une fois.
- Les propositions de modification expirent après dix minutes.
- Les requêtes navigateur cross-origin sont rejetées par le serveur MCP.
- L'accès à Garmin n'est pas automatiquement élargi par MCP.
