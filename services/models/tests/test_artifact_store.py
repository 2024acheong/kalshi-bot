from __future__ import annotations

from services.models.shared import artifact_store


def test_artifact_store_local_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(artifact_store, "ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_ARTIFACT_STORAGE", "local")
    model = {"kind": "stand-in", "weights": [1, 2, 3]}

    path = artifact_store.save_artifact(model, "test_model", "v1")

    assert path == str(tmp_path / "test_model" / "v1" / "model.pkl")
    assert artifact_store.load_artifact(path) == model


def test_artifact_store_supabase_upload_and_download(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(artifact_store, "ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(artifact_store, "MODEL_ARTIFACT_BUCKET", "test-artifacts")
    monkeypatch.setenv("MODEL_ARTIFACT_STORAGE", "supabase")
    model = {"kind": "stand-in", "weights": [4, 5, 6]}

    class FakeBucket:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}
            self.upload_options = None

        def upload(self, path, payload, file_options):
            self.objects[path] = payload
            self.upload_options = file_options

        def download(self, path):
            return self.objects[path]

    class FakeStorage:
        def __init__(self) -> None:
            self.bucket = FakeBucket()
            self.created_buckets = []

        def list_buckets(self):
            return []

        def create_bucket(self, bucket, options):
            self.created_buckets.append((bucket, options))

        def from_(self, bucket):
            assert bucket == "test-artifacts"
            return self.bucket

    class FakeClient:
        def __init__(self) -> None:
            self.storage = FakeStorage()

    fake_client = FakeClient()
    monkeypatch.setattr(
        "services.models.shared.model_registry.get_supabase_client",
        lambda: fake_client,
    )

    uri = artifact_store.save_artifact(model, "test_model", "v2")

    assert uri == "supabase://test-artifacts/test_model/v2/model.pkl"
    assert (tmp_path / "test_model" / "v2" / "model.pkl").exists()
    assert fake_client.storage.created_buckets == [
        ("test-artifacts", {"public": False})
    ]
    assert fake_client.storage.bucket.upload_options == {
        "content-type": "application/octet-stream",
        "upsert": "true",
    }
    assert artifact_store.load_artifact(uri) == model
