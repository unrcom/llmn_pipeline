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
3. **実装は経路 1 本**: 常にバックグラウンドタスクで実行し、`asyncio.wait_for(timeout=...)` で待つ。
   間に合えば 200、間に合わなければ 202
4. **タイムアウト値(10 秒)はハードコードせず Settings で設定化する**
   (例: `sync_wait_timeout_seconds: float = 10.0`、.env で上書き可能)。
   運用調整とテスト容易性(テストでは短い値に設定)のため

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

※ 各エンドポイントの詳細なリクエスト/レスポンス定義は以下 §7.8 以降に順次追加する。

---

## 7.8 エラー形式(全 API 共通、確定)

```jsonc
// 400 / 401 / 404 / 409 / 500 共通
{
  "error": {
    "code": "project_not_found",      // 機械可読
    "message": "Project not found",   // 人間可読
    "detail": { ... }                 // 任意(バリデーション詳細等)
  }
}
```

---

## 7.8.5 認証(確定)

### 方式: 自己記述的 API キー(.env 格納)

すべての API は `X-API-Key` ヘッダによる認証を必須とする(**GET /health のみ除外**)。

### キー形式

```
llmn_{name}_{secret}
  例: llmn_mop_dev_Xa9kR2mPqW8vT5nL7cJ4
```

- `llmn_` — プロジェクトプレフィックス
- `{name}` — ユーザー/用途の識別子(英数と `_`)。キーを見ればどのユーザーのリクエストか分かる
- `{secret}` — ランダム部(**`secrets.token_hex(16)` で生成。`_` を含まない**)。秘密性はここだけが担う。
  name 部には `_` を含んでよい(識別部の切り出しは「最後の `_`」で行われ、secret が `_` を
  含まないことで境界が一意に定まる)

### 検証規則

1. 認証は **キー文字列全体の完全一致** で行う(name 部を切り出して照合するようなパースはしない)
2. 有効キーは環境変数 `API_KEYS`(カンマ区切り)に列挙する

```bash
# .env
API_KEYS=llmn_mop_dev_Xa9kR2mPqW8vT5nL7cJ4,llmn_sier_a_B3fH6jN9sD2gK5pQ8wZ1
```

3. 認証失敗(ヘッダなし / 不一致)は **401** を §7.8 形式で返す:

```jsonc
{ "error": { "code": "invalid_api_key", "message": "Invalid or missing API key" } }
```

4. ログにはキー全体を残さず、secret 部を除いた識別部(例: `llmn_mop_dev`)のみを記録する

### 実装方針

- FastAPI の依存性(DI)として一点実装し、全ルーターに適用する(/health のみ除外)。
  将来 DB 方式(api_keys テーブル)や unrauth(JWT)へ移行する場合も、この DI の内部
  実装を差し替えるだけで API 契約(ヘッダ名・401 形式)は不変
- キーの発行は手動: `python -c "import secrets; print('llmn_{name}_' + secrets.token_hex(16))"`
  を実行し .env に追記、アプリ再起動で反映

### 将来拡張(未実装)

- DB 方式(api_keys テーブル: ハッシュ保存、is_active による即時失効、last_used_at 記録)
- 認可(キー×プロジェクトのテナント分離)は unrauth 統合時に検討

---

## 7.9 プロジェクト管理系 API 詳細(確定)

### POST /projects — プロジェクト作成

```jsonc
// Request
{
  "name": "薬効RAG",                        // 必須
  "description": "薬効データの検索",          // 任意
  "query_transform_mode": "passthrough",     // 任意(既定: passthrough)
  "retrieval_plan": {                        // 任意(既定: システム既定プラン)
    "passes": [
      { "name": "meta+vec", "strategy": "vector",   "top_k": 10, "use_metadata_filter": true,  "enabled": true },
      { "name": "vec_only", "strategy": "vector",   "top_k": 3,  "use_metadata_filter": false, "enabled": true },
      { "name": "fulltext", "strategy": "fulltext", "top_k": 5,  "use_metadata_filter": false, "enabled": true }
    ]
  }
}

// Response 201 Created
{
  "project_id": "uuid",
  "name": "薬効RAG",
  "description": "薬効データの検索",
  "query_transform_mode": "passthrough",
  "retrieval_plan": { ... },
  "created_at": "2026-07-11T00:00:00Z",
  "updated_at": "2026-07-11T00:00:00Z"
}
```

### GET /projects — 一覧

Response 200: `{ "projects": [ 上記形式の配列 ] }`

### GET /projects/{project_id} — 詳細

Response 200: 上記形式 + `embedding_settings` を同梱

```jsonc
{
  "project_id": "uuid",
  "name": "...", "description": "...",
  "query_transform_mode": "passthrough",
  "retrieval_plan": { ... },
  "embedding_settings": [
    { "model_key": "bge_m3",       "threshold": 0.6, "is_default": true },
    { "model_key": "plamo_emb_1b", "threshold": 0.5, "is_default": false }
  ],
  "created_at": "...", "updated_at": "..."
}
```

### PATCH /projects/{project_id} — 部分更新

name / description / query_transform_mode / retrieval_plan のうち渡されたものだけ更新。
Response 200: 更新後の全体(GET 詳細と同形式)

### DELETE /projects/{project_id} — 削除

Response 204 No Content。CASCADE で sources/chunks/embeddings も削除される。
物理削除(復旧可能性は監査ログ audit_log が担保する。schema.md 参照)。

### PUT /projects/{project_id}/embedding-settings/{model_key}

```jsonc
// Request
{ "threshold": 0.6, "is_default": true }
// Response 200
{ "model_key": "bge_m3", "threshold": 0.6, "is_default": true }
```

`is_default: true` を設定したら、同一プロジェクトの他モデルの is_default は自動的に false になる
(1 プロジェクトに default は常に 1 つ)。

### GET /projects/{project_id}/embedding-settings

Response 200: `{ "settings": [ ... ] }`

### 設計判断

1. **retrieval_plan は API 上プロジェクトの属性として扱う**。DB 上の格納方法(JSONB カラムか
   別テーブルか)が未決のままでも API の形は不変
2. **is_default の付け替えは PUT 側で自動処理**
3. **DELETE は物理削除**。監査ログ(audit_log)がトラブル対応・障害解析・データ復旧を担保する

---

## 7.10 Ingestion 系 API 詳細(確定)

### front matter の意味論(確定)

**文書レベルのみ**を解釈する。ソーステキスト先頭の front matter が、そのソースから生成される
**全チャンクの ingest_metadata に同じ内容**として入る。セクション単位の付与は独自記法の発明を
伴うため採用しない(チャンク単位の差は user_metadata で付ける。ingest_metadata のカラム設計は
将来のセクションレベル解釈にも対応できる形なので、必要が実証されたら拡張する)。

### POST /projects/{project_id}/sources — ソース登録+取り込み(I1〜I5)

```jsonc
// Request
{
  "source_data": "薬効データベース 2026年版",   // 必須(ソースの説明)
  "raw_text": "---\ncategory: 薬効\n---\n# ロキソプロフェン\n...",  // 必須
  "metadata": { "department": "内科" },        // 任意(ソース由来メタデータ)
  "embed_models": ["bge_m3"]                   // 任意(既定: is_default のモデルのみ)
}

// Response 200(10秒以内に完了)
{
  "status": "completed",
  "job_id": "uuid",
  "result": {
    "source_id": "uuid",
    "run_id": "uuid",
    "chunks_total": 12, "chunks_carried": 0, "chunks_new": 12, "chunks_dropped": 0,
    "embedded": { "bge_m3": 12 }
  }
}
// Response 202(10秒超過)→ §7.7 の契約通り
```

### PUT /sources/{source_id} — ソース更新+再取り込み(引き継ぎルール発動)

Request は POST と同形式(source_data / raw_text / metadata / embed_models のうち渡されたものだけ更新)。
Response も同じハイブリッド契約。result の carried / dropped に引き継ぎ結果が出る。
dropped > 0 の場合は孤児 API(下記)で詳細を確認する。

### GET /projects/{project_id}/sources — 一覧

Response 200: `{ "sources": [ { source_id, source_data, metadata, created_at, updated_at, chunk_count } ] }`

### GET /sources/{source_id} — 詳細

Response 200: 上記 + raw_text

### DELETE /sources/{source_id} — 削除

Response 204。CASCADE で chunks / embeddings も削除(復旧は audit_log)。

### GET /sources/{source_id}/chunks — チャンク一覧

```jsonc
// Response 200
{
  "chunks": [
    {
      "chunk_id": "uuid", "seq": 0,
      "section_title": "効能・効果",
      "text": "...",
      "content_hash": "sha256...",
      "ingest_metadata": { "category": "薬効" },
      "user_metadata": { "重要度": "高" },
      "embedded_in": ["bge_m3", "plamo_emb_1b"]   // どのモデルでベクトル化済みか
    }
  ]
}
```

### PATCH /chunks/{chunk_id}/metadata — user_metadata の編集

```jsonc
// Request(user_metadata 全体を置換。部分マージではない)
{ "user_metadata": { "重要度": "高", "確認済み": true } }
// Response 200: 更新後のチャンク(上記 chunks 要素と同形式)
```

### GET /sources/{source_id}/export — front matter 形式エクスポート

Response 200: `{ "content": "---\n...\n---\n# ...", "format": "frontmatter_markdown" }`

user_metadata を front matter に織り込んだ、再取り込み可能な形式を返す。
「API で試行錯誤 → 良かった設定をソースに反映 → 再取り込みしても残る」の運用ループ用。

### GET /sources/{source_id}/ingest-runs — 取り込み履歴

Response 200: `{ "runs": [ { run_id, executed_at, chunks_total, chunks_carried, chunks_new, chunks_dropped } ] }`

### GET /ingest-runs/{run_id}/orphans — 孤児メタデータ一覧

Query パラメータ: `?resolved=false`(未解決のみ)
Response 200: `{ "orphans": [ { orphan_id, old_seq, old_text, old_metadata, resolved } ] }`

### POST /orphans/{orphan_id}/apply — 孤児の再適用

```jsonc
// Request
{ "chunk_id": "uuid", "mode": "replace" }   // mode: "replace" | "merge"
// Response 200: 適用後のチャンク。orphan は resolved=true になる
```

- `replace`: 対象チャンクの user_metadata を old_metadata で置き換える
- `merge`: 対象チャンクの user_metadata に old_metadata をマージする(キー衝突は old_metadata が勝つ)

---

## 7.11 Query 系 API 詳細(確定)

### レスポンス方式(確定)

- **一括レスポンス**。ストリーミング(SSE)は採用しない
- **ハイブリッド同期/非同期契約(§7.7)は /query にも適用する**。生成が 10 秒を超えたら
  202 + job_id を返し、`GET /jobs/{job_id}` の result に下記の一括レスポンスがそのまま入る
- 将来チャット UI を作る際に SSE が必要になれば、既存契約と並存させる(Accept ヘッダでの切り替え等)

### POST /projects/{project_id}/query — フルパイプライン実行(Q1〜Q5)

```jsonc
// Request
{
  "user_input": "頭痛に効く薬は?",           // 必須
  "session_id": "uuid",                      // 任意(継続会話。なければ新規セッション扱い)
  "model_key": "bge_m3",                     // 任意(既定: is_default)
  "transform_mode": "llm_rewrite",           // 任意(既定: プロジェクト設定)
  "retrieval_plan": { "passes": [...] },     // 任意(既定: プロジェクト設定)
  "metadata_filter": { "category": "薬効" }, // 任意
  "include_trace": false                     // 任意(既定: false)
}

// Response 200(10秒以内。超過時は §7.7 の契約で 202)
{
  "trace_id": "uuid",
  "session_id": "uuid",
  "response": "頭痛には、次の薬が...",
  "is_empty": false,          // true なら「データなし」応答だったことを示す
  "context_chunks": [         // 生成に使ったチャンク(出典表示用の最小情報)
    { "chunk_id": "uuid", "source_id": "uuid", "section_title": "効能・効果", "found_in": ["meta+vec"] }
  ],
  "response_time_ms": 4321,
  "trace": { ... }            // include_trace: true のとき QueryTrace 全体
}
```

### POST /projects/{project_id}/search — 検索のみ(Q1〜Q3)

Request は /query と同形式(session_id は不要)。

```jsonc
// Response 200
{
  "trace_id": "uuid",
  "transformed_query": { "original": "...", "query": "...", "mode": "llm_rewrite" },
  "context": {
    "chunks": [
      { "chunk_id": "...", "text": "...", "section_title": "...", "seq": 0,
        "metadata": { ... }, "score": 0.32, "score_type": "cosine_distance",
        "found_in": ["meta+vec", "fulltext"] }
    ],
    "is_empty": false,
    "dropped": [ ... ]        // 落としたものも返す(調整作業の主役)
  },
  "passes": [                 // パス別の生結果(search loosely の観察用)
    { "name": "meta+vec", "hit_count": 10, "elapsed_ms": 45 }
  ]
}
```

### POST /projects/{project_id}/compare — 比較モード

```jsonc
// Request
{
  "user_input": "頭痛に効く薬は?",
  "model_keys": ["bge_m3", "plamo_emb_1b"],  // 任意(既定: プロジェクトに登録済みの全モデル)
  "transform_mode": "passthrough",            // 任意。比較の公平性のため全モデル共通
  "retrieval_plan": { ... },                  // 任意。同上
  "metadata_filter": { ... }                  // 任意
}

// Response 200
{
  "comparisons": [
    { "model_key": "bge_m3",       "trace_id": "uuid", "result": { search と同形式 } },
    { "model_key": "plamo_emb_1b", "trace_id": "uuid", "result": { search と同形式 } }
  ]
}
```

### 設計判断

1. **/query の応答は「会話に必要な最小限」**。詳細は trace_id 経由(include_trace: true か
   GET /traces/{trace_id})の 2 段構え
2. **/search は dropped まで返す**。評価・調整用エンドポイントであり「何を落としたか」が主役級の情報
3. **/compare は 1 モデル = 1 トレース**(generation: null)。比較専用の記録形式は作らず、
   トレースの世界を一元化する

---

## 7.12 エンベディング / ジョブ / トレース系 API 詳細(確定)

### GET /embedding-models — 台帳一覧

```jsonc
// Response 200
{
  "models": [
    { "model_key": "bge_m3", "model_name": "BAAI/bge-m3",
      "dimensions": 1024, "vector_type": "vector", "is_active": true }
  ]
}
```

### POST /projects/{project_id}/embed — 一括エンベディング

```jsonc
// Request
{
  "model_key": "plamo_emb_1b",   // 必須
  "source_ids": ["uuid"]         // 任意(省略時: プロジェクト内の全ソース)
}
// Response: §7.7 ハイブリッド契約
// result: { "model_key": "plamo_emb_1b", "embedded": 322, "skipped": 0 }
```

**冪等**: 既にベクトルがあるチャンクはスキップする。何度呼んでも安全であり、
モデル追加後の「追いつき処理」がこの API 1 本で済む。

### GET /jobs/{job_id} — ジョブ照会

§7.7 で定義済みの形式(status: processing/completed/failed + progress / result / error)。

### GET /projects/{project_id}/traces — トレース一覧

```jsonc
// Query パラメータ:
//   ?session_id=uuid&from=2026-07-01T00:00:00Z&to=...&kind=query|search|compare&limit=50&offset=0
// Response 200
{
  "traces": [
    { "trace_id": "uuid", "kind": "query", "session_id": "uuid",
      "user_input": "頭痛に効く薬は?", "model_key": "bge_m3",
      "is_empty": false, "has_generation": true,
      "response_time_ms": 4321, "created_at": "..." }
  ],
  "total": 123
}
```

一覧は軽量サマリのみ。`kind` は /query・/search・/compare のどれ由来かを示す(評価時のフィルタ用)。

### GET /traces/{trace_id} — トレース詳細

Response 200: **QueryTrace 全体**(§7.5.2 の構造そのまま。
request / transformed / retrieval(パス別生結果)/ context(dropped 込み)/ generation(prompt 全文込み))

---

以上で全エンドポイントの詳細定義は完了。実装順序は [implementation_plan.md](./implementation_plan.md) を参照。

---

