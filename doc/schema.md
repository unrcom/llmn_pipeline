# llamune RAG パイプライン テーブル設計 (DDL)

- 本書は設計書本体([pipeline_design.md](./pipeline_design.md))の第8章を独立させたもの
- スキーマ `rag` の DDL を扱う。更新頻度: 高
- jobs テーブルの DDL は [interfaces.md](./interfaces.md) §7.7.3 に定義がある(ハイブリッド同期/非同期契約とセットのため)
- データ構造は [interfaces.md](./interfaces.md) を参照

---

## 8. テーブル設計 DDL ドラフト

node-pg-migrate に乗せる前提。**スキーマ名は `rag`(確定)。projects テーブルは新設(確定)**。

```sql
-- 0. プロジェクト(新設)
CREATE TABLE rag.projects (
  project_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name        TEXT NOT NULL,
  description TEXT,
  -- Q1 Query Transform の既定モード
  query_transform_mode TEXT NOT NULL DEFAULT 'passthrough'
    CHECK (query_transform_mode IN ('passthrough', 'llm_rewrite')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 1. ソース文書
CREATE TABLE rag.sources (
  source_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id   UUID NOT NULL REFERENCES rag.projects ON DELETE CASCADE,
  source_data  TEXT NOT NULL,               -- ソースの説明(現行踏襲)
  raw_text     TEXT NOT NULL,
  metadata     JSONB NOT NULL DEFAULT '{}', -- ユーザー付与(ソース由来)
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. チャンク(モデル非依存)
CREATE TABLE rag.chunks (
  chunk_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       UUID NOT NULL REFERENCES rag.sources ON DELETE CASCADE,
  seq             INT  NOT NULL,               -- 文書内順序
  section_title   TEXT,
  text            TEXT NOT NULL,
  content_hash    TEXT NOT NULL,               -- SHA-256(正規化済み text)
  ingest_metadata JSONB NOT NULL DEFAULT '{}', -- front matter 由来(再取込で再生成)
  user_metadata   JSONB NOT NULL DEFAULT '{}', -- 手編集(hash 一致で引き継ぐ)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source_id, seq)
);
CREATE INDEX ON rag.chunks (source_id, content_hash);

-- 3. エンベディングモデル台帳
CREATE TABLE rag.embedding_models (
  model_key   TEXT PRIMARY KEY,              -- テーブル名に使うので英数+_
  model_name  TEXT NOT NULL,
  dimensions  INT  NOT NULL,
  vector_type TEXT NOT NULL DEFAULT 'vector' -- 'vector' | 'halfvec'
    CHECK (vector_type IN ('vector', 'halfvec')),
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 初期データ(確定した 3 モデル)
INSERT INTO rag.embedding_models (model_key, model_name, dimensions, vector_type) VALUES
  ('bge_m3',        'BAAI/bge-m3',                    1024, 'vector'),
  ('me5_large',     'intfloat/multilingual-e5-large', 1024, 'vector'),
  ('plamo_emb_1b',  'pfnet/plamo-embedding-1b',       2048, 'halfvec');

-- 4. モデル別ベクトル(モデル登録時にマイグレーションで生成)
CREATE TABLE rag.chunk_embeddings_bge_m3 (
  chunk_id    UUID PRIMARY KEY REFERENCES rag.chunks ON DELETE CASCADE,
  embedding   vector(1024) NOT NULL,
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON rag.chunk_embeddings_bge_m3
  USING hnsw (embedding vector_cosine_ops);

CREATE TABLE rag.chunk_embeddings_me5_large (
  chunk_id    UUID PRIMARY KEY REFERENCES rag.chunks ON DELETE CASCADE,
  embedding   vector(1024) NOT NULL,
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON rag.chunk_embeddings_me5_large
  USING hnsw (embedding vector_cosine_ops);

-- PLaMo は 2048 次元のため halfvec(HNSW の vector 型上限 2000 を超えるため)
CREATE TABLE rag.chunk_embeddings_plamo_emb_1b (
  chunk_id    UUID PRIMARY KEY REFERENCES rag.chunks ON DELETE CASCADE,
  embedding   halfvec(2048) NOT NULL,
  embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON rag.chunk_embeddings_plamo_emb_1b
  USING hnsw (embedding halfvec_cosine_ops);

-- 5. 取り込み実行の記録
CREATE TABLE rag.ingest_runs (
  run_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id      UUID NOT NULL REFERENCES rag.sources ON DELETE CASCADE,
  executed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  chunks_total   INT NOT NULL,
  chunks_carried INT NOT NULL,               -- hash 一致で引き継ぎ
  chunks_new     INT NOT NULL,
  chunks_dropped INT NOT NULL                -- メタデータ消滅
);

-- 6. 消滅メタデータの退避(孤児)
CREATE TABLE rag.orphaned_metadata (
  orphan_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  run_id        UUID NOT NULL REFERENCES rag.ingest_runs ON DELETE CASCADE,
  old_seq       INT NOT NULL,
  old_text      TEXT NOT NULL,               -- 証拠として保全
  old_metadata  JSONB NOT NULL,              -- 消えた user_metadata
  resolved      BOOLEAN NOT NULL DEFAULT false  -- 再適用済みフラグ
);

-- 7. プロジェクト × モデルの検索設定
CREATE TABLE rag.project_embedding_settings (
  project_id  UUID NOT NULL REFERENCES rag.projects ON DELETE CASCADE,
  model_key   TEXT NOT NULL REFERENCES rag.embedding_models,
  threshold   REAL NOT NULL,                 -- 閾値はモデルとセット
  is_default  BOOLEAN NOT NULL DEFAULT false, -- 通常検索で使うモデル
  PRIMARY KEY (project_id, model_key)
);
```

### DDL の設計ポイント

1. **ID 階層は project_id → source_id → chunk_id**。すべて ON DELETE CASCADE で連鎖削除
2. **chunks のメタデータを 2 カラムに分離**: 「引き継ぐ対象は手編集分だけ」というルールをカラム構造でそのまま表現
3. **embedding_models に vector_type カラム**: モデル登録マイグレーション生成時に vector / halfvec を切り替える
4. **比較モードは project_embedding_settings に行が複数あるだけ**: 特別なテーブルは不要
5. **query_transform_mode は projects の既定値 + リクエスト単位の上書き**(上書きは API パラメータであり DB には持たない)
6. **orphaned_metadata の resolved フラグ**: 未解決の孤児だけを返す API を作れる

