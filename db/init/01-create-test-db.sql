-- postgres イメージの初回起動時(空の named volume 作成時)にのみ実行される。
-- 依頼 1.5: テスト専用 DB を開発 DB と同一コンテナ内に分離する。
CREATE DATABASE llmn_pipeline_test;
