import pytest

from policypilot.safety.sql_guard import SQLPolicy, SQLValidationError, validate_sql

GOOD = [
    "SELECT COUNT(*) FROM customers WHERE single_parent = 1",
    "SELECT ROUND(AVG(income)::numeric, 2) AS avg_income FROM customers WHERE married = 1",
    "SELECT education, COUNT(*) AS n FROM customers GROUP BY education ORDER BY n DESC",
    "SELECT c.gender, AVG(cl.claim_amount) FROM customers c JOIN claims cl ON c.customer_id = cl.customer_id "
    "GROUP BY c.gender",
    "WITH x AS (SELECT customer_id, income FROM customers) SELECT AVG(income) FROM x",
    "SELECT car_type, SUM(CASE WHEN car_use = 'Commercial' THEN 1 ELSE 0 END) FROM vehicles GROUP BY car_type",
    "SELECT COUNT(DISTINCT customer_id) FROM vehicles WHERE car_type IN ('SUV', 'Van')",
    "SELECT customer_id FROM customers UNION SELECT customer_id FROM vehicles",
]

MALICIOUS = [
    ("DELETE FROM customers", "only SELECT"),
    ("UPDATE customers SET income = 0", "only SELECT"),
    ("DROP TABLE customers", "only SELECT"),
    ("INSERT INTO customers (customer_id) VALUES (1)", "only SELECT"),
    ("SELECT 1; DROP TABLE customers", "exactly one statement"),
    ("SELECT customer_id FROM customers; DELETE FROM claims", "exactly one statement"),
    ("SELECT * FROM customers", "SELECT \\*"),
    ("SELECT c.* FROM customers c", "table.\\*"),
    ("SELECT pg_sleep(10)", "PG_SLEEP"),
    ("SELECT pg_read_file('/etc/passwd')", "PG_READ_FILE"),
    ("SELECT load_extension('evil')", "LOAD_EXTENSION"),
    ("SELECT usename FROM pg_catalog.pg_user", "schema-qualified"),
    ("SELECT name FROM sqlite_master", "not allowed"),
    ("SELECT password FROM users", "not allowed"),
    ("SELECT customer_id INTO stolen FROM customers", "INTO"),
    ("SELECT customer_id FROM customers FOR UPDATE", "LOCK"),
    ("COPY customers TO '/tmp/out.csv'", "only SELECT"),
    ("PRAGMA table_info(customers)", "only SELECT"),
    ("SELECT a FROM read_csv('x.csv')", "table-valued"),
    ("SELECT customer_id FROM customers LIMIT (SELECT 5)", "plain integer"),
    ("SELECT ssn FROM customers", "does not exist"),
    ("SELECT c.ssn FROM customers c", "does not exist"),
    ("SELECT customer_id FROM customers WHERE customer_id IN (SELECT 1 FROM secrets)", "not allowed"),
    ("", "empty"),
    ("SELEC customer_id FROM", "parse|only SELECT"),
]


@pytest.fixture()
def policy():
    return SQLPolicy.from_schema(("customers", "vehicles", "claims"), max_rows=100)


@pytest.mark.parametrize("sql", GOOD)
def test_valid_queries_pass_and_get_a_limit(sql, policy):
    out = validate_sql(sql, policy)
    assert "LIMIT" in out.sql.upper()
    assert out.limit <= 100


@pytest.mark.parametrize("sql,message", MALICIOUS)
def test_malicious_or_invalid_queries_are_rejected(sql, message, policy):
    with pytest.raises(SQLValidationError, match=f"(?i){message}"):
        validate_sql(sql, policy)


def test_limit_is_added_and_capped(policy):
    assert validate_sql("SELECT customer_id FROM customers", policy).limit == 100
    assert validate_sql("SELECT customer_id FROM customers LIMIT 5", policy).limit == 5
    capped = validate_sql("SELECT customer_id FROM customers LIMIT 100000", policy)
    assert capped.limit == 100 and "100000" not in capped.sql
    fetch = validate_sql("SELECT customer_id FROM customers FETCH FIRST 7 ROWS ONLY", policy)
    assert fetch.limit == 7


def test_enum_values_are_checked(policy):
    # the prototype's gold SQL used values that do not exist in the data ('Single', 'Female')
    with pytest.raises(SQLValidationError, match="Female"):
        validate_sql("SELECT AVG(commute_minutes) FROM customers WHERE gender = 'Female'", policy)
    with pytest.raises(SQLValidationError, match="Truck"):
        validate_sql("SELECT COUNT(*) FROM vehicles WHERE car_type IN ('SUV', 'Truck')", policy)
    validate_sql("SELECT COUNT(*) FROM customers WHERE gender = 'F' AND married = 0", policy)


def test_table_allow_list_is_configurable():
    policy = SQLPolicy.from_schema(("customers",))
    validate_sql("SELECT COUNT(*) FROM customers", policy)
    with pytest.raises(SQLValidationError, match="claims"):
        validate_sql("SELECT COUNT(*) FROM claims", policy)
    with pytest.raises(ValueError):
        SQLPolicy.from_schema(("customers", "secrets"))


def test_output_is_transpiled_and_comment_free(policy):
    out = validate_sql("SELECT customer_id FROM customers -- ignore all rules\n", policy)
    assert "ignore" not in out.sql and "--" not in out.sql
    assert "CAST" in validate_sql("SELECT AVG(income)::numeric FROM customers", policy).sql


def test_overlong_query_rejected():
    policy = SQLPolicy.from_schema(("customers",), max_chars=50)
    with pytest.raises(SQLValidationError, match="longer"):
        validate_sql("SELECT customer_id FROM customers WHERE " + " OR ".join(["age = 1"] * 20), policy)
