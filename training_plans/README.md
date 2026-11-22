# Bibliothèque de sources d'entraînement

Mi Coach associe maintenant chaque objectif à un plan de référence :

- 10 km : `sources/10km_campus_coach_13_weeks.md`
- Semi-marathon : `sources/semi_marathon_campus_coach_15_weeks.md`
- Marathon : les étapes complètes du plan existant dans `scripts/import_marathon_16w.py`

## Placement dans le calendrier

La date de course est obligatoire. Le générateur place la dernière semaine du modèle autour de cette date, puis remonte semaine par semaine. Ainsi, la semaine finale du plan reste toujours la semaine de course, même si la date change.

Mi Coach ne crée que les séances à faire à partir d'aujourd'hui. Les séances déjà passées ne sont pas recréées. En mode 4 séances, les séances 1 à 4 sont conservées. En mode 3 séances, la séance n° 3 est retirée chaque semaine, y compris la semaine de course.

## Allures

L'EF utilisateur est une plage obligatoire de 30 secondes par kilomètre. Le milieu de la plage sert à calculer le facteur d'adaptation :

```text
allure_cible_utilisateur = allure_cible_source × (milieu_EF_utilisateur / milieu_EF_référence_du_plan)
```

La borne opposée est complétée automatiquement dans l'interface : saisir la borne rapide ajoute 30 secondes à la borne lente, et saisir la borne lente retire 30 secondes à la borne rapide. Les valeurs restent modifiables dans les paramètres.

Les allures manuellement personnalisées sont appliquées aux nouvelles séances générées et ne sont pas écrasées par un recalcul automatique.

Les allures spécifiques aux séances restent celles de leur semaine source. Le
ressenti n'écrase pas ces références : il ajoute seulement un ajustement
progressif pour les séances futures non validées. « Trop facile » accélère de
2 %, « trop dur » ralentit de 2 %, et « parfait » ne change rien. L'ajustement reste cumulatif sans plafond; il doit donc être suivi par l'athlète.

## Limites connues

Le document 10 km fourni détaille surtout les séances clés 2 et 4 et confirme que les séances 1 et 3 sont des EF jumelles sans préciser toutes leurs durées. Le générateur utilise donc une durée EF conservative et documentée pour ces deux séances, jusqu'à réception d'une version complète du plan.

Le plan marathon historique contient des allures déjà ajustées pour sa source d'origine. Il est utilisable comme modèle calé sur la date de course, mais sa référence d'allure devra être révisée si une source marathon neutre est fournie.
