"""Minimal in-memory stand-in for google.cloud.firestore.Client (no network)."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional


class FakeSnapshot:
    def __init__(self, doc_id: str, data: Optional[dict]):
        self.id = doc_id
        self._data = copy.deepcopy(data)
        self.exists = data is not None

    def to_dict(self) -> Optional[dict]:
        return copy.deepcopy(self._data)


class FakeDocRef:
    def __init__(self, client: "FakeFirestore", path: str):
        self._client = client
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def get(self, transaction: Any = None) -> FakeSnapshot:
        self._client.reads += 1
        return FakeSnapshot(self.id, self._client.docs.get(self.path))

    def set(self, data: dict) -> None:
        self._client._write(self.path, data)

    def collection(self, name: str) -> "FakeCollection":
        return FakeCollection(self._client, f"{self.path}/{name}")


class FakeCollection:
    def __init__(self, client: "FakeFirestore", path: str, fields: Optional[List[str]] = None):
        self._client = client
        self.path = path
        self._fields = fields

    def document(self, doc_id: str) -> FakeDocRef:
        return FakeDocRef(self._client, f"{self.path}/{doc_id}")

    def select(self, fields: List[str]) -> "FakeCollection":
        return FakeCollection(self._client, self.path, list(fields))

    def stream(self):
        prefix = self.path + "/"
        for path, data in sorted(self._client.docs.items()):
            rest = path[len(prefix):] if path.startswith(prefix) else None
            if rest and "/" not in rest:
                self._client.reads += 1
                if self._fields is not None:
                    data = {k: v for k, v in data.items() if k in self._fields}
                yield FakeSnapshot(rest, data)


class FakeBatch:
    def __init__(self, client: "FakeFirestore"):
        self._client = client
        self._ops: list = []

    def set(self, ref: FakeDocRef, data: dict) -> None:
        self._ops.append((ref.path, copy.deepcopy(data)))

    def commit(self) -> None:
        if self._client.fail_next_commit:
            self._client.fail_next_commit = False
            raise RuntimeError("simulated Firestore outage")
        for path, data in self._ops:  # all-or-nothing
            self._client._write(path, data)
        self._client.commits += 1


class FakeTransaction:
    def __init__(self, client: "FakeFirestore"):
        self._client = client

    def set(self, ref: FakeDocRef, data: dict) -> None:
        self._client._write(ref.path, data)


class FakeFirestore:
    def __init__(self) -> None:
        self.docs: Dict[str, dict] = {}
        self.reads = 0
        self.writes = 0
        self.commits = 0
        self.fail_next_commit = False

    def _write(self, path: str, data: dict) -> None:
        self._check_value(data)
        self.docs[path] = copy.deepcopy(data)
        self.writes += 1

    def _check_value(self, value: Any, in_array: bool = False) -> None:
        # Firestore does not allow arrays directly inside arrays.
        if isinstance(value, dict):
            for v in value.values():
                self._check_value(v)
        elif isinstance(value, (list, tuple)):
            if in_array:
                raise ValueError("Firestore cannot store nested arrays")
            for v in value:
                self._check_value(v, in_array=True)

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self, name)

    def batch(self) -> FakeBatch:
        return FakeBatch(self)

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)
