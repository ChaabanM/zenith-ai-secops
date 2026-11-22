from sqlalchemy import text

from app.core.database import SessionLocal, check_database, engine, get_db


def test_engine_uses_test_database():
    assert engine.url.get_backend_name() == "sqlite"
    assert engine.url.database.endswith("test.db")


def test_session_executes_query():
    with SessionLocal() as session:
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        assert check_database(session) is True


def test_session_can_write_and_read():
    with SessionLocal() as session:
        session.execute(text("CREATE TABLE IF NOT EXISTS _probe (id INTEGER PRIMARY KEY, v TEXT)"))
        session.execute(text("INSERT INTO _probe (v) VALUES ('hello')"))
        session.commit()
    with SessionLocal() as session:
        assert session.execute(text("SELECT v FROM _probe")).scalar_one() == "hello"
        session.execute(text("DROP TABLE _probe"))
        session.commit()


def test_get_db_dependency_closes_session():
    gen = get_db()
    session = next(gen)
    assert check_database(session)
    gen.close()
    assert not session.in_transaction()
