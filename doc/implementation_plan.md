# llmn_pipeline 実装計画(エージェンティックコーディング)

- 進め方: **API 1 本 = 1 依頼**。実装 → コードレビュー → 動作確認を通過してから次の依頼へ進む
- 各依頼は「動作確認可能な単位」で閉じるよう、大きい API は分割している
- 仕様の正は [interfaces.md](./interfaces.md) と [schema.md](./schema.md)。依頼時に該当節を指定する

---

## フェーズ 0: 土台(API ではない。最初の 1〜2 依頼)

| # | 依頼 | 内容 |
|---|------|------|
| 0-1 | リポジトリ骨格 | FastAPI 起動、ディレクトリ構成、設定読み込み、DB 接続、エラー形式(§7.8)の共通ハンドラ |
| 0-2 | マイグレーション | schema.md の DDL 一式(projects 〜 audit_log、トリガー含む) |

## フェーズ 1: プロジェクト管理系(§7.9)— 依存が少なく、実装パターン確立に最適

| # | 依頼 | 内容 |
|---|------|------|
| 1-1 | POST /projects + GET /projects | 作成と一覧をセット(動作確認が自己完結する) + 認証基盤(§7.8.5) |
| 1.5 | テスト DB の分離 | 同一コンテナ内に `llmn_pipeline_test` DB を追加。conftest.py が接続先 DB 名を無条件に test に固定(開発 DB でテストが走る事故を構造的に排除)。テスト開始時にマイグレーション適用。setup.md 追記 |
| 1-2 | GET /projects/{id} + PATCH + DELETE | 詳細・部分更新・削除(CASCADE と audit_log の動作確認込み) |
| 1-3 | embedding-settings PUT / GET | is_default の自動付け替え含む |

## フェーズ 2: Ingestion 系(§7.10)— パイプライン本体が登場

| # | 依頼 | 内容 |
|---|------|------|
| 2-1 | POST /sources(I1〜I3) | チャンキング + front matter 解釈。**エンベディングなし**で一旦完結 |
| 2-2 | I4〜I5 追加 | Embedder 抽象 + bge_m3 実装 + ジョブ機構(§7.7)+ GET /jobs/{id}。**一番重い依頼** |
| 2-3 | ソース・チャンク参照系 | GET /sources 系、GET /chunks、PATCH /chunks/metadata |
| 2-4 | PUT /sources | 再取り込み + content_hash 引き継ぎ + orphans 記録 |
| 2-5 | 運用系 | export / ingest-runs / orphans 一覧 / apply |

## フェーズ 3: Query 系(§7.11)

| # | 依頼 | 内容 |
|---|------|------|
| 3-1 | POST /search 最小構成 | Q1 passthrough + Q2 vector パスのみ + Q3(足切り・件数制御・ゼロ件判定) |
| 3-2 | 多段検索の完成 | Q2 に fulltext パス追加、Q3 に RRF 統合・重複排除(found_in) |
| 3-3 | POST /query | Q4 Generate(MLX)+ Q5 Log + セッション管理 |
| 3-4 | POST /compare + Q1 llm_rewrite | 比較モードとクエリ書き換え |
| 3-5 | 拡張 | 残りモデル(me5_large / plamo_emb_1b)追加、トレース API(§7.12) |

---

## 依頼テンプレート

各依頼は以下の形式で固定する(設計書がそのまま仕様書として機能する):

```
doc/interfaces.md の §X.Y と doc/schema.md を読み、以下の API を実装してください。

対象: <エンドポイント>
仕様: interfaces.md §X.Y に従う。仕様と実装が食い違う場合は実装ではなく質問すること
規約: 既存コードのディレクトリ構成・命名・DI パターンに従う
テスト: 正常系・異常系(404 / バリデーション)のテストを含める
確認: 実装完了後、動作確認手順(curl 例)を提示すること
```

## レビュー・動作確認の観点(依頼ごとに実施)

1. 仕様(interfaces.md)との一致 — リクエスト/レスポンスの形、ステータスコード、エラー形式
2. SonarCloud 0 issues の維持
3. 動作確認 — 提示された curl 手順を実際に実行して確認
4. audit_log — UPDATE/DELETE を伴う API では監査ログが記録されること
