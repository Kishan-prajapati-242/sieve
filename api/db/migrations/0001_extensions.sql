-- Extensions before any tables exist:
--   pg_trgm  trigram similarity, used by the dedup cascade (Phase 3)
--   vector   pgvector, halfvec embeddings + HNSW index (Phase 2)
-- Both ship with the pgvector/pgvector:pg16 image; CREATE EXTENSION needs a
-- superuser, which the compose POSTGRES_USER is.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;
