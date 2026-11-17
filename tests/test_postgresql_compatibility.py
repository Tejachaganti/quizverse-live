"""Tests for PostgreSQL dialect compatibility across all 12 stages of SQLAlchemy models."""

from sqlalchemy import create_mock_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from quizverse.persistence.db import Database
from quizverse.persistence.models import Base


def test_all_models_compile_to_valid_postgresql_ddl():
    """Verify that all 59 declarative tables generate valid PostgreSQL DDL statements."""
    statements = []

    def executor(sql, *multiparams, **params):
        statements.append(str(sql.compile(dialect=postgresql.dialect())))

    mock_engine = create_mock_engine("postgresql+psycopg://", executor=executor)
    Base.metadata.create_all(mock_engine)

    assert len(Base.metadata.tables) >= 50
    table_names = list(Base.metadata.tables.keys())

    # Critical tables from each stage must exist
    assert "quizzes" in table_names
    assert "player_profiles" in table_names
    assert "competitive_matches" in table_names
    assert "tournaments" in table_names
    assert "league_seasons" in table_names
    assert "friendships" in table_names
    assert "room_invitations" in table_names
    assert "direct_messages" in table_names
    assert "player_notifications" in table_names
    assert "player_learning_paths" in table_names
    assert "creator_profiles" in table_names
    assert "quiz_publications" in table_names

    for name, table in Base.metadata.tables.items():
        ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
        assert "CREATE TABLE" in ddl
        assert name in ddl


def test_database_session_rollback_on_error():
    """Verify that the database session context manager safely rolls back transactions on error."""
    db = Database("sqlite:///:memory:")
    with db.engine.connect() as conn:
        conn.execute(text("CREATE TABLE test_items (id INT PRIMARY KEY, val TEXT);"))
        conn.commit()

    # Successful insert
    with db.session() as s:
        s.execute(text("INSERT INTO test_items VALUES (1, 'initial');"))
        s.commit()

    # Failed insert inside session context
    try:
        with db.session() as s:
            s.execute(text("INSERT INTO test_items VALUES (2, 'second');"))
            raise ValueError("Deliberate failure inside transaction")
    except ValueError:
        pass

    # Verify that id 2 was rolled back and does not exist
    with db.session() as s:
        rows = s.execute(text("SELECT id, val FROM test_items;")).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == "initial"
