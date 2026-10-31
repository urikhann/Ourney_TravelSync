# Explicit migrations

001 is ../schema.sql, the supplied baseline. 002_recommendation_scale.sql aligns
result scores with the existing 1-5 recommendation scale and permits undefined
fields to be SQL NULL. db-init applies all migrations atomically on an empty schema.
Existing schemas require explicit db-migrate --directory database.

002 refuses historical result rows. Back up and review an existing installation
before applying it; normalized historic results cannot safely be guessed or
silently rescaled. The cleanup never connects to or migrates a shared database.

003_enriched_poi_metadata.sql adds JSONB storage for the 43 enriched source
fields. db-init includes it for new schemas; existing schemas use db-migrate.
