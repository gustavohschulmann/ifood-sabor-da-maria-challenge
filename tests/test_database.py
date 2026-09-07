from src.infrastructure.database import Database


def test_schema_initializes_without_error() -> None:
    db = Database(":memory:")
    row = db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='workspace'")
    assert row is not None
    db.close()


def test_transaction_commits_on_success() -> None:
    db = Database(":memory:")
    with db.transaction() as cur:
        cur.execute(
            "INSERT INTO workspace (id, initial_budget, pantry_json) "
            "VALUES ('test', '80.00', '{}')"
        )
    row = db.fetchone("SELECT id FROM workspace WHERE id = 'test'")
    assert row is not None
    db.close()


def test_transaction_rolls_back_on_error() -> None:
    db = Database(":memory:")
    try:
        with db.transaction() as cur:
            cur.execute(
                "INSERT INTO workspace (id, initial_budget, pantry_json) "
                "VALUES ('test', '80.00', '{}')"
            )
            raise RuntimeError("simulated error")
    except RuntimeError:
        pass
    row = db.fetchone("SELECT id FROM workspace WHERE id = 'test'")
    assert row is None
    db.close()


def test_kitchen_capabilities_include_detail_column() -> None:
    db = Database(":memory:")
    columns = [row[1] for row in db.fetchall("PRAGMA table_info(kitchen_capabilities)")]
    assert "detail" in columns
    db.close()


def test_dumps_and_loads_roundtrip() -> None:
    from decimal import Decimal

    data = {"amount": Decimal("23.67"), "items": [1, 2]}
    s = Database.dumps(data)
    loaded = Database.loads(s)
    assert loaded["amount"] == "23.67"
    assert loaded["items"] == [1, 2]
