# Explicit migrations

001 is ../schema.sql, the supplied baseline. 002_recommendation_scale.sql aligns
result scores with the existing 1-5 recommendation scale and permits undefined
fields to be SQL NULL. db-init applies both atomically on an empty schema.
Existing schemas require explicit db-migrate --directory database.

002 refuses historical result rows. Back up and review an existing installation
before applying it; normalized historic results cannot safely be guessed or
silently rescaled. The cleanup never connects to or migrates a shared database.
