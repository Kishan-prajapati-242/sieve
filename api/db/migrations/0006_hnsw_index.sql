-- Phase 2: the HNSW index 0002 promised, deferred until after the bulk load
-- and embedding backfill so 197K inserts didn't each pay graph maintenance.
--
-- Both SETs are session-scoped: they tune this build and die with the
-- migration's connection.
--
-- maintenance_work_mem: pgvector builds the graph in memory sized by this
-- setting and degrades badly once the graph no longer fits (default 64MB;
-- this graph needs ~450MB). Parallel workers share ONE such area, not one
-- each — verified 2026-07-31 against pgvector v0.8.5 source (hnswbuild.c:
-- esthnswarea = maintenance_work_mem * 1024, allocated once, mapped by all
-- workers) and by a live 30K probe build. The area is a single POSIX shm
-- segment allocated UP FRONT, so the postgres container's /dev/shm must
-- exceed it: shm_size 2g in docker-compose.yml (the 64MB container default
-- kills the build with "could not resize shared memory segment").
SET maintenance_work_mem = '1GB';
SET max_parallel_maintenance_workers = 2;

CREATE INDEX papers_embed_idx ON papers
    USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);
