-- Up Migration

-- retrieval_plan の DB 格納方法(依頼 1-1 で確定): JSONB カラムとして projects に追加する。
-- システム既定プラン(interfaces.md §7.9)を DB 側の DEFAULT にも設定し、
-- アプリ側で省略時に明示投入する値と一致させる。
ALTER TABLE rag.projects
  ADD COLUMN retrieval_plan JSONB NOT NULL DEFAULT '{
    "passes": [
      { "name": "meta+vec", "strategy": "vector",   "top_k": 10, "use_metadata_filter": true,  "enabled": true },
      { "name": "vec_only", "strategy": "vector",   "top_k": 3,  "use_metadata_filter": false, "enabled": true },
      { "name": "fulltext", "strategy": "fulltext", "top_k": 5,  "use_metadata_filter": false, "enabled": true }
    ]
  }'::jsonb;

-- Down Migration

ALTER TABLE rag.projects
  DROP COLUMN retrieval_plan;
