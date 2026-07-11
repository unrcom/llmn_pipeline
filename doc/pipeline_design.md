# llamune RAG パイプライン再定義・再実装 設計書

- 作成日: 2026-07-11
- 版: v2(2026-07-11 改訂)
- ステータス: ドラフト(テーブル設計まで合意済み、Query 側インターフェース定義は未着手)

---

## 1. 背景と目的

llamune は閉域 LLM による RAG 実装基盤である。これまでの経緯:

1. 閉域 LLM を使ったローカル動作のチャットアプリとして開始
2. 閉域 LLM 単体ではクラウド LLM に対して実用面で見劣りすることが判明
3. ファインチューニング(FT)基盤アプリでベースモデルにない知識の投入を試行したが、うまくいかず
4. RAG による解決に方針転換し、動作するものができてきた

これまで「なんとなく」(バイブコーディング)で作ってきたものを、**RAG をパイプラインとして再定義し、再構築する**のが本プロジェクトの目的である。

### 1.1 方針

- **作り直しが主**: パイプライン定義を先に固め、実装もステージ単位で組み直す
- **スコープは RAG 単体**: FT 連携(`[SEARCH]` 構文等)は今回のスコープに含めない
- **本プロジェクトは REST API の実装のみ。フロントエンドは含まない**
- 既存の設計知見「search loosely, control at generation(緩く検索して生成で制御)」を踏襲する
- ゼロ件検索時は明示的に「データなし」を返す方針を踏襲する

### 1.2 開発の進め方

- **API のインターフェース設計まではバイブコーディング**(対話的に設計を固める)
- **インターフェース確定後は API ごとにエージェンティックコーディング**(API 単位でエージェントにタスクを委譲して実装)
- この進め方が成立するために、本設計書と API インターフェース定義が「エージェントに渡す仕様」として自立している必要がある

---

## 2. パイプラインのステージ分割(確定)

パイプラインは Ingestion(投入系)と Query(検索・生成系)の 2 系統、計 10 ステージに分割する。各ステージは「決まったデータ構造を受け取り、決まったデータ構造を返す純粋な変換」として定義する。

### 2.1 Ingestion パイプライン(投入系)

| # | ステージ | 責務 |
|----|------------|------|
| I1 | Load | ファイル/テキストの取り込み、ソースメタデータ付与(project_id / source_id 等) |
| I2 | Preprocess | 正規化(文字コード、空白、不要要素の除去) |
| I3 | Chunk | セクション見出し付き段落分割(500 文字方式がベース) |
| I4 | Embed | エンベディングモデルによるベクトル化 |
| I5 | Index | ベクトルストアへの登録(delete-then-create 方式) |

### 2.2 Query パイプライン(検索・生成系)

| # | ステージ | 責務 |
|----|------------------|------|
| Q1 | Query Transform | ユーザー入力から検索クエリを作る。**モードを選択可能: (a) 素通し (b) LLM による書き換え** |
| Q2 | Retrieve | ベクトル検索(recall-first、閾値はプロジェクト×モデル設定) |
| Q3 | Context Build | 検索結果の**リランク**・整形・件数制御・ゼロ件判定 |
| Q4 | Generate | プロンプト組み立て + MLX 生成(「生成で制御」の実体) |
| Q5 | Log | トレーシング(クエリ・距離・応答・所要時間) |

### 2.3 観測・評価層

ステージ 1〜10 が「リクエストごとに流れるパイプライン」であるのに対し、**評価はログを横断して見るオフラインの活動**であり、パイプラインを観測する層として位置づける。

- 評価はトレーシング(Q5 Log の出力)から始める
- 最初の具体的装置は **エンベディングモデル比較モード**(後述 §4.3)

### 2.4 設計上の判断

- **独立した Rerank ステージは設けない**。ただしリランク(検索結果の並べ替え・絞り込み)は **Q3 Context Build の責務の一部**として行う。recall-first で緩く取った結果を Q3 で制御する、という「search loosely, control at generation」の構造をステージ分割にそのまま写した形
- **Q3(Context Build)を独立ステージ化**: ゼロ件時の「データなし」応答の判定も Q3 に置き、生成ステージ(Q4)が判断を持たない構造にする
- **Q1 Query Transform はモード選択制**:
  - `passthrough`: ユーザー入力をそのまま検索クエリにする
  - `llm_rewrite`: LLM で検索に適したクエリに書き換える
  - モードはプロジェクト設定で既定を持ち、リクエスト単位で上書き可能とする(比較・評価のため)

---

## 3. ベクトルストア: PostgreSQL + pgvector(確定)

ChromaDB から **PostgreSQL + pgvector** に移行する。

### 3.1 採用理由

- **メタデータの表現力**: JSONB で自由な構造を持てる。フィルタは SQL で記述できる(ChromaDB は scalar 値のみの制限があった)
- **運用の一元化**: chat_logs / sessions / projects と同居し、**トレースと検索結果を JOIN して評価できる**
- **トランザクション**: delete-then-create の入れ替えが 1 トランザクションで安全に実行できる
- **バックアップ・マイグレーション**: node-pg-migrate の既存フローに乗る

### 3.2 技術メモ

- pgvector の HNSW インデックスは `vector` 型で **2000 次元が上限**。それを超えるモデルは `halfvec`(半精度、4000 次元まで索引可)を使う
- 想定規模(数百〜数万チャンク)では検索速度は実用上問題なし

---

## 4. エンベディングモデルの複数並存(確定)

パイプラインがうまく機能しないとき、原因の切り分け(チャンキングか、エンベディングか、閾値か)のために**エンベディングモデルを差し替え可能な部品にする**。

### 4.1 初期登録する 3 モデル(確定)

| model_key | モデル | 次元 | ベクトル型 | 特徴 |
|-----------|--------|------|-----------|------|
| `bge_m3` | BAAI/bge-m3 | 1024 | vector(1024) | 多言語定番。現行 llamune の実績あり |
| `me5_large` | intfloat/multilingual-e5-large | 1024 | vector(1024) | 多言語定番の対抗馬 |
| `plamo_emb_1b` | pfnet/plamo-embedding-1b | 2048 | **halfvec(2048)** | 日本語特化、JMTEB Retrieval 首位級。Apache 2.0 |

「多言語定番 / 多言語対抗 / 日本語特化」の 3 枚で比較する構成。

### 4.2 重要な設計発見: クエリ/文書エンコードの非対称性

モデルによってクエリと文書のエンコード方法が異なる:

| モデル | 文書側 | クエリ側 |
|--------|--------|---------|
| bge-m3 | そのまま | そのまま |
| multilingual-e5-large | `passage: ` プレフィックス必須 | `query: ` プレフィックス必須 |
| PLaMo-Embedding-1B | `encode_document` メソッド | `encode_query` メソッド(内部でプレフィックス付与) |

→ **Embedder インターフェースは `embed_documents()` と `embed_query()` を別メソッドとして定義する**。この差異は Embedder 実装の内部に閉じ込め、パイプライン(I4 / Q2)からは見えないようにする。

### 4.3 設計原則: 「チャンクはモデル非依存、ベクトルはモデル別」

- I3 Chunk までの成果物(テキスト+メタデータ)はモデルと無関係 → 1 回だけ作る
- ベクトルはモデルごとに別テーブルで並存 → モデル切り替え時の再チャンキング不要、I4 Embed の再実行のみ

### 4.4 制約と規則

- HNSW インデックスは次元固定のため、**モデルごとにベクトルテーブルを分け、モデル登録時にマイグレーションで作成**する。台帳テーブル(embedding_models)で管理する
- Q2 Retrieve でクエリをベクトル化するモデルは、検索対象ベクトルと**必ず同一モデル**であること。プロジェクト設定で強制する
- 距離スコアの分布はモデルごとに異なるため、**検索閾値(threshold)はモデルとセット**で持つ

### 4.5 比較モード

同一クエリを複数モデルで同時に検索し、結果(ヒットチャンク・距離分布)を並べて確認できる API を提供する。「観測・評価層」の最初の実体。

- 実現方法: project_embedding_settings に複数行を登録するだけ。Q2 Retrieve を「モデル指定を受け取る純粋な関数」として定義しておけば、通常検索(is_default の 1 モデル)も比較モード(N モデル並列)も同じ関数の呼び方が違うだけになる
- 本プロジェクトは API のみのため、比較結果は API レスポンス(JSON)として返す。画面は将来のフロントエンド側の責務

---

## 5. メタデータ設計(確定)

### 5.1 2 層構造

- **ソース由来メタデータ**: ユーザーが SourceDocument に付与(sources.metadata)
- **チャンク固有メタデータ**: ユーザーが Chunk に付与。さらに付与経路で 2 カラムに分離する
  - `ingest_metadata`: 投入前付与(front matter 付き Markdown 等の構造化形式)由来。再取り込みで毎回再生成される
  - `user_metadata`: 投入後に API 経由で編集したもの。引き継ぎルール(§6)の対象

### 5.2 マージ規則

インデックス/検索時の実効メタデータは以下の順でマージする(後勝ち):

```
source.metadata ⊕ chunk.ingest_metadata ⊕ chunk.user_metadata
```

### 5.3 付与のタイミング

**両方**をサポートする。

1. **投入前付与**(先に実装): front matter 付き Markdown 等で用意し、I1/I3 が解釈して載せる。パイプライン本体の一部
2. **投入後付与**(後で実装): チャンク単位のメタデータ更新 API(UPDATE)

編集済みメタデータを front matter 形式に**エクスポート**できる API を用意し、「API で試行錯誤 → 良かった設定をソース側に反映 → 再取り込みしても残る」という運用ループを成立させる(既存のバックアップ形式 YAML front matter + Markdown と整合)。

---

## 6. 再取り込み時のメタデータ引き継ぎ(確定)

再取り込み(delete-then-create)時、手編集メタデータ(user_metadata)の扱い:

- **変化しなかったチャンクのメタデータは残す**
- **変化したチャンクのメタデータは消える。ただしユーザーにわかるようにする**

### 6.1 引き継ぎルール: content_hash 完全一致

曖昧な類似マッチングは採用しない。決定的なルールにする。

1. チャンクに **content_hash**(正規化済みテキストの SHA-256)を持たせる
2. 再取り込み時、同一ソース内で旧チャンクと新チャンクを content_hash で突き合わせる
   - **一致** → 「変化しなかった」とみなし user_metadata を引き継ぐ
   - **不一致(旧にしかない)** → メタデータは消える。孤児レコードとして記録する
3. 同一ハッシュが複数ある場合は seq 順で対応付ける

### 6.2 可視化

- **ingest_runs**: 取り込み実行ごとに total / carried(引き継ぎ) / new / dropped(消滅) を記録
- **orphaned_metadata**: 消えたメタデータを旧テキストごと退避。resolved フラグで再適用済みを管理し、未解決の孤児だけを返す API を用意する
- 取り込み API のレスポンスで「12 チャンク中 9 引き継ぎ / 3 消滅」を返す

トランザクション内で「旧を読む → 突き合わせ → 新を書く → 孤児を記録 → 旧を消す」を原子的に実行する。

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

※ Query 側(Q1〜Q5)のインターフェース定義は次ステップで行う。

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

---

## 9. 技術スタック(前提)

| 領域 | 選定 |
|------|------|
| 提供形態 | **REST API のみ(フロントエンドなし)** |
| バックエンド | FastAPI (Python) |
| DB | PostgreSQL(Docker)+ pgvector |
| マイグレーション | node-pg-migrate |
| エンベディング | bge-m3 / multilingual-e5-large / PLaMo-Embedding-1B の 3 モデル並存 |
| LLM 推論 | MLX(mlx-lm)、gemma-3-12b 等、Apple Silicon ローカル(Q1 llm_rewrite と Q4 Generate で使用) |
| データモデル | Pydantic |

---

## 10. 未決事項・次のステップ

### 未決事項

- Q1 `llm_rewrite` の具体仕様(書き換えプロンプト、使用モデル、失敗時のフォールバック)
- Q3 Context Build のリランク方式(距離順のまま件数制御か、別のスコアリングを入れるか)
- PLaMo-Embedding-1B の Apple Silicon 上での推論方法(transformers + MPS で動かすか、量子化するか)— 要検証
- API エンドポイントの一覧と URL 設計

### 決定済み(v2 で確定)

- スキーマ名: `rag`
- projects テーブル: 新設
- 初期 3 モデル: bge-m3 / multilingual-e5-large / PLaMo-Embedding-1B
- Q1 Query Transform: passthrough / llm_rewrite の選択制
- リランクは Q3 Context Build の責務
- 提供形態は REST API のみ

### 次のステップ

1. **Query 側インターフェース定義**(Q1〜Q5 の入出力データ構造)← 次はここ
2. **API インターフェース設計**(エンドポイント一覧、リクエスト/レスポンス定義)← ここまでがバイブコーディングの範囲
3. インターフェース確定 → **API ごとにエージェンティックコーディング**
4. DDL 確定 → マイグレーション作成
5. 設定と評価の詳細(トレーススキーマ、比較モード API)
