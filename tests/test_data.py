from policypilot.data.cleaning import (clean_rows, normalize_gender, parse_birth, parse_money, parse_yes_no,
                                       strip_prefix)
from policypilot.data.seed import build_documents, read_sqlite
from policypilot.data.synthetic import generate
from policypilot.schema import ddl


def _raw(**overrides):
    row = {"ID": "63581743", "KIDSDRIV": "0", "BIRTH": "16MAR39", "AGE": "60", "HOMEKIDS": "0", "YOJ": "11",
           "INCOME": "$67,349", "PARENT1": "No", "HOME_VAL": "$0", "MSTATUS": "z_No", "GENDER": "M",
           "EDUCATION": "PhD", "OCCUPATION": "Professional", "TRAVTIME": "14", "CAR_USE": "Private",
           "BLUEBOOK": "$14,230", "TIF": "11", "CAR_TYPE": "Minivan", "RED_CAR": "yes", "OLDCLAIM": "$4,461",
           "CLM_FREQ": "2", "REVOKED": "No", "MVR_PTS": "3", "CLM_AMT": "$0", "CAR_AGE": "18", "CLAIM_FLAG": "0",
           "URBANICITY": "Highly Urban/ Urban"}
    row.update(overrides)
    return row


def test_gender_repair_including_the_corrupted_no_label():
    assert normalize_gender("z_F") == "F"
    assert normalize_gender("M") == "M"
    assert normalize_gender("No") == "F"      # what female rows became in the earlier broken load
    assert normalize_gender("?") is None


def test_money_prefixes_and_flags():
    assert parse_money("$14,230") == 14230 and parse_money("") is None
    assert strip_prefix("z_SUV") == "SUV"
    assert parse_yes_no("z_No") == 0 and parse_yes_no("Yes") == 1 and parse_yes_no("maybe") is None
    assert parse_birth("16MAR39", age=60) == "1939-03-16"
    assert parse_birth("garbage") is None


def test_clean_rows_types_keys_and_deduplication():
    rows = [_raw(), _raw(GENDER="z_F"), _raw(ID="5", GENDER="z_F", CAR_TYPE="z_SUV", EDUCATION="<High School",
                                              URBANICITY="z_Highly Rural/ Rural", OCCUPATION="")]
    data, report = clean_rows(rows)
    assert report.duplicates_dropped == 1 and len(data.customers) == 2
    first, second = data.customers
    assert first["customer_id"] == 63581743 and first["income"] == 67349 and first["married"] == 0
    assert second["gender"] == "F" and second["education"] == "Less Than High School"
    assert second["occupation"] == "Unknown"
    assert data.vehicles[1]["car_type"] == "SUV" and data.vehicles[1]["urbanicity"] == "Rural"
    # the source ID is the shared key for customers, vehicles and claims
    assert {v["customer_id"] for v in data.vehicles} == {c["customer_id"] for c in data.customers}
    assert data.claims[0]["past_claims_total"] == 4461


def test_rows_without_id_are_rejected():
    _, report = clean_rows([_raw(ID="")])
    assert report.rows_rejected == 1


def test_synthetic_data_is_deterministic_and_consistent():
    a, b = generate(50, seed=1), generate(50, seed=1)
    assert a.customers == b.customers and a.vehicles == b.vehicles
    assert generate(50, seed=2).customers != a.customers
    customer_ids = {c["customer_id"] for c in a.customers}
    assert all(v["customer_id"] in customer_ids for v in a.vehicles)
    assert {c["vehicle_id"] for c in a.claims} == {v["vehicle_id"] for v in a.vehicles}
    assert {c["gender"] for c in a.customers} <= {"F", "M"}


def test_documents_are_typed_and_built_from_the_same_rows(dataset, db_path):
    docs = build_documents(dataset)
    assert len(docs) == len(dataset.vehicles)
    doc = docs[0]
    assert isinstance(doc["bluebook_value"], int) and isinstance(doc["claims"]["amount"], int)
    assert isinstance(doc["car"]["red"], bool)
    assert build_documents(read_sqlite(db_path)) == docs      # SQL and documents agree


def test_ddl_has_foreign_keys_and_enum_checks():
    text = ddl()
    assert "REFERENCES customers(customer_id)" in text
    assert "CHECK (gender IN ('F', 'M'))" in text
