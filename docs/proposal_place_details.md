# Reviewing proposed places

Chat swap proposals show the source POI category and area beneath each added or
removed place. Selecting the place or “View place details” opens the shared POI
sheet with its description, category, visit duration, contact/map information,
available metadata and opening-hours checks for the proposed date and start time.
Adjusted activities use the same detail action. English and Korean labels are
available. Failed detail retrieval displays an error instead of indefinite loading.

The chat API supplies the dataset version from the proposal's base optimization
audit. Both the category preview and full sheet use that version, including when
reading older chat messages after the active catalogue changes. Place queries
share the existing versioned query cache. Opening details does not cast a vote or
apply a change; the existing proposal approval and hard-validation policies remain.

The API regression checks detail lookup for both added and removed POIs, pinned
dataset identity and unchanged proposal votes. No external provider or live mobile
interaction is claimed by this regression; frontend integration is checked with
TypeScript and the production build.
