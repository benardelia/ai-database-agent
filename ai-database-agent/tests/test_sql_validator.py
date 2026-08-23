import pytest

from dbagent.services.sql_validator import SqlValidationError, SqlValidator


@pytest.fixture(scope="module")
def validator() -> SqlValidator:
    return SqlValidator()


def test_valid_select_passes(validator: SqlValidator):
    result = validator.validate("SELECT srid, auth_name FROM spatial_ref_sys")
    assert "SELECT" in result.upper()


def test_valid_select_with_cte_passes(validator: SqlValidator):
    sql = "WITH x AS (SELECT srid FROM spatial_ref_sys) SELECT * FROM x"
    result = validator.validate(sql)
    assert "SELECT" in result.upper()


def test_multiple_statements_rejected(validator: SqlValidator):
    with pytest.raises(SqlValidationError, match="one SQL statement"):
        validator.validate("SELECT * FROM spatial_ref_sys; DELETE FROM spatial_ref_sys;")


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM spatial_ref_sys",
        "UPDATE spatial_ref_sys SET auth_name = 'x'",
        "INSERT INTO spatial_ref_sys (srid) VALUES (1)",
        "DROP TABLE spatial_ref_sys",
        "ALTER TABLE spatial_ref_sys ADD COLUMN x int",
        "TRUNCATE TABLE spatial_ref_sys",
        "CREATE TABLE evil (id int)",
        "GRANT SELECT ON spatial_ref_sys TO public",
    ],
)
def test_destructive_statements_rejected(validator: SqlValidator, sql: str):
    with pytest.raises(SqlValidationError):
        validator.validate(sql)


def test_data_modifying_cte_rejected(validator: SqlValidator):
    """A SELECT that looks safe at the top level but hides a DELETE inside
    a CTE must still be rejected -- this is the real attack the guide
    warns about (Phase 47: SQL injection / do-not-trust-the-LLM)."""
    sql = "WITH x AS (DELETE FROM spatial_ref_sys RETURNING *) SELECT * FROM x"
    with pytest.raises(SqlValidationError, match="Disallowed SQL construct"):
        validator.validate(sql)


def test_empty_sql_rejected(validator: SqlValidator):
    with pytest.raises(SqlValidationError):
        validator.validate("   ")


def test_unparseable_sql_rejected(validator: SqlValidator):
    with pytest.raises(SqlValidationError):
        validator.validate("SELEKT * FROM;;; garbage(")


def test_excluded_table_rejected_even_for_valid_select():
    validator = SqlValidator(excluded_tables={"my_case_db.user_credentials"})
    with pytest.raises(SqlValidationError, match="not permitted"):
        validator.validate("SELECT * FROM my_case_db.user_credentials")


def test_non_excluded_table_still_allowed():
    validator = SqlValidator(excluded_tables={"my_case_db.user_credentials"})
    result = validator.validate("SELECT * FROM spatial_ref_sys")
    assert "SELECT" in result.upper()
