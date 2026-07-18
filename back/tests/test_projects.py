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

    async def test_empty_name_returns_400(self, client):
        response = await client.post("/projects", headers=AUTH_HEADERS, json={"name": ""})
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


NIL_UUID = "00000000-0000-0000-0000-000000000000"


async def _create_project(client, **overrides):
    payload = {"name": "詳細取得用プロジェクト", **overrides}
    response = await client.post("/projects", headers=AUTH_HEADERS, json=payload)
    assert response.status_code == 201
    return response.json()


class TestGetProject:
    async def test_get_returns_detail_with_empty_embedding_settings(self, client):
        created = await _create_project(client)

        response = await client.get(f"/projects/{created['project_id']}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["project_id"] == created["project_id"]
        assert body["name"] == "詳細取得用プロジェクト"
        assert body["embedding_settings"] == []

    async def test_get_nonexistent_returns_404(self, client):
        response = await client.get(f"/projects/{NIL_UUID}", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json() == {
            "error": {"code": "project_not_found", "message": "Project not found"}
        }

    async def test_get_invalid_uuid_returns_400(self, client):
        response = await client.get("/projects/not-a-uuid", headers=AUTH_HEADERS)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_get_without_api_key_returns_401(self, client):
        created = await _create_project(client)
        response = await client.get(f"/projects/{created['project_id']}")
        assert response.status_code == 401


class TestPatchProject:
    async def test_patch_updates_only_given_field(self, client):
        created = await _create_project(client, description="元の説明")

        response = await client.patch(
            f"/projects/{created['project_id']}",
            headers=AUTH_HEADERS,
            json={"description": "更新後の説明"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["description"] == "更新後の説明"
        assert body["name"] == created["name"]
        assert body["query_transform_mode"] == created["query_transform_mode"]
        assert body["retrieval_plan"] == created["retrieval_plan"]
        assert body["updated_at"] != created["updated_at"]

    async def test_patch_nonexistent_returns_404(self, client):
        response = await client.patch(
            f"/projects/{NIL_UUID}", headers=AUTH_HEADERS, json={"name": "存在しない"}
        )
        assert response.status_code == 404

    async def test_patch_invalid_uuid_returns_400(self, client):
        response = await client.patch(
            "/projects/not-a-uuid", headers=AUTH_HEADERS, json={"name": "x"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_patch_invalid_transform_mode_returns_400(self, client):
        created = await _create_project(client)

        response = await client.patch(
            f"/projects/{created['project_id']}",
            headers=AUTH_HEADERS,
            json={"query_transform_mode": "invalid_mode"},
        )
        assert response.status_code == 400

    async def test_patch_null_name_returns_400(self, client):
        created = await _create_project(client)

        response = await client.patch(
            f"/projects/{created['project_id']}", headers=AUTH_HEADERS, json={"name": None}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_patch_empty_name_returns_400(self, client):
        created = await _create_project(client)

        response = await client.patch(
            f"/projects/{created['project_id']}", headers=AUTH_HEADERS, json={"name": ""}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_patch_null_description_clears_it(self, client):
        created = await _create_project(client, description="元の説明")

        response = await client.patch(
            f"/projects/{created['project_id']}", headers=AUTH_HEADERS, json={"description": None}
        )
        assert response.status_code == 200
        assert response.json()["description"] is None

    async def test_patch_without_api_key_returns_401(self, client):
        created = await _create_project(client)
        response = await client.patch(
            f"/projects/{created['project_id']}", json={"name": "無認証更新"}
        )
        assert response.status_code == 401


class TestDeleteProject:
    async def test_delete_then_get_returns_404(self, client):
        created = await _create_project(client)

        delete_response = await client.delete(
            f"/projects/{created['project_id']}", headers=AUTH_HEADERS
        )
        assert delete_response.status_code == 204
        assert delete_response.content == b""

        get_response = await client.get(f"/projects/{created['project_id']}", headers=AUTH_HEADERS)
        assert get_response.status_code == 404

    async def test_delete_nonexistent_returns_404(self, client):
        response = await client.delete(f"/projects/{NIL_UUID}", headers=AUTH_HEADERS)
        assert response.status_code == 404

    async def test_delete_invalid_uuid_returns_400(self, client):
        response = await client.delete("/projects/not-a-uuid", headers=AUTH_HEADERS)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"

    async def test_delete_without_api_key_returns_401(self, client):
        created = await _create_project(client)
        response = await client.delete(f"/projects/{created['project_id']}")
        assert response.status_code == 401
