from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.file_folders.models import FileFolders, SharedFileFolderSubscription
from app.files.canvas_assets import (
    CANVAS_ASSET_REFERENCES_META_KEY,
    CanvasAssetAccessError,
    build_canvas_asset_references,
    decide_canvas_asset_reference,
    is_canvas_artifact_dependency_snapshot_current,
    notify_canvas_asset_approval_requests,
    prepare_public_canvas_assets_payload,
    request_public_canvas_asset_access,
    resolve_canvas_asset_for_read,
)
from app.files.models import CanvasAssetGrant, Files


@pytest.fixture
def db():
    """Provide the real SQLite query boundary used by file ACL helpers."""

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            FileFolders.__table__,
            SharedFileFolderSubscription.__table__,
            Files.__table__,
            CanvasAssetGrant.__table__,
        ],
    )
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _folder(db, *, folder_id: str, owner_id: str) -> FileFolders:
    folder = FileFolders(
        id=folder_id,
        user_id=owner_id,
        name=folder_id,
        live_share_id=f"live-{folder_id}",
        collaborate_share_id=f"share-{folder_id}",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(folder)
    db.commit()
    return folder


def _subscribe(db, *, folder_id: str, user_id: str, share_type: str = "collaborate") -> None:
    db.add(
        SharedFileFolderSubscription(
            id=f"subscription-{folder_id}-{user_id}",
            folder_id=folder_id,
            subscriber_id=user_id,
            share_type=share_type,
            subscribed_at=datetime.now(timezone.utc),
        )
    )
    db.commit()


def _file(
    db,
    *,
    file_id: str,
    owner_id: str,
    folder_id: str | None = None,
    canvas_type: str | None = None,
) -> Files:
    meta = {"original_filename": f"{file_id}.txt"}
    if canvas_type:
        meta.update({"canvas": True, "canvas_type": canvas_type})
    record = Files(
        id=file_id,
        user_id=owner_id,
        file_name=f"{file_id}.txt",
        storage_provider="local",
        storage_key=f"{owner_id}/{file_id}.txt",
        file_category="text",
        file_type="text/plain" if not canvas_type else "text/x-tex",
        file_size=10,
        folder_id=folder_id,
        meta=meta,
        created_at=datetime.now(timezone.utc),
        last_updated_at=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    return record


def test_private_owner_asset_id_is_rejected_without_creating_request(db):
    """Knowing a private owner UUID is not enough to create an approval request."""

    _folder(db, folder_id="shared-folder", owner_id="owner")
    _subscribe(db, folder_id="shared-folder", user_id="collaborator")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="owner",
        folder_id="shared-folder",
        canvas_type="latex",
    )
    _file(db, file_id="owner-private", owner_id="owner")

    with pytest.raises(CanvasAssetAccessError):
        build_canvas_asset_references(
            db,
            canvas_record=canvas,
            actor_user_id="collaborator",
            asset_file_ids=["owner-private"],
        )


def test_canvas_asset_grant_supplies_consistent_timestamp_defaults(db):
    """Direct grant construction must not depend on every caller setting time."""

    canvas = _file(db, file_id="canvas-defaults", owner_id="owner")
    asset = _file(db, file_id="asset-defaults", owner_id="owner")
    grant = CanvasAssetGrant(
        canvas_file_id=canvas.id,
        asset_file_id=asset.id,
        asset_owner_user_id="owner",
        added_by_user_id="owner",
    )

    db.add(grant)
    db.flush()

    assert grant.created_at is not None
    assert grant.updated_at is not None


def test_pending_grant_notifies_the_real_asset_owner(monkeypatch):
    """The shared notifier targets the owner and carries translated UI details."""

    delivered = []

    class _EmptyNotificationQuery:
        def filter(self, *args):
            return self

        def limit(self, value):
            return self

        def all(self):
            return []

    fake_db = SimpleNamespace(
        query=lambda model: _EmptyNotificationQuery(),
        rollback=lambda: None,
    )
    monkeypatch.setattr(
        "app.users.models.get_user",
        lambda db, user_id: SimpleNamespace(
            first_name="Alice",
            last_name="Editor",
            email="alice@example.test",
        ),
    )
    monkeypatch.setattr(
        "app.userNotifications.models.create_user_notification",
        lambda db, **kwargs: delivered.append(kwargs),
    )
    canvas = SimpleNamespace(
        id="canvas-1",
        file_name="canvas-1.md",
        meta={"original_filename": "Plan.md"},
    )

    notify_canvas_asset_approval_requests(
        fake_db,
        actor_user_id="alice",
        canvas_record=canvas,
        references=[
            {
                "request_id": "request-1",
                "file_id": "asset-1",
                "asset_owner_user_id": "bob",
                "asset_name": "diagram.png",
            }
        ],
    )

    assert delivered[0]["user_ids"] == ["bob"]
    assert delivered[0]["details"] == {
        "type": "canvas_asset_approval",
        "scope": "canvas_members",
        "canvas_file_id": "canvas-1",
        "canvas_title": "Plan.md",
        "asset_file_id": "asset-1",
        "asset_name": "diagram.png",
        "request_id": "request-1",
        "requester_id": "alice",
        "requester_name": "Alice Editor",
    }


def test_asset_owner_can_grant_private_asset_to_all_canvas_members(db):
    """An owner attachment becomes a Canvas-scoped grant, not a global file grant."""

    _folder(db, folder_id="shared-folder", owner_id="canvas-owner")
    _subscribe(db, folder_id="shared-folder", user_id="asset-owner")
    _subscribe(db, folder_id="shared-folder", user_id="viewer")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="canvas-owner",
        folder_id="shared-folder",
        canvas_type="latex",
    )
    asset = _file(db, file_id="private-logo", owner_id="asset-owner")

    references, pending = build_canvas_asset_references(
        db,
        canvas_record=canvas,
        actor_user_id="asset-owner",
        asset_file_ids=[asset.id],
    )
    assert pending == []
    assert references[0]["status"] == "active"
    canvas.meta = {**canvas.meta, CANVAS_ASSET_REFERENCES_META_KEY: references}
    db.add(canvas)
    db.commit()

    resolved = resolve_canvas_asset_for_read(
        db,
        canvas_record=canvas,
        actor_user_id="viewer",
        asset_file_id=asset.id,
    )

    assert resolved.record.id == asset.id
    assert resolved.storage_owner_user_id == "asset-owner"


def test_forged_active_metadata_does_not_create_a_grant(db):
    """Imported metadata cannot manufacture access to another user's bytes."""

    _folder(db, folder_id="shared-folder", owner_id="canvas-owner")
    _subscribe(db, folder_id="shared-folder", user_id="viewer")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="canvas-owner",
        folder_id="shared-folder",
        canvas_type="html",
    )
    asset = _file(db, file_id="private-asset", owner_id="asset-owner")
    canvas.meta = {
        **canvas.meta,
        CANVAS_ASSET_REFERENCES_META_KEY: [
            {
                "request_id": "forged",
                "file_id": asset.id,
                "asset_owner_user_id": asset.user_id,
                "status": "active",
            }
        ],
    }
    db.commit()

    with pytest.raises(CanvasAssetAccessError):
        resolve_canvas_asset_for_read(
            db,
            canvas_record=canvas,
            actor_user_id="viewer",
            asset_file_id=asset.id,
        )


def test_readable_foreign_asset_outside_canvas_scope_requires_owner_approval(db):
    """Read access from another share cannot silently become a Canvas-wide grant."""

    _folder(db, folder_id="canvas-folder", owner_id="canvas-owner")
    _subscribe(db, folder_id="canvas-folder", user_id="requester")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="canvas-owner",
        folder_id="canvas-folder",
        canvas_type="latex",
    )

    _folder(db, folder_id="asset-folder", owner_id="asset-owner")
    _subscribe(db, folder_id="asset-folder", user_id="requester", share_type="live")
    asset = _file(
        db,
        file_id="foreign-asset",
        owner_id="asset-owner",
        folder_id="asset-folder",
    )

    references, pending = build_canvas_asset_references(
        db,
        canvas_record=canvas,
        actor_user_id="requester",
        asset_file_ids=[asset.id],
    )
    assert references[0]["status"] == "pending"
    assert pending == references

    canvas.meta = {**canvas.meta, CANVAS_ASSET_REFERENCES_META_KEY: references}
    db.add(canvas)
    db.commit()
    with pytest.raises(CanvasAssetAccessError):
        resolve_canvas_asset_for_read(
            db,
            canvas_record=canvas,
            actor_user_id="requester",
            asset_file_id=asset.id,
        )


def test_asset_owner_approval_activates_the_canvas_grant(db):
    """A pending foreign reference becomes usable after the real owner decides."""

    _folder(db, folder_id="canvas-folder", owner_id="canvas-owner")
    _subscribe(db, folder_id="canvas-folder", user_id="requester")
    _subscribe(db, folder_id="canvas-folder", user_id="viewer", share_type="live")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="canvas-owner",
        folder_id="canvas-folder",
        canvas_type="markdown",
    )
    _folder(db, folder_id="asset-folder", owner_id="asset-owner")
    _subscribe(db, folder_id="asset-folder", user_id="requester", share_type="live")
    asset = _file(
        db,
        file_id="foreign-asset",
        owner_id="asset-owner",
        folder_id="asset-folder",
    )
    references, pending = build_canvas_asset_references(
        db,
        canvas_record=canvas,
        actor_user_id="requester",
        asset_file_ids=[asset.id],
    )
    canvas.meta = {**canvas.meta, CANVAS_ASSET_REFERENCES_META_KEY: references}
    db.commit()

    decided_canvas, reference = decide_canvas_asset_reference(
        db,
        canvas_file_id=canvas.id,
        request_id=pending[0]["request_id"],
        asset_owner_user_id="asset-owner",
        approve=True,
    )
    resolved = resolve_canvas_asset_for_read(
        db,
        canvas_record=decided_canvas,
        actor_user_id="viewer",
        asset_file_id=asset.id,
    )

    assert reference["status"] == "active"
    assert resolved.record.id == asset.id

    public_pending = request_public_canvas_asset_access(
        db,
        canvas_record=decided_canvas,
        sharing_user_id="canvas-owner",
    )
    assert len(public_pending) == 1
    with pytest.raises(CanvasAssetAccessError):
        prepare_public_canvas_assets_payload(
            db,
            canvas_record=decided_canvas,
            include_content=False,
        )

    decide_canvas_asset_reference(
        db,
        canvas_file_id=canvas.id,
        request_id=public_pending[0]["public_request_id"],
        asset_owner_user_id="asset-owner",
        approve=True,
        public=True,
    )
    assert (
        prepare_public_canvas_assets_payload(
            db,
            canvas_record=decided_canvas,
            include_content=False,
        )
        == []
    )


def test_foreign_asset_in_same_collaboration_folder_is_already_authorized(db):
    """Putting an asset in the Canvas folder is an explicit member-scope share."""

    _folder(db, folder_id="shared-folder", owner_id="canvas-owner")
    _subscribe(db, folder_id="shared-folder", user_id="requester")
    canvas = _file(
        db,
        file_id="canvas",
        owner_id="canvas-owner",
        folder_id="shared-folder",
        canvas_type="latex",
    )
    asset = _file(
        db,
        file_id="shared-asset",
        owner_id="canvas-owner",
        folder_id="shared-folder",
    )

    references, pending = build_canvas_asset_references(
        db,
        canvas_record=canvas,
        actor_user_id="requester",
        asset_file_ids=[asset.id],
    )

    assert pending == []
    assert references[0]["status"] == "active"
    assert references[0]["authorized_by_user_id"] == "canvas-owner"


def test_generated_pdf_must_match_current_canvas_revision():
    """A public link cannot keep serving a PDF after its asset ACL changed."""

    source = SimpleNamespace(
        id="source",
        meta={"canvas_revision": 4, "latex_render_status": "ready"},
    )
    current_pdf = SimpleNamespace(
        id="pdf",
        meta={"latex_source_file_id": "source", "latex_source_revision": 4},
    )
    stale_pdf = SimpleNamespace(
        id="pdf",
        meta={"latex_source_file_id": "source", "latex_source_revision": 3},
    )

    assert is_canvas_artifact_dependency_snapshot_current(current_pdf, source) is True
    assert is_canvas_artifact_dependency_snapshot_current(stale_pdf, source) is False
