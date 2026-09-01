import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

if "zstandard" not in sys.modules:
    fake_zstandard = ModuleType("zstandard")
    fake_zstandard.ZstdCompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_writer=lambda handle: handle,
        compress=lambda payload: payload,
    )
    fake_zstandard.ZstdDecompressor = lambda *args, **kwargs: SimpleNamespace(
        stream_reader=lambda handle: handle,
        decompress=lambda payload: payload,
    )
    sys.modules["zstandard"] = fake_zstandard


from app.admin.users import utils as bulk_user
from app.users import admin_management as admin_user_management
from app.users import utils as user_utils


def test_admin_create_user_rejects_passwords_that_fail_policy(monkeypatch):
    seen_passwords: list[str] = []

    def fake_assert_password_policy(password, db):
        seen_passwords.append(password)
        raise HTTPException(status_code=400, detail="weak password")

    monkeypatch.setattr(
        admin_user_management, "user_exists_by_email", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        admin_user_management, "_assert_password_policy", fake_assert_password_policy
    )
    monkeypatch.setattr(
        admin_user_management,
        "hash_password",
        lambda *_args, **_kwargs: pytest.fail(
            "hash_password should not run when policy validation fails"
        ),
    )
    monkeypatch.setattr(
        admin_user_management,
        "create_user",
        lambda *_args, **_kwargs: pytest.fail(
            "create_user should not run when policy validation fails"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        admin_user_management.create_user_via_admin(
            SimpleNamespace(
                email="new.user@example.com",
                password="weakpass",
                first_name="New",
                last_name="User",
                group_id=None,
            ),
            object(),
            object(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "weak password"
    assert seen_passwords == ["weakpass"]


def test_admin_create_user_persists_force_password_change(monkeypatch):
    created_user = SimpleNamespace(
        id="user-1",
        email="new.user@example.com",
        first_name="New",
        last_name="User",
        role="user",
    )
    db = object()
    settings_updates: list[tuple] = []

    monkeypatch.setattr(
        admin_user_management, "user_exists_by_email", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        admin_user_management,
        "_assert_password_policy",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        admin_user_management,
        "hash_password",
        lambda password: f"hashed:{password}",
    )
    monkeypatch.setattr(
        admin_user_management,
        "get_value_by_page_and_key",
        lambda page, key, _db: "group-1" if key == "default_user_group" else "user",
    )
    monkeypatch.setattr(
        admin_user_management, "create_user", lambda *_args, **_kwargs: created_user
    )
    monkeypatch.setattr(
        admin_user_management,
        "create_authentication_log",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        admin_user_management,
        "update_user_settings",
        lambda *args, **_kwargs: settings_updates.append(args),
    )

    result = admin_user_management.create_user_via_admin(
        SimpleNamespace(
            email="new.user@example.com",
            password="StrongerPass123!",
            first_name="New",
            last_name="User",
            group_id=None,
            has_to_change_password=True,
        ),
        db,
        object(),
    )

    assert result["status"] == "success"
    assert settings_updates == [
        ("user-1", "security", "has_to_change_password", True, db)
    ]


@pytest.mark.parametrize(
    ("importer", "parser_name"),
    [
        (bulk_user.create_users_from_xlsx, "iter_xlsx_rows"),
        (bulk_user.create_users_from_csv, "iter_csv_rows"),
    ],
)
def test_bulk_user_import_requires_admin_default_password(
    monkeypatch, importer, parser_name
):
    """Direct importer calls must use the same password input as the admin UI."""
    monkeypatch.setattr(bulk_user, parser_name, lambda _contents: [])

    with pytest.raises(HTTPException) as exc_info:
        importer(b"ignored", object())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Default password is required"


@pytest.mark.parametrize(
    ("importer", "parser_name"),
    [
        (
            bulk_user.create_users_from_xlsx,
            "iter_xlsx_rows",
        ),
        (
            bulk_user.create_users_from_csv,
            "iter_csv_rows",
        ),
    ],
)
def test_bulk_user_import_rejects_default_passwords_that_fail_policy(
    monkeypatch, importer, parser_name
):
    seen_passwords: list[str] = []

    def fake_assert_password_policy(password, db):
        seen_passwords.append(password)
        raise HTTPException(status_code=400, detail="weak default password")

    monkeypatch.setattr(bulk_user, parser_name, lambda _contents: [])
    monkeypatch.setattr(
        bulk_user, "_assert_password_policy", fake_assert_password_policy
    )

    with pytest.raises(HTTPException) as exc_info:
        importer(b"ignored", object(), default_password="weakpass")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "weak default password"
    assert seen_passwords == ["weakpass"]


@pytest.mark.parametrize(
    ("importer", "parser_name"),
    [
        (
            bulk_user.create_users_from_xlsx,
            "iter_xlsx_rows",
        ),
        (
            bulk_user.create_users_from_csv,
            "iter_csv_rows",
        ),
    ],
)
def test_bulk_user_import_requires_default_group(monkeypatch, importer, parser_name):
    """CSV and XLSX imports must apply the same group configuration check."""
    monkeypatch.setattr(bulk_user, parser_name, lambda _contents: [])
    monkeypatch.setattr(
        bulk_user,
        "get_value_by_page_and_key",
        lambda _page, _key, _db: None,
    )
    monkeypatch.setattr(bulk_user, "_assert_password_policy", lambda *_args: None)
    monkeypatch.setattr(
        bulk_user,
        "get_password_policy_requirements",
        lambda _db: {},
    )

    with pytest.raises(HTTPException) as exc_info:
        importer(b"ignored", object(), default_password="TemporaryPass123!")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "Default user group is not configured. Please set it in settings."
    )


@pytest.mark.parametrize(
    ("importer", "parser_name"),
    [
        (
            bulk_user.create_users_from_xlsx,
            "iter_xlsx_rows",
        ),
        (
            bulk_user.create_users_from_csv,
            "iter_csv_rows",
        ),
    ],
)
def test_bulk_user_import_generates_unique_passwords_from_default(
    monkeypatch, importer, parser_name
):
    rows = [
        (
            2,
            {
                "email": "one@example.com",
                "first_name": "One",
                "last_name": "User",
                "password": "row-password-must-be-ignored",
                "has_to_change_password": True,
            },
        ),
        (3, {"email": "two@example.com", "first_name": "Two", "last_name": "User"}),
    ]
    hashed_passwords: list[str] = []
    settings_updates: list[tuple] = []
    commits: list[None] = []
    password_requirements = {
        "min_len": 64,
        "min_special": 4,
        "min_upper": 3,
        "min_lower": 3,
        "min_num": 3,
        "special_characters": user_utils.PASSWORD_POLICY_SPECIAL_CHARACTERS,
    }

    monkeypatch.setattr(bulk_user, parser_name, lambda _contents: rows)
    monkeypatch.setattr(
        bulk_user, "_assert_password_policy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        bulk_user, "get_password_policy_requirements", lambda _db: password_requirements
    )
    monkeypatch.setattr(
        bulk_user,
        "get_value_by_page_and_key",
        lambda _page, key, _db: "user" if key == "default_user_role" else "group-1",
    )
    monkeypatch.setattr(
        bulk_user, "user_exists_by_email", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        bulk_user,
        "hash_password",
        lambda password: hashed_passwords.append(password) or f"hashed:{password}",
    )

    def fake_create_user(
        _db,
        email,
        hashed_password,
        first_name,
        last_name,
        role,
        group_id,
        *,
        commit=True,
    ):
        assert commit is False
        return SimpleNamespace(
            id=f"user-{email}",
            email=email,
            first_name=first_name,
            last_name=last_name,
            role=role,
            is_active=True,
            hashed_password=hashed_password,
            group_id=group_id,
        )

    monkeypatch.setattr(bulk_user, "create_user", fake_create_user)
    monkeypatch.setattr(
        bulk_user,
        "update_user_settings_bulk",
        lambda user_id, settings, db, *, commit=True: settings_updates.append(
            (user_id, settings, db, commit)
        ),
    )

    db = SimpleNamespace(
        commit=lambda: commits.append(None),
        refresh=lambda _user: pytest.fail(
            "bulk import must not refresh an account after committing it"
        ),
        rollback=lambda: None,
    )
    result = importer(
        b"ignored",
        db,
        default_password="TemporaryPass123!",
        force_password_change=False,
    )

    temporary_passwords = [
        user["temporary_password"] for user in result["created_users"]
    ]
    assert result["total_created"] == 2
    assert len(set(temporary_passwords)) == 2
    assert temporary_passwords == hashed_passwords
    assert all("TemporaryPass123!" not in password for password in temporary_passwords)
    for password in temporary_passwords:
        counts = user_utils.count_password_character_classes(password)
        assert counts["len"] >= password_requirements["min_len"]
        assert counts["special"] >= password_requirements["min_special"]
        assert counts["upper"] >= password_requirements["min_upper"]
        assert counts["lower"] >= password_requirements["min_lower"]
        assert counts["num"] >= password_requirements["min_num"]
    assert settings_updates == [
        (
            "user-one@example.com",
            {"security": {"has_to_change_password": False}},
            db,
            False,
        ),
        (
            "user-two@example.com",
            {"security": {"has_to_change_password": False}},
            db,
            False,
        ),
    ]
    assert len(commits) == 2


def test_bulk_user_row_rolls_back_and_hides_internal_errors(monkeypatch):
    """A settings failure must not commit the account or expose its exception."""
    rows = [(2, {"email": "new@example.com", "first_name": "New", "last_name": "User"})]
    transaction_calls: list[str] = []
    db = SimpleNamespace(
        commit=lambda: transaction_calls.append("commit"),
        refresh=lambda _user: transaction_calls.append("refresh"),
        rollback=lambda: transaction_calls.append("rollback"),
    )

    monkeypatch.setattr(bulk_user, "iter_csv_rows", lambda _contents: rows)
    monkeypatch.setattr(
        bulk_user, "_assert_password_policy", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        bulk_user,
        "get_password_policy_requirements",
        lambda _db: {
            "min_len": 10,
            "min_special": 1,
            "min_upper": 1,
            "min_lower": 1,
            "min_num": 1,
            "special_characters": user_utils.PASSWORD_POLICY_SPECIAL_CHARACTERS,
        },
    )
    monkeypatch.setattr(
        bulk_user,
        "get_value_by_page_and_key",
        lambda _page, _key, _db: "group-1",
    )
    monkeypatch.setattr(
        bulk_user, "user_exists_by_email", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        bulk_user, "hash_password", lambda password: f"hashed:{password}"
    )

    def fake_create_user(*_args, commit=True, **_kwargs):
        assert commit is False
        return SimpleNamespace(
            id="user-1",
            email="new@example.com",
            first_name="New",
            last_name="User",
            role="user",
        )

    monkeypatch.setattr(bulk_user, "create_user", fake_create_user)
    monkeypatch.setattr(
        bulk_user,
        "update_user_settings_bulk",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("database password=do-not-expose")
        ),
    )

    result = bulk_user.create_users_from_csv(
        b"ignored",
        db,
        default_password="TemporaryPass123!",
        force_password_change=True,
    )

    assert transaction_calls == ["rollback"]
    assert result["created_users"] == []
    assert result["errors"] == ["Row 2: Unable to create user"]
    assert "do-not-expose" not in str(result)
