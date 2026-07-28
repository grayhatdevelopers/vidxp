from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


class RepositoryLayout(BaseModel):
    model_config = ConfigDict(frozen=True)

    root: Path

    @property
    def descriptor(self) -> Path:
        return self.root / "repository.json"

    @property
    def media(self) -> Path:
        return self.root / "media"

    @property
    def catalog(self) -> Path:
        return self.root / "catalog.sqlite3"

    @property
    def media_objects(self) -> Path:
        return self.media / "objects"

    @property
    def indexes(self) -> Path:
        return self.root / "indexes"

    @property
    def local_index(self) -> Path:
        return self.indexes

    @property
    def generations(self) -> Path:
        return self.indexes / "generations"

    @property
    def index_store(self) -> Path:
        return self.indexes / "store"

    @property
    def snapshots(self) -> Path:
        return self.indexes / "snapshots"

    @property
    def active_snapshot(self) -> Path:
        return self.indexes / "active-snapshot.json"

    @property
    def index_lease(self) -> Path:
        return self.indexes / ".repository.lock"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def artifact_objects(self) -> Path:
        return self.artifacts / "objects"

    @property
    def local_workflows(self) -> Path:
        return self.root / "local-workflows"

    @property
    def workflow_database(self) -> Path:
        return self.local_workflows / "jobs.sqlite3"

    def ensure_local_directories(self) -> None:
        for path in (
            self.root,
            self.media,
            self.indexes,
            self.index_store,
            self.generations,
            self.snapshots,
            self.artifacts,
            self.local_workflows,
        ):
            path.mkdir(parents=True, exist_ok=True)
