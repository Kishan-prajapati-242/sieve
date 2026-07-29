-- Extensions before any tables exist:
--   pg_trgm  trigram similarity, used by the dedup cascade (Phase 3)
--   vector   pgvector, halfvec embeddings + HNSW index (Phase 2)
-- Both are trusted extensions (pg_trgm since PG13, pgvector since 0.5), so
-- the app role can create them without superuser; the compose user is
-- superuser anyway. SCHEMA public is pinned so the types resolve regardless
-- of the migrating role's search_path — Supabase, for one, pre-installs
-- extensions into a separate "extensions" schema, where IF NOT EXISTS would
-- silently no-op and later halfvec(384) columns would fail to resolve.

CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public;
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;
