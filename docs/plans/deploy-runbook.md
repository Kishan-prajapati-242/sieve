# Deploy runbook

Everything below is built and configured. **Four accounts are needed and only
Kishan can create them** — I have no way to sign up on his behalf, and two of
them (email delivery, database) are hard blockers for "OTP to real inboxes"
and "a live URL".

---

## What the sizing decided

Measured, not assumed (`bench/deploy_sizing.py`, 2026-08-15):

| piece | size | needed to serve queries? |
|---|---|---|
| papers heap | 276 MB | yes |
| `papers_embed_idx` (HNSW) | 204 MB | yes — vector mode |
| `papers_fts_idx` (GIN) | 83 MB | yes — bm25 mode |
| `papers_title_year_idx` | 19 MB | yes — year filter |
| dedup-only indexes | 49 MB | **no** — cascade runs before deploy |
| `source_records` | 820 MB | **no** — ingestion provenance |
| **total database** | **2,067 MB** | |
| **serving footprint** | **586 MB** | **3,354 bytes per paper** |

**586 MB does not fit any 0.5 GB free tier, so the deployment is a subset.**
`bench/export_deploy_subset.py` keeps whole topic buckets — the demo-query
buckets first, then largest-first until the budget is spent:

    128,078 papers · 69.9% of the corpus · ~410 MB

Whole buckets rather than a random sample because the demo queries depend on
specific papers being present; a random 70% would quietly drop one arm's
unique hits and the fusion demo would stop demonstrating fusion. A plain
largest-first pass dropped `text-simplification`, which is one of the three
demo queries — hence the `--require` list.

**The deployed site reports its own size.** The hero reads `/api/stats`, so a
128k deployment says 128,078 rather than inheriting 183,167. That is why that
change mattered.

---

## Accounts Kishan needs to create

| # | service | what for | free tier | blocker? |
|---|---|---|---|---|
| 1 | **Neon** | Postgres + pgvector | 0.5 GB — the subset is sized for it | **yes** |
| 2 | **Render** | the FastAPI service | free web service, sleeps when idle | **yes** |
| 3 | **Vercel** | the frontend | generous; he already uses it | **yes** |
| 4 | **Resend** (or Brevo, or a Gmail app password) | real OTP email | Resend ~100/day | **yes, for real inboxes** |

**On free tiers generally:** these limits change and I cannot verify them from
here. I sized the subset against 0.5 GB because that is what Neon and Supabase
have advertised; if the limit turns out different, change `--papers` and
re-export — nothing else moves.

**Worth checking before settling:** some providers advertise larger free
Postgres (Aiven has offered ~5 GB). If one of them supports the `vector`
extension and genuinely fits 586 MB, **the full corpus deploys with no subset
at all** and the export step disappears. I have not verified any of them, so
the plan above assumes the constrained case.

---

## Steps

### 1. Neon — database

Create a project, then enable the extension and load the subset:

    psql $NEON_URL -c 'CREATE EXTENSION IF NOT EXISTS vector;'
    psql $NEON_URL -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'

    docker compose run --rm -v ./bench:/app/bench -v ./api:/app/api \
      test python -m bench.export_deploy_subset --papers 140000 --execute

    psql $NEON_URL -f deploy_subset.sql
    psql $NEON_URL -c 'DROP INDEX IF EXISTS papers_title_trgm_idx;'
    psql $NEON_URL -c 'DROP INDEX IF EXISTS papers_abstract_md5_idx;'
    psql $NEON_URL -c "SET maintenance_work_mem='1GB'; REINDEX INDEX papers_embed_idx;"

**Check afterwards:** `SELECT count(*) FROM papers;` and confirm
`pg_database_size` is under the tier limit. The REINDEX matters — an HNSW
index restored from a dump is valid but not compact.

### 2. Render — API

`render.yaml` is in the repo. Point Render at it and set the secrets it marks
`sync: false`. The model directory must be present: the Dockerfile's runtime
stage needs `models/bge-small-en-v1.5`, which is gitignored, so either commit
it to a release artifact or add a build step that downloads it.

**Free tier behaviour to expect:** the instance sleeps after ~15 minutes idle
and the first request afterwards pays a cold start including ONNX model load.
The frontend already handles this — the 200 ms delayed loading state exists
precisely because a cold hybrid query measured 1,611 ms locally, and Render is
slower than this laptop.

### 3. Vercel — frontend

`web/vercel.json` is in the repo. Root directory `web`. The app calls the API
on relative paths, so set a rewrite from `/api/*` to the Render URL, or set
`VITE_API_BASE` and rebuild.

### 4. Google Console — redirect URIs

The OAuth client already works locally. For production, add to the existing
client (ID `826207232466-…apps.googleusercontent.com`):

* Authorized redirect URI: `https://<render-app>.onrender.com/api/auth/google/callback`
* Authorized JavaScript origin: `https://<vercel-app>.vercel.app`

Then set `GOOGLE_REDIRECT_URI` and `APP_ORIGIN` on Render to match, and
`CORS_ORIGINS` to the Vercel URL. **Repeat for the custom domain** when it
lands — both here and in the Render env.

**Rotate the client secret.** It was shared in plaintext during setup. It is
in `.env`, which is gitignored, and is not in the repo.

### 5. Email — the only piece that is a stub without an account

`api/auth/mailer.py` already speaks SMTP. Set `SMTP_HOST`, `SMTP_USER`,
`SMTP_PASSWORD`, `MAIL_FROM` on Render and codes go to real inboxes with no
code change. Until then it uses the console transport, which **logs the code
rather than pretending an email was sent** — and the verify banner says so on
screen, so a reviewer can still complete the flow honestly.

Resend is the least friction: an API key works as an SMTP password
(`smtp.resend.com`, user `resend`). Sending from a custom domain needs DNS
records, so this is easiest to finish after the domain is bought.

---

## Order, once the accounts exist

1. Neon, load subset, verify count and size
2. Render, deploy API, hit `/healthz` and `/api/stats`
3. Vercel, deploy frontend, check it reads the corpus count from the API
4. Google redirect URIs, test sign-in end to end
5. SMTP, test a real OTP
6. Custom domain: re-add origins in Google, update `CORS_ORIGINS` and
   `APP_ORIGIN`, rotate the Google secret

## What will need re-doing after the PubMed pull

The corpus goes to ~200,000, so the subset is re-exported and reloaded. The
number on the site follows automatically — nothing is typed in.

---

## Demo queries, re-picked against the DEPLOYED corpus (2026-08-16)

The original "BERT for de-identification of clinical records" returns **0
bm25 matches** on the 47,617-paper subset — its five keyword matches were not
in the top-cited slice. The landing page's fusion claim would not hold in a
live demo. Measured replacements, on Neon:

| query | bm25 | top-20 fused: both / kw-only / sem-only |
|---|---|---|
| **summarizing radiology reports** | 11 | **4 / 7 / 9** ← best balance |
| **clinical text de-identification with transformers** | 6 | 4 / 2 / 14 |
| **simplifying medical jargon for patients** | 3 | 2 / 1 / 17 |
| named entity recognition in clinical notes | 20 | 18 / 1 / 1 — too much agreement |
| depression screening using language models | 20 | 17 / 1 / 2 — too much agreement |

Use the top three. The first is the strongest demonstration: both arms
contribute real volume and neither dominates, which is exactly the case
fusion exists for. The last two in the table are poor demos precisely because
the arms agree — there is nothing for fusion to resolve.
