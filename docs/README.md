# TravelSync documentation

| Task | Guide |
|---|---|
| Use the application | [Application guide](application_guide.md) |
| Understand scheduling rules | [Planning rules](planning_rules.md), [meals](dynamic_meals.md), [optional evening visits](evening_choices.txt) |
| Explore a saved plan | [Itinerary map](itinerary_map.md) |
| Review or change a plan | [Chat replacements](localized_itinerary_repair.md), [confirmation and scopes](conversation_state_and_replanning.md), [proposal details](proposal_place_details.md) |
| Manage protected visits | [Place locks](place_locks_and_planning_recovery.md), [activity removal](removal_repair.md) |
| Install or operate services | [Docker setup](../DOCKER.md), [production deployment](../deploy/DEPLOY.md), [snapshot setup](deployment_snapshot.md) |
| Select a catalogue | [Dataset snapshots](dataset_snapshots.md) |
| Understand recommendations | [E5 scoring](e5_hybrid.md), [score compatibility](e5_changes.md), [tie handling](tie_policy.md) |
| Understand planning internals | [Architecture](architecture.md), [candidate retrieval](itinerary_candidate_retrieval.md), [optimizer](optimizer.md), [API integration](optimizer_app_integration.md), [place identity](duplicate_place_identity.md) |
| Configure routes | [Planning-data service](../services/planning_data/README.md), [quota policy](routing_quota_guard.txt) |
| Collect or import data | [Data workflows](data_workflows.md), [enrichment](poi_enrichment.md), [metadata](poi_metadata.md), [normalized hours](normalized_operating_hours.md) |
| Work with schemas | [Service contracts](../contracts/README.md), [database contracts](../database/CONTRACTS.md), [migrations](../database/migrations/README.md) |
| Run checks | [Testing](optimizer_verification.md), [CI workflows](../.github/workflows) |

The application API and standalone recommendation package have separate database
schemas. Follow the guide for the component being configured. Local generated
reports, caches, model downloads and database backups belong outside tracked
source; previous implementation reports remain available in Git history.
