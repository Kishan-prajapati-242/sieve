-- Sieve pre-0016 backup. Restore with:
--   psql $NEON_URL -f pre-0016-backup.sql
BEGIN;
-- users: 4 rows
INSERT INTO users (id,email,password_hash,created_at,email_verified_at) VALUES (3,'dontmissuse0242@gmail.com',NULL,'2026-08-16 04:41:12.782453+00:00','2026-08-16 04:41:12.782453+00:00') ON CONFLICT DO NOTHING;
INSERT INTO users (id,email,password_hash,created_at,email_verified_at) VALUES (4,'kishansp242@gmail.com',NULL,'2026-08-16 04:43:17.580206+00:00','2026-08-16 04:43:17.580206+00:00') ON CONFLICT DO NOTHING;
INSERT INTO users (id,email,password_hash,created_at,email_verified_at) VALUES (1,'prajapati.kish@northeastern.edu','$argon2id$v=19$m=65536,t=3,p=4$Vg7u6x1gyKbPgSDvi8qV1g$NnSY0Lst6Qbet/C7ShNZikAT4hdYm09YzDuGhJ6HNr0','2026-08-16 04:36:57.668631+00:00','2026-08-16 06:19:50.520930+00:00') ON CONFLICT DO NOTHING;
INSERT INTO users (id,email,password_hash,created_at,email_verified_at) VALUES (6,'pkishans242@gmail.com','$argon2id$v=19$m=65536,t=3,p=4$eoK+8RD0phPNTOMkspS84g$oujWqXkW0O4wNkYEoYhIGJsz9zPaJjC9UDgmySpC3dg','2026-08-16 05:38:55.233025+00:00','2026-08-16 06:19:50.520930+00:00') ON CONFLICT DO NOTHING;
-- collections: 6 rows
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (1,'gatekeepnt',NULL,'2026-08-16 04:42:23.452618+00:00',3) ON CONFLICT DO NOTHING;
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (2,'gatekeepnt bruh',NULL,'2026-08-16 04:43:26.330859+00:00',4) ON CONFLICT DO NOTHING;
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (3,'test=1',NULL,'2026-08-17 18:37:37.641023+00:00',3) ON CONFLICT DO NOTHING;
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (4,'test-2',NULL,'2026-08-17 18:37:39.861735+00:00',3) ON CONFLICT DO NOTHING;
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (5,'test-3',NULL,'2026-08-17 18:37:42.132555+00:00',3) ON CONFLICT DO NOTHING;
INSERT INTO collections (id,name,question,created_at,user_id) VALUES (6,'test-4','bruh','2026-08-17 18:37:45.807057+00:00',3) ON CONFLICT DO NOTHING;
-- screenings: 4 rows
INSERT INTO screenings (collection_id,paper_id,decision,note,decided_at) VALUES (1,22875,'include',NULL,'2026-08-16 04:42:48.575324+00:00') ON CONFLICT DO NOTHING;
INSERT INTO screenings (collection_id,paper_id,decision,note,decided_at) VALUES (1,22682,'exclude',NULL,'2026-08-16 04:42:52.213638+00:00') ON CONFLICT DO NOTHING;
INSERT INTO screenings (collection_id,paper_id,decision,note,decided_at) VALUES (2,589,'exclude',NULL,'2026-08-16 04:43:42.672998+00:00') ON CONFLICT DO NOTHING;
INSERT INTO screenings (collection_id,paper_id,decision,note,decided_at) VALUES (2,25100,'maybe',NULL,'2026-08-16 04:43:44.435481+00:00') ON CONFLICT DO NOTHING;
COMMIT;
