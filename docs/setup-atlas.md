# MongoDB Atlas setup

Knowledge Studio uses Atlas for application data, **Atlas Vector Search** for semantic retrieval and **Atlas Search** for keyword retrieval. Storing embeddings doesn't make them searchable: the two search indexes below must exist and be `READY`.

## 1. Create the cluster

1. Create an Atlas project and a cluster. The free M0 tier is fine for development; check Atlas's current limits on search indexes for your tier. Production should use a dedicated tier sized for your library (vectors at 768 dimensions are roughly 3 KiB per chunk in the index).
2. **Database Access:** create a database user with `readWrite` on your database (e.g. `knowledge_studio`). Avoid `atlasAdmin` for the application user.
3. **Network Access:** add the public IPs of your API and worker hosts. For local development, add your current IP. Avoid `0.0.0.0/0` in production.
4. **Connect > Drivers:** copy the `mongodb+srv://…` string into `MONGODB_URI` in `.env`.

Atlas clusters are replica sets, so multi-document transactions are available and detected automatically at startup (`transactions_supported=True` in the API log). The code also has documented single-document atomic fallbacks.

## 2. Create collections, validators and indexes

```powershell
./scripts/setup-db.ps1
# equivalent to:
backend\.venv\Scripts\python.exe -m app.cli setup-db
backend\.venv\Scripts\python.exe -m app.cli setup-search-indexes --wait
```

`setup-db` is idempotent: it creates collections, applies `$jsonSchema` validators, builds regular, unique and TTL indexes, seeds the Free and Premium plans if the catalog is empty, and maps `PADDLE_PRICE_ID_PREMIUM_*` if they're set.

`setup-search-indexes` creates both search indexes on `chunks` through the driver (`createSearchIndexes`) and, with `--wait`, polls until both are `READY`. Building takes a few minutes on an empty cluster.

## 3. Search index definitions

The exact definitions, generated from code for the configured dimensions, are in [`backend/scripts/atlas-search-indexes.json`](../backend/scripts/atlas-search-indexes.json). To create them by hand in the Atlas UI: **Atlas Search > Create Search Index > JSON Editor**, database `knowledge_studio`, collection `chunks`.

**`chunks_vector_v1`** (type *Vector Search*):

```json
{
  "fields": [
    { "type": "vector", "path": "embedding", "numDimensions": 768, "similarity": "cosine" },
    { "type": "filter", "path": "embedding_space" },
    { "type": "filter", "path": "workspace_id" },
    { "type": "filter", "path": "file_id" },
    { "type": "filter", "path": "source_id" },
    { "type": "filter", "path": "source_type" },
    { "type": "filter", "path": "language" }
  ]
}
```

**`chunks_text_v1`** (type *Atlas Search*):

```json
{
  "mappings": {
    "dynamic": false,
    "fields": {
      "text": { "type": "string", "analyzer": "lucene.standard",
                "multi": { "english": { "type": "string", "analyzer": "lucene.english" } } },
      "section_title": { "type": "string", "analyzer": "lucene.standard" },
      "source_title": { "type": "string", "analyzer": "lucene.standard" },
      "author": { "type": "string", "analyzer": "lucene.standard" },
      "workspace_id": { "type": "objectId" },
      "file_id": { "type": "objectId" },
      "source_id": { "type": "objectId" },
      "source_type": { "type": "token" },
      "embedding_space": { "type": "token" },
      "language": { "type": "token" }
    }
  }
}
```

`lucene.standard` tokenises Georgian and other scripts, so keyword search works across languages; `lucene.english` adds stemming for English text.

**`numDimensions` must equal `GEMINI_EMBEDDING_DIMENSIONS`.** Admin panel > System flags a mismatch (wrong dimensions or missing vector field) as a failed check. To change dimensions, follow the migration in the [runbook](runbook.md#changing-the-embedding-model); never edit the live index in place.

## 4. Verify

* `GET /health/ready` returns `ready: true`.
* Admin panel > **System**: “Search indexes” shows both indexes `READY`.
* Upload a document, then use **Library search** or **Ask AI**. A degraded-mode notice appears if either search method is unavailable.

## When a search feature isn't available

If your deployment can't run Atlas Search (for example a self-managed `mongod` without `mongot`), `setup-search-indexes` reports `UNAVAILABLE` instead of pretending. For local development and automated tests only, you can use:

```
VECTOR_SEARCH_BACKEND=exact        # cosine similarity computed in the database over the workspace's chunks
KEYWORD_SEARCH_BACKEND=text_index  # MongoDB $text index
```

`exact` scans every chunk in the workspace, so it doesn't scale and is **refused in production** (`APP_ENV=production|staging`). Production alternatives are Atlas (any tier with search) or MongoDB Enterprise/Community deployments that run `mongot` and support `$vectorSearch`.
