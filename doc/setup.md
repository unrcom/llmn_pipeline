# セットアップ手順

GitHub からクローンした直後の状態から、コマンドラインだけで開発環境を構築する手順。
上から順に実行すれば `GET /health` の確認、DB の確認まで到達できる。

## 0. 前提ツールの確認

以下がインストール済みであること。

```bash
pyenv --version      # pyenv
pyenv versions        # 3.13.13 系が入っているか確認。なければ: pyenv install 3.13.13
node --version         # v20 以上を推奨
npm --version
docker --version
docker compose version
```

`back/` 既存の Docker(llamune 等)がポート 5434-5439 を使用中のため、
本プロジェクトの PostgreSQL はポート 5440 で起動する(後述の docker-compose.yml で設定済み)。

### ツールが無い場合のインストール(macOS / Homebrew)

```bash
# pyenv
brew install pyenv
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.zshrc
echo 'command -v pyenv >/dev/null && eval "$(pyenv init -)"' >> ~/.zshrc
source ~/.zshrc
pyenv install 3.13.13

# node / docker が無い場合
brew install node
brew install --cask docker   # 初回は Docker Desktop を一度起動すること
```

pyenv global の変更は不要(back/.python-version により back/ 配下では自動で 3.13.13 になる)。

## 1. バックエンド(FastAPI)のセットアップ

```bash
cd back
pyenv local 3.13.13    # .python-version として既にコミット済みのはず
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # 必要に応じて値を編集(DB_PORT=5440 が既定)
```

## 2. DB(PostgreSQL + pgvector)の起動とマイグレーション

別ターミナルで実行(`back/.venv` は activate したまま、`db/` は Node のプロジェクト)。

```bash
cd db
npm install

cp .env.example .env   # back/.env の DB_NAME / DB_USER / DB_PASSWORD / DB_PORT と値を揃えること

docker compose up -d    # PostgreSQL(pgvector 同梱)をポート 5440 で起動
```

初回起動時(named volume が空の状態)には、`db/init/01-create-test-db.sql` により
開発 DB `llmn_pipeline` に加えてテスト専用 DB `llmn_pipeline_test` が自動作成される
(§6 参照)。postgres の初期化スクリプトは **volume が空のときにしか実行されない**ため、
依頼 1.5 より前に作成した既存の volume を使い続けている場合は
`docker compose down -v` してから `up -d` し直すこと(**開発 DB のデータも消える**)。

コンテナが起動するまで数秒待ってから、マイグレーションを実行する。

```bash
npm run migrate:up
```

`rag` スキーマ・全テーブル・監査トリガー・embedding_models の初期 3 行が作成される。

## 3. アプリの起動

`back/` に戻り、venv を activate したまま起動する。

```bash
cd ../back
source .venv/bin/activate   # 別ターミナルの場合
uvicorn app.main:app --reload --port 8000
```

## 4. 動作確認

### 4.1 ヘルスチェック

```bash
curl -i http://127.0.0.1:8000/health
# HTTP/1.1 200 OK
# {"status":"ok"}
```

### 4.2 DB の確認(psql)

```bash
cd db
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline -c "\dt rag.*"
```

`rag` スキーマ配下に 12 テーブル(projects / sources / chunks / embedding_models /
chunk_embeddings_bge_m3 / chunk_embeddings_me5_large / chunk_embeddings_plamo_emb_1b /
ingest_runs / orphaned_metadata / project_embedding_settings / jobs / audit_log)が
表示されることを確認する。

```bash
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline \
  -c "SELECT model_key, dimensions, vector_type FROM rag.embedding_models ORDER BY model_key;"
```

`bge_m3` / `me5_large` / `plamo_emb_1b` の 3 行が返ることを確認する。

```bash
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline \
  -c "SELECT tgname, tgrelid::regclass FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname;"
```

`audit_projects` / `audit_sources` / `audit_chunks` / `audit_project_embedding_settings` /
`audit_orphaned_metadata` の 5 トリガーが表示されることを確認する。

## 5. マイグレーションのロールバック(確認用)

```bash
cd db
npm run migrate:down -- 4   # 現時点のマイグレーション本数(4)を指定して全ロールバック
```

引数を省略した場合は直近 1 本のみ戻る点に注意。全て戻したことは以下で確認できる。

```bash
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline -c "\dn"
# rag スキーマが存在しないこと(public のみ)を確認
```

再度適用する場合は `npm run migrate:up` を実行する。

## 6. テストの実行(依頼 1.5: テスト DB の分離)

テストは開発 DB(`llmn_pipeline`)とは別の専用 DB `llmn_pipeline_test` に対して実行される
(同一コンテナ内、§2 の初回起動時に `db/init/01-create-test-db.sql` で作成される)。
接続先 DB 名は `back/tests/conftest.py` 内で無条件に固定されており、`.env` の `DB_NAME` を
変更してもテストの接続先は変わらない(開発 DB でテストが走る事故を構造的に排除するため)。

```bash
cd back
source .venv/bin/activate
pip install -r requirements-dev.txt

pytest
```

pytest 実行時、セッション開始時に `db/` ディレクトリで `npm run migrate:up` が
`DATABASE_URL` を `llmn_pipeline_test` 向けに上書きして自動実行される
(`db/` で `npm install` 済みであることが前提。§2 参照)。

### 動作確認: 開発 DB のデータが pytest 実行後も残ること

```bash
# 開発 DB にマーカー行を入れる
cd db
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline \
  -c "INSERT INTO rag.projects (name) VALUES ('marker');"

# テストを実行(11 件全て成功すること)
cd ../back
pytest

# 開発 DB にマーカー行が残っていることを確認(1 row 返れば OK)
cd ../db
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline \
  -c "SELECT name FROM rag.projects WHERE name = 'marker';"

# 後片付け
docker compose exec -T postgres psql -U llmn_pipeline -d llmn_pipeline \
  -c "DELETE FROM rag.projects WHERE name = 'marker';"
```

## 7. 後片付け

```bash
cd db
docker compose down          # コンテナ停止(データは named volume に残る)
docker compose down -v       # データも含めて完全に削除する場合(テスト DB も消える)
```
