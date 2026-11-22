# Bibliothèque de sources d'entraînement

Ce dossier conserve les plans de référence avant leur conversion en programmes chargés par l'application. Les sources ci-dessous ne sont pas encore importées dynamiquement par l'application.

## Plans disponibles

- 10 km, 13 semaines : [`sources/10km_campus_coach_13_weeks.md`](sources/10km_campus_coach_13_weeks.md). Source détaillée fournie par Valentin. Elle décrit notamment les séances clés, mais ne constitue pas encore un programme complet sélectionnable dans l'application.
- Semi-marathon, 15 semaines : [`sources/semi_marathon_campus_coach_15_weeks.md`](sources/semi_marathon_campus_coach_15_weeks.md). Période du 9 novembre 2026 au 21 février 2027.
- Marathon, 16 semaines : les séances sont actuellement intégrées dans [`../scripts/import_marathon_16w.py`](../scripts/import_marathon_16w.py). Les allures de ce script ont déjà été décalées de 30 secondes par kilomètre pour les comptes ciblés, elles ne constituent donc pas encore une source de référence neutre.
- Autre plan Campus Coach : [`../scripts/import_campus_coach.py`](../scripts/import_campus_coach.py) importe un fichier `scratchpad/plan_marathon_8_nov_2026.md`, qui n'est pas présent dans le dépôt.

## Règles d'adaptation des allures

Les plans 10 km et semi fournis ont une plage EF de référence de 4:39 à 5:09 min/km en semaine 1, soit un milieu de plage à 4:54 min/km. L'EF utilisateur est elle aussi saisie comme une plage de 30 secondes par kilomètre. Le facteur d'adaptation utilise le milieu de la plage utilisateur :

```text
allure_cible_utilisateur = allure_cible_source × (milieu_EF_utilisateur / milieu_EF_référence_du_plan)
```

Toutes les allures sont traitées en secondes par kilomètre. Les valeurs calculées sont des valeurs initiales modifiables par l'utilisateur. Une valeur modifiée manuellement ne doit pas être écrasée silencieusement lors d'un nouveau calcul. Les allures de course sont spécifiques à l'objectif choisi.

Cette règle est une adaptation proportionnelle demandée par Valentin, pas une prédiction physiologique. Les durées des étapes chronométrées restent fixes ; leur distance estimée doit être recalculée. Pour une étape prescrite en distance, la distance reste fixe et sa durée estimée change. Les sprints indiqués « rapides » ou « aux sensations » restent sans allure cible.

## Fréquence des séances

La version 4 séances conserve le programme source. La version 3 séances retire la séance n° 3 de chaque semaine, y compris la semaine de course. Si un texte saisi n'est pas numéroté, la troisième ligne est retirée lorsqu'il contient quatre séances.

## Limites connues

Le document 10 km n'inclut pas le détail complet de toutes les séances hebdomadaires, il n'est donc pas encore un programme complet sélectionnable. Avant de calculer les allures marathon, il faut retrouver une allure EF de référence non modifiée, car au moins un import contient déjà un décalage de +30 s/km.
