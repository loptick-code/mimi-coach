# Bibliothèque de sources d'entraînement

Ce dossier conserve les plans de référence avant leur conversion en programmes chargés par l'application. Les sources ci-dessous ne sont pas encore importées dynamiquement par l'application.

## Plans disponibles

- Semi-marathon, 15 semaines : [`sources/semi_marathon_campus_coach_15_weeks.md`](sources/semi_marathon_campus_coach_15_weeks.md). Période du 9 novembre 2026 au 21 février 2027.
- Marathon, 16 semaines : les séances sont actuellement intégrées dans [`../scripts/import_marathon_16w.py`](../scripts/import_marathon_16w.py). Les allures de ce script ont déjà été décalées de 30 secondes par kilomètre pour les comptes ciblés, elles ne constituent donc pas encore une source de référence neutre.
- Autre plan Campus Coach : [`../scripts/import_campus_coach.py`](../scripts/import_campus_coach.py) importe un fichier `scratchpad/plan_marathon_8_nov_2026.md`, qui n'est pas présent dans le dépôt.
- 10 km : source à ajouter lorsqu'elle sera fournie.

## Adaptation des allures

Le plan semi fournit une plage EF de 4:39 à 5:09 min/km en semaine 1. Son allure EF de référence est le milieu de cette plage, soit 4:54 min/km. Pour une première version du calcul, l'utilisateur renseignerait son allure EF en min/km, puis toutes les allures chiffrées seraient multipliées par le même facteur :

```text
allure_cible_utilisateur = allure_cible_source × (EF_utilisateur / EF_référence_du_plan)
```

Les allures sont traitées en secondes par kilomètre. Par exemple, pour une EF utilisateur de 5:30/km, l'allure semi source de 3:33/km deviendrait environ 3:59/km. La progression EF déjà prévue au fil des semaines reste conservée par ce facteur fixe. Les durées des étapes chronométrées restent fixes ; leur distance estimée doit être recalculée avec la nouvelle allure. Pour une étape prescrite en distance, la distance reste fixe et sa durée estimée change. Les sprints indiqués « rapides » ou « aux sensations » restent sans allure cible.

Ce calcul applique une règle de proportion demandée par Valentin. Il ne constitue pas une prédiction physiologique. Avant de l'appliquer aux plans marathon, il faut retrouver leur allure EF de référence non modifiée, car au moins un import contient déjà un décalage de +30 s/km.
