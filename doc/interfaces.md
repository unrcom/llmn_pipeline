# llamune RAG パイプライン インターフェース定義

- 本書は設計書本体([pipeline_design.md](./pipeline_design.md))の第7章を独立させたもの
- データ構造(Pydantic モデル)と API 定義を扱う。更新頻度: 高
- DDL は [schema.md](./schema.md) を参照

---

## 7. Ingestion 側インターフェース定義(ドラフト)

Pydantic モデルで表現する(FastAPI と整合)。

```
SourceDocument   … I1 の出力
  - source_id: UUID
  - project_id: UUID          # ★上位 ID。ソースは必ずプロジェクトに属する
  - source_data: str          # ソースの説明(自由テキスト、現行踏襲)
  - raw_text: str
  - metadata: dict            # ユーザー付与(ソース由来)、JSONB 相当
  - created_at: datetime (UTC)

CleanDocument    … I2 の出力
  - source_id: UUID
  - text: str                 # 正規化済み

Chunk            … I3 の出力(リストで流れる)
  - chunk_id: UUID
  - source_id: UUID
  - section_title: str | None # セクション見出し
  - text: str                 # 見出し付き 500 文字段落
  - seq: int                  # 文書内の順序
  - content_hash: str         # SHA-256(正規化済み text)
  - ingest_metadata: dict     # front matter 由来
  - user_metadata: dict       # 手編集(新規取込時は空)

EmbeddedChunk    … I4 の出力
  - chunk: Chunk
  - model_key: str            # 使用したエンベディングモデル
  - vector: list[float]       # 次元はモデルごと

IndexResult      … I5 の出力
  - source_id: UUID
  - run_id: UUID              # ingest_runs への参照
  - upserted: int
  - carried / new / dropped: int
```

### Embedder インターフェース(§4.2 を受けて)

```
Embedder(抽象)
  - model_key: str
  - dimensions: int
  - embed_documents(texts: list[str]) -> list[list[float]]
  - embed_query(text: str) -> list[float]
```

実装: BgeM3Embedder / MultilingualE5Embedder / PlamoEmbedder。プレフィックス付与や encode_query/encode_document の呼び分けは各実装の内部に閉じる。

---

## 7.5 Query 側インターフェース定義(確定)

### 7.5.1 パラメータ解決の統一パターン

Query 側の可変パラメータ(model_key / transform_mode / retrieval_plan / threshold)はすべて
**「プロジェクト設定に既定値を持ち、リクエストで都度上書き可能」** とする。

### 7.5.2 データ構造

```
QueryRequest       … パイプライン全体への入力(API リクエストの中身)
  - project_id: UUID
  - user_input: str                # ユーザーの生入力
  - model_key: str | None          # None なら is_default のモデル
  - transform_mode: str | None     # None なら projects.query_transform_mode
  - retrieval_plan: RetrievalPlan | None  # None ならプロジェクト既定プラン
  - metadata_filter: dict | None   # JSONB に対するフィルタ条件

TransformedQuery   … Q1 の出力
  - original: str                  # user_input そのまま(トレース用)
  - query: str                     # 実際に検索に使うクエリ
  - mode: str                      # 'passthrough' | 'llm_rewrite'(実際に適用されたもの)

RetrievalPlan      … 検索の実行計画(多段検索)
  - passes: list[PassSpec]
      PassSpec:
        - name: str                 # 例 'meta+vec', 'vec_only', 'fulltext'
        - strategy: str             # 'vector' | 'fulltext'(将来追加可)
        - top_k: int                # パスごとに定義
        - use_metadata_filter: bool # QueryRequest.metadata_filter を適用するか
        - enabled: bool

  既定プランの例:
    1. vector + metadata_filter 適用, top_k=10
    2. vector のみ(フィルタなし),   top_k=3
    3. fulltext,                     top_k=5

RetrievalResult    … Q2 の出力
  - model_key: str
  - passes: list[PassResult]
      PassResult:
        - spec: PassSpec            # 何をやったか(トレース用)
        - hits: list[Hit]
        - elapsed_ms: int
  - threshold: float                # 適用予定の閾値(トレース用。足切りは Q3)

Hit
  - chunk_id: UUID
  - text: str
  - section_title: str | None
  - seq: int
  - metadata: dict                  # マージ済み実効メタデータ
  - score: float                    # 生の値(閾値適用前)
  - score_type: str                 # 'cosine_distance' | 'ts_rank' 等

BuiltContext       … Q3 の出力
  - chunks: list[ContextChunk]      # リランク・重複排除・件数制御後(最終順)
      ContextChunk:
        - hit: Hit
        - found_in: list[str]       # ヒットしたパス名(複数パスは信頼度高)
  - is_empty: bool                  # ゼロ件判定(「データなし」応答のトリガ)
  - dropped: list[Hit]              # 閾値・件数制御で落としたもの(トレース用)

GenerationResult   … Q4 の出力
  - response: str
  - llm_model: str                  # 生成に使った LLM
  - prompt: str                     # 実際に投げたプロンプト全文(トレース用)
  - response_time_ms: int

QueryTrace         … Q5 が永続化する単位(上記すべての合成)
  - trace_id: UUID
  - project_id, session_id, turn_cnt
  - request: QueryRequest
  - transformed: TransformedQuery
  - retrieval: RetrievalResult
  - context: BuiltContext
  - generation: GenerationResult | None  # 比較モード等、検索のみの場合は None
  - created_at: datetime (UTC)
```

### 7.5.3 設計ポイント

1. **多段検索(マルチパス retrieval)**: Q2 は「検索プラン(性質の違う検索パスの列)の実行」。
   メタデータフィルタ付きベクトル検索、フィルタなしベクトル検索、全文検索などを
   パスごとの top_k で実行し、結果をパス別に返す。「search loosely」の実体
2. **スコアの異種混在を明示**: コサイン距離と全文検索ランクは比較不能なため score_type を持つ。
   Q3 のリランクは異種スコアの統合(RRF: Reciprocal Rank Fusion が有力候補)
3. **重複排除は Q3**: 同一チャンクが複数パスでヒットした場合、Q2 はそのまま返し、
   Q3 が chunk_id で統合。found_in にパス名を残す(複数パスヒット = 信頼度の指標)
4. **Q2 は閾値適用「前」を返す**: 足切り・リランク・件数制御・ゼロ件判定はすべて Q3 の責務。
   「search loosely(Q2), control(Q3)」がインターフェースに現れる
5. **各ステージの出力にトレース材料を含める**: original / 生スコア / dropped / prompt 全文。
   「Q2 では取れていたが Q3 で落ちた」が追える
6. **generation が Optional**: 比較モード(Q4 スキップ)も同じ QueryTrace 構造で記録できる

---

## 7.6 API エンドポイント設計(確定)

REST API として以下のエンドポイントを提供する。リソース指向で整理。

### 7.6.1 プロジェクト管理

```
POST   /projects                          プロジェクト作成
GET    /projects                          一覧
GET    /projects/{project_id}             詳細(既定プラン・transform_mode 含む)
PATCH  /projects/{project_id}             設定変更
DELETE /projects/{project_id}             削除(CASCADE)

GET    /projects/{project_id}/embedding-settings              モデル×閾値の一覧
PUT    /projects/{project_id}/embedding-settings/{model_key}  閾値・is_default 設定
```

### 7.6.2 Ingestion(投入系)

```
POST   /projects/{project_id}/sources     ソース登録+取り込み実行(I1〜I5 一気通貫)★
GET    /projects/{project_id}/sources     ソース一覧
GET    /sources/{source_id}               ソース詳細
PUT    /sources/{source_id}               ソース更新+再取り込み(引き継ぎルール発動)★
DELETE /sources/{source_id}               削除

GET    /sources/{source_id}/chunks        チャンク一覧
PATCH  /chunks/{chunk_id}/metadata        user_metadata の編集
GET    /sources/{source_id}/export        front matter 形式でエクスポート(運用ループ用)

GET    /sources/{source_id}/ingest-runs   取り込み履歴
GET    /ingest-runs/{run_id}/orphans      孤児メタデータ一覧(?resolved=false で未解決のみ)
POST   /orphans/{orphan_id}/apply         孤児をチャンクへ再適用(resolved 化)
```

### 7.6.3 Query(検索・生成系)

```
POST   /projects/{project_id}/query       フルパイプライン実行(Q1〜Q5)→ response + trace_id
POST   /projects/{project_id}/search      検索のみ(Q1〜Q3、Q4 スキップ)
POST   /projects/{project_id}/compare     比較モード(複数モデルで search を並列実行)
```

`/query` と `/search` の分離は QueryTrace の `generation: Optional` に対応する。
`/compare` は内部的に search を N モデル分実行するだけ。

### 7.6.4 エンベディング

```
GET    /embedding-models                  台帳一覧
POST   /projects/{project_id}/embed       指定モデルで未エンベディングのチャンクを一括処理 ★
                                          (モデル追加時・比較準備用)
```

### 7.6.5 ジョブ

```
GET    /jobs/{job_id}                     ジョブ状態・結果の照会(ポーリング用)
```

### 7.6.6 トレース(観測・評価)

```
GET    /projects/{project_id}/traces      トレース一覧(フィルタ: 期間、session_id)
GET    /traces/{trace_id}                 トレース詳細(QueryTrace 全体)
```

---

## 7.7 ハイブリッド同期/非同期契約(確定)

★印の重い処理(取り込み・エンベディング)は **「10 秒までは同期、超えたら非同期に昇格」** とする。

### 7.7.1 API 契約

```
処理開始 → 10 秒以内に完了した場合:
  HTTP 200 OK
  {
    "status": "completed",
    "job_id": "...",          # 完了時も採番する(履歴の一貫性のため)
    "result": { IndexResult }
  }

10 秒経過しても未完了の場合:
  HTTP 202 Accepted
  {
    "status": "processing",
    "job_id": "...",
    "poll_url": "/jobs/{job_id}"
  }

GET /jobs/{job_id}
  {
    "status": "processing" | "completed" | "failed",
    "progress": { "phase": "embedding", "done": 120, "total": 322 },  # 任意
    "result": { IndexResult } | null,
    "error": { ... } | null
  }
```

### 7.7.2 設計判断

1. **クライアントは `status` フィールドで判定する**(HTTP 200/202 も対応させるが、判定の正は status)。
   「completed なら result を読む、processing なら poll_url をポーリング」の一本道
2. **同期完了でも job_id を採番する**。全実行が jobs テーブルに記録され、
   同期/非同期は応答の仕方の違いに過ぎない
3. **実装は経路 1 本**: 常にバックグラウンドタスクで実行し、`asyncio.wait_for(timeout=10)` で待つ。
   間に合えば 200、間に合わなければ 202

### 7.7.3 jobs テーブル(DDL 追加)

```sql
CREATE TABLE rag.jobs (
  job_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type    TEXT NOT NULL,           -- 'ingest' | 'reingest' | 'embed'
  status      TEXT NOT NULL DEFAULT 'processing'
    CHECK (status IN ('processing', 'completed', 'failed')),
  progress    JSONB,                   -- { phase, done, total }
  result      JSONB,                   -- IndexResult 等
  error       JSONB,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
```

※ 各エンドポイントの詳細なリクエスト/レスポンス定義は次ステップで行う。

---

