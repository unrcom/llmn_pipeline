-- Up Migration

-- 8. 監査ログ(全テーブル共通)
-- UPDATE / DELETE の直前に更新前の行全体を JSONB で記録する。
-- 物理削除を採用しつつ、トラブル対応・障害解析・データ復旧を可能にするための仕組み。
CREATE TABLE rag.audit_log (
  audit_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  table_name  TEXT NOT NULL,
  operation   TEXT NOT NULL CHECK (operation IN ('UPDATE', 'DELETE')),
  operated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  old_data    JSONB NOT NULL          -- 更新/削除前の行全体
);
CREATE INDEX ON rag.audit_log (table_name, operated_at);

-- 汎用トリガー関数(1つで全テーブルに使い回す)
CREATE FUNCTION rag.audit_trigger() RETURNS trigger AS $$
BEGIN
  INSERT INTO rag.audit_log (table_name, operation, old_data)
  VALUES (TG_TABLE_NAME, TG_OP, to_jsonb(OLD));
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- 適用対象: projects / sources / chunks / project_embedding_settings / orphaned_metadata
-- 除外: chunk_embeddings_*(chunksから再生成可能でサイズが大きい)、
--       jobs / ingest_runs / audit_log 自身(追記中心のため)
CREATE TRIGGER audit_projects
  BEFORE UPDATE OR DELETE ON rag.projects
  FOR EACH ROW EXECUTE FUNCTION rag.audit_trigger();
CREATE TRIGGER audit_sources
  BEFORE UPDATE OR DELETE ON rag.sources
  FOR EACH ROW EXECUTE FUNCTION rag.audit_trigger();
CREATE TRIGGER audit_chunks
  BEFORE UPDATE OR DELETE ON rag.chunks
  FOR EACH ROW EXECUTE FUNCTION rag.audit_trigger();
CREATE TRIGGER audit_project_embedding_settings
  BEFORE UPDATE OR DELETE ON rag.project_embedding_settings
  FOR EACH ROW EXECUTE FUNCTION rag.audit_trigger();
CREATE TRIGGER audit_orphaned_metadata
  BEFORE UPDATE OR DELETE ON rag.orphaned_metadata
  FOR EACH ROW EXECUTE FUNCTION rag.audit_trigger();

-- Down Migration

DROP TRIGGER IF EXISTS audit_orphaned_metadata ON rag.orphaned_metadata;
DROP TRIGGER IF EXISTS audit_project_embedding_settings ON rag.project_embedding_settings;
DROP TRIGGER IF EXISTS audit_chunks ON rag.chunks;
DROP TRIGGER IF EXISTS audit_sources ON rag.sources;
DROP TRIGGER IF EXISTS audit_projects ON rag.projects;
DROP FUNCTION IF EXISTS rag.audit_trigger();
DROP TABLE IF EXISTS rag.audit_log;
