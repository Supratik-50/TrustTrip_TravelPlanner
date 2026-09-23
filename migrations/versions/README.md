# Deprecation Notice: migrations/versions/

All versioned database migrations in TrustTrip are authoritatively located in the top-level `migrations/` directory:
- `migrations/004_travel_intelligence.sql`
- `migrations/005_evidence_revision_lifecycle.sql`
- `migrations/006_research_and_rag.sql`

The `migrations/versions/` subdirectory is retained for legacy compatibility reference only and contains no active or executable migration files. All migration runners and automated test fixtures load exclusively from `migrations/*.sql`.
