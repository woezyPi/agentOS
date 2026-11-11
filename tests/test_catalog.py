"""Offline catalogue metadata, cache and credential-isolation checks.
Run: .venv/bin/python tests/test_catalog.py
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from claude_clone import models as MD
from claude_clone.config import load_settings
from claude_clone.server import create_app
from claude_clone.graph import build_graph
from fastapi.testclient import TestClient
from test_smoke import FakeLLM


class CatalogTests(unittest.TestCase):
    def test_metadata(self):
        rows = MD.normalize_catalog([
            {"id": "lab/free", "name": "Free", "context_length": 128000, "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools"]},
            {"id": "lab/paid", "pricing": {"prompt": "0", "completion": "0.01"}, "supported_parameters": []},
            {"id": "lab/unknown", "max_model_len": 32000},
            {"id": "lab/bad-price", "pricing": {"prompt": "oops", "completion": "0"}},
            {"id": "lab/image", "architecture": {"output_modalities": ["image"]}},
            {"id": "lab/audio-only", "architecture": {"input_modalities": ["audio"], "output_modalities": ["text"]}},
            {"id": "lab/embed"}, {"id": None}, "bad",
        ])
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(len(rows), 4)
        self.assertTrue(by_id["lab/free"]["free"])
        self.assertTrue(by_id["lab/free"]["tools"])
        self.assertEqual(by_id["lab/free"]["window"], 128000)
        self.assertFalse(by_id["lab/paid"]["free"])
        self.assertFalse(by_id["lab/paid"]["tools"])
        self.assertIsNone(by_id["lab/unknown"]["free"])
        self.assertIsNone(by_id["lab/unknown"]["tools"])
        self.assertIsNone(by_id["lab/bad-price"]["free"])
        self.assertEqual(by_id["lab/unknown"]["window"], 32000)

    def test_public_catalog_cache_and_no_credentials(self):
        real_client = httpx.AsyncClient
        requests = []
        unavailable = False
        def handle(request):
            requests.append(request)
            self.assertEqual(str(request.url), MD.OPENROUTER_MODELS_URL)
            self.assertNotIn("authorization", request.headers)
            if unavailable:
                return httpx.Response(503)
            return httpx.Response(200, json={"data": [{"id": "lab/free", "supported_parameters": ["tools"]}]})
        with patch.object(MD, "_OPENROUTER_CACHE", {"t": 0.0, "data": []}), patch.object(MD.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw)):
            rows, error = asyncio.run(MD.list_openrouter())
            self.assertIsNone(error)
            self.assertEqual(rows[0]["id"], "lab/free")
            asyncio.run(MD.list_openrouter())
            self.assertEqual(len(requests), 1)
            unavailable = True
            cached, error = asyncio.run(MD.list_openrouter(force=True))
            self.assertEqual(cached, rows)
            self.assertIn("indisponible", error)

    def test_catalog_endpoint_auth_and_sources(self):
        async def configured(settings, force=False):
            return [{"id": "local/model"}], None
        async def public(force=False):
            return [{"id": "public/model"}], None
        with patch.object(MD, "list_available", configured), patch.object(MD, "list_openrouter", public):
            client = TestClient(create_app(token="catalog", hosts={"testserver"}, graph=build_graph(supervisor_llm=FakeLLM([]), coder_llm=FakeLLM([]))), base_url="http://testserver")
            headers = {"X-CC-Token": "catalog"}
            self.assertEqual(client.get("/api/models/catalog").status_code, 401)
            self.assertEqual(client.get("/api/models/catalog", headers=headers).json()["available"][0]["id"], "local/model")
            self.assertEqual(client.get("/api/models/catalog?source=openrouter", headers=headers).json()["available"][0]["id"], "public/model")
            self.assertEqual(client.get("/api/models/catalog?source=https://example.com", headers=headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
