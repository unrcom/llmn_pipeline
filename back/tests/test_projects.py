from tests.conftest import VALID_API_KEY

AUTH_HEADERS = {"X-API-Key": VALID_API_KEY}


class TestAuth:
    async def test_missing_api_key_returns_401(self, client):
        response = await client.get("/projects")
        assert response.status_code == 401
        assert response.json() == {
            "error": {"code": "invalid_api_key", "message": "Invalid or missing API key"}
        }

    async def test_wrong_api_key_returns_401(self, client):
        response = await client.get("/projects", headers={"X-API-Key": "llmn_dev_not_a_valid_key"})
        assert response.status_code == 401

    async def test_valid_api_key_passes_auth(self, client):
        response = await client.get("/projects", headers=AUTH_HEADERS)
        assert response.status_code == 200


class TestCreateProject:
    async def test_create_project_with_defaults(self, client):
        response = await client.post(
            "/projects", headers=AUTH_HEADERS, json={"name": "薬効RAG"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "薬効RAG"
        assert body["description"] is None
        assert body["query_transform_mode"] == "passthrough"
        assert body["retrieval_plan"]["passes"] == [
            {"name": "meta+vec", "strategy": "vector", "top_k": 10, "use_metadata_filter": True, "enabled": True},
            {"name": "vec_only", "strategy": "vector", "top_k": 3, "use_metadata_filter": False, "enabled": True},
            {"name": "fulltext", "strategy": "fulltext", "top_k": 5, "use_metadata_filter": False, "enabled": True},
        ]
        assert "project_id" in body
        assert "created_at" in body
        assert "updated_at" in body

    async def test_create_project_with_explicit_retrieval_plan(self, client):
        custom_plan = {
            "passes": [
                {"name": "vec_only", "strategy": "vector", "top_k": 5, "use_metadata_filter": False, "enabled": True}
            ]
        }
        response = await client.post(
            "/projects",
            headers=AUTH_HEADERS,
            json={
                "name": "カスタムプラン",
                "description": "説明",
                "query_transform_mode": "llm_rewrite",
                "retrieval_plan": custom_plan,
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["query_transform_mode"] == "llm_rewrite"
        assert body["retrieval_plan"] == custom_plan

    async def test_missing_name_returns_400(self, client):
        response = await client.post("/projects", headers=AUTH_HEADERS, json={"description": "説明のみ"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_invalid_transform_mode_returns_400(self, client):
        response = await client.post(
            "/projects",
            headers=AUTH_HEADERS,
            json={"name": "不正モード", "query_transform_mode": "invalid_mode"},
        )
        assert response.status_code == 400

    async def test_invalid_retrieval_plan_strategy_returns_400(self, client):
        response = await client.post(
            "/projects",
            headers=AUTH_HEADERS,
            json={
                "name": "不正戦略",
                "retrieval_plan": {
                    "passes": [
                        {"name": "x", "strategy": "not_a_strategy", "top_k": 1, "use_metadata_filter": False, "enabled": True}
                    ]
                },
            },
        )
        assert response.status_code == 400

    async def test_create_project_without_api_key_returns_401(self, client):
        response = await client.post("/projects", json={"name": "無認証"})
        assert response.status_code == 401


class TestListProjects:
    async def test_list_returns_created_projects(self, client):
        await client.post("/projects", headers=AUTH_HEADERS, json={"name": "プロジェクトA"})
        await client.post("/projects", headers=AUTH_HEADERS, json={"name": "プロジェクトB"})

        response = await client.get("/projects", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        names = [p["name"] for p in body["projects"]]
        assert names == ["プロジェクトA", "プロジェクトB"]

    async def test_list_empty(self, client):
        response = await client.get("/projects", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json() == {"projects": []}
