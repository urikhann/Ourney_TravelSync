# Compatibility guide

Use the guide for the component you are configuring:

- [Application and installation](README.md)
- [Docker services](DOCKER.md)
- [Service contracts](contracts/README.md)
- [Database migrations](database/migrations/README.md)
- [E5 score semantics](docs/e5_changes.md)
- [Planning rules](docs/agreed_planning_implementation_20261117.md)

Dataset versions and saved recommendation inputs are immutable. Import data as a
new version and activate it explicitly. Apply the migrations for the database
schema you use; the API and standalone recommendation package have separate schemas.
