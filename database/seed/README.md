# No automatic seed

Import the packaged enriched POI CSV with db-import after schema initialization.
The import creates a READY version but does not activate it. Review with db-check
and explicitly use db-activate. No accounts or sample preferences are seeded.
