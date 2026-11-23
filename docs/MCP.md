# MCP Mi Coach

Mi Coach expose une première version d'un serveur MCP distant en lecture seule à l'adresse :

```text
https://<ton-deploiement-mi-coach>/mcp
```

## Fonctionnalités actuelles

Le serveur expose deux outils :

- `get_my_profile`, pour lire le profil, les allures et le plan actif ;
- `get_my_training_plan`, pour lire les séances, les étapes, les ressentis et les informations Garmin disponibles.

Les données sont toujours limitées au compte associé au token utilisé.

## Connexion

1. Ouvre **Connecter une IA** dans Mi Coach.
2. Crée un token pour ton assistant.
3. Copie-le immédiatement, car il n'est affiché qu'une seule fois.
4. Configure l'URL `/mcp` et le token dans ton client MCP.
5. Révoque le token depuis Mi Coach si nécessaire.

Cette version utilise un bearer token personnel afin de valider la surface MCP. Le token n'est jamais stocké en clair. Une connexion OAuth 2.1 avec autorisation dans le navigateur sera ajoutée avant d'ouvrir les fonctions d'écriture.

## Limites volontaires

Cette version ne peut pas :

- modifier le programme ;
- modifier le profil ;
- valider une séance ;
- exporter une séance vers Garmin ;
- consulter le compte d'un autre athlète.

Les fonctions d'écriture devront passer par un aperçu, une confirmation explicite et un identifiant de proposition à usage unique.
