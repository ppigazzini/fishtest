# ruff: noqa: ANN201, ANN206, B904, D100, D101, D102, E501, EM101, EM102, INP001, PLC0415, PT009, S105, S106, TRY003

import unittest

try:
    import fastapi_util
except ModuleNotFoundError:  # pragma: no cover
    from tests import fastapi_util


class TestGlueErrorsFastAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Skips cleanly if FastAPI/TestClient (and its deps like httpx) aren't available.
        FastAPI, TestClient = fastapi_util.require_fastapi()

        try:
            import util as test_util
        except ModuleNotFoundError:  # pragma: no cover
            from tests import util as test_util

        cls.rundb = test_util.get_rundb()
        cls.FastAPI = FastAPI
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls):
        cls.rundb.userdb.clear_cache()
        cls.rundb.pgndb.delete_many({})
        cls.rundb.runs.delete_many({})
        cls.rundb.runs.drop()
        cls.rundb.conn.close()

    def test_ui_404_is_html(self):
        client = fastapi_util.make_test_client(
            rundb=self.rundb,
            include_api=False,
            include_views=True,
        )

        response = client.get("/this-ui-route-does-not-exist")
        self.assertEqual(response.status_code, 404)

        content_type = response.headers.get("content-type", "")
        self.assertTrue(
            content_type.startswith("text/html"),
            msg=f"expected text/html content-type, got {content_type}",
        )

    def test_api_404_is_json(self):
        client = fastapi_util.make_test_client(
            rundb=self.rundb,
            include_api=True,
            include_views=False,
        )

        response = client.get("/api/this-api-route-does-not-exist")
        self.assertEqual(response.status_code, 404)

        content_type = response.headers.get("content-type", "")
        self.assertTrue(
            content_type.startswith("application/json"),
            msg=f"expected application/json content-type, got {content_type}",
        )
        self.assertEqual(response.json(), {"detail": "Not Found"})

    def test_worker_validation_error_is_shaped(self):
        from pydantic import BaseModel

        app = fastapi_util.build_test_app(
            rundb=self.rundb,
            include_api=False,
            include_views=False,
        )

        class Body(BaseModel):
            required_field: int

        @app.post("/api/request_task")
        def _worker_validation_probe(body: Body):
            _ = body
            return {"ok": True}

        client = self.TestClient(app)
        response = client.post("/api/request_task", json={})

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body.get("error"), "/api/request_task: invalid request")
        self.assertTrue(isinstance(body.get("duration"), (int, float)))


if __name__ == "__main__":
    unittest.main()
