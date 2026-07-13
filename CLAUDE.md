# llmn_pipeline

llamune(閉域 LLM による RAG 実装基盤)の RAG パイプラインを再定義・再実装するプロジェクト。
REST API のみを実装する(フロントエンドなし)。

## 必読ドキュメント

作業開始前に必ず読むこと。**仕様の正はこれらのドキュメントであり、実装ではない。**

- `doc/pipeline_design.md` — 背景・方針・ステージ分割・設計判断
- `doc/interfaces.md` — データ構造(Pydantic)・API 定義(リクエスト/レスポンス・エラー形式)
- `doc/schema.md` — テーブル設計 DDL(スキーマ `rag`、監査ログ含む)
- `doc/implementation_plan.md` — 実装順序(フェーズ 0〜3)と依頼の分割

## 進め方のルール

- **1 依頼 = 1 API(または implementation_plan.md の 1 行)**。依頼範囲外の実装を先回りしないこと
- 仕様(doc/)と実装が食い違う場合、**勝手に解釈して実装せず、質問すること**
- 実装完了時は動作確認手順(curl 例)を提示すること

## 技術スタック

- Python 3.13(pyenv)、venv は `back/.venv`
- FastAPI + Pydantic + SQLAlchemy
- PostgreSQL(Docker)+ pgvector。スキーマ名は `rag`
- マイグレーション: node-pg-migrate(`npm run migrate:up`)
- エンベディング: bge-m3 / multilingual-e5-large / PLaMo-Embedding-1B(Embedder 抽象で差し替え可能)
- LLM 推論: MLX(mlx-lm)、Apple Silicon ローカル

## コーディング規約

- SonarCloud で 0 issues を維持する(不要な複雑度・重複を作らない)
- FastAPI の DI は Annotated パターンを使う
- 関数は小さく分割する。1 関数 1 責務
- テスト: 正常系・異常系(404 / バリデーションエラー)を含める
- コミットメッセージは日本語可。prefix は feat: / fix: / docs: / test: / refactor:

## 環境メモ

- 開発機: Mac mini (Apple Silicon, 64GB)
- 稼働中の既存プロダクト(llamune 等)の Docker ポート 5434-5439 は使用中。衝突させないこと

## Git 運用

- **git commit / push は行わないこと**。コミットは常にユーザーが手動で行う
- git status / git diff / git add -n などの確認系コマンドは使用してよい
