"""Clean the public "Car Insurance Claim" CSV layout into the canonical typed model.

The raw file stores money as text (``"$14,230"``), prefixes some categories with
``z_`` (``z_F``, ``z_No``, ``z_SUV``), repeats some IDs and leaves fields blank. An
earlier load stripped the prefixes badly and turned every female customer into
``"No"``; :func:`normalize_gender` repairs that too. The source ``ID`` is kept as the
shared ``customer_id`` and duplicated IDs are dropped (first row wins) and counted.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..schema import CAR_TYPE, EDUCATION, OCCUPATION
from .synthetic import Dataset

_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}


def strip_prefix(value: str | None) -> str:
    """Remove the source's ``z_`` marker and surrounding whitespace."""
    text = (value or "").strip()
    return text[2:].strip() if text.lower().startswith("z_") else text


def parse_money(value: str | None) -> int | None:
    """``"$14,230"`` -> 14230; blanks -> None. Never returns a string."""
    text = (value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return None
    return int(round(float(text)))


def parse_int(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    return int(round(float(text)))


def parse_yes_no(value: str | None) -> int | None:
    text = strip_prefix(value).lower()
    if text in {"yes", "y", "true", "1"}:
        return 1
    if text in {"no", "n", "false", "0"}:
        return 0
    return None


def normalize_gender(value: str | None) -> str | None:
    """Map raw and previously-corrupted gender labels to ``F`` / ``M``.

    ``"No"`` is accepted as female on purpose: it is what ``z_F`` turned into in the
    earlier, broken load, where it was the only non-``M`` value in the column.
    """
    text = strip_prefix(value).lower()
    if text in {"f", "female", "no"}:
        return "F"
    if text in {"m", "male"}:
        return "M"
    return None


def normalize_education(value: str | None) -> str | None:
    text = strip_prefix(value)
    if text.startswith("<"):
        return "Less Than High School"
    return text if text in EDUCATION else None


def normalize_occupation(value: str | None) -> str:
    text = strip_prefix(value)
    return text if text in OCCUPATION else "Unknown"


def normalize_car_type(value: str | None) -> str | None:
    text = strip_prefix(value)
    return text if text in CAR_TYPE else None


def normalize_urbanicity(value: str | None) -> str | None:
    text = strip_prefix(value).lower()
    if "rural" in text:
        return "Rural"
    if "urban" in text:
        return "Urban"
    return None


def parse_birth(value: str | None, age: int | None = None, reference_year: int = 1999) -> str | None:
    """``"16MAR39"`` -> ``"1939-03-16"``; the century is chosen to agree with ``age`` when known."""
    text = (value or "").strip().upper()
    m = re.fullmatch(r"(\d{1,2})([A-Z]{3})(\d{2})", text)
    if not m or m.group(2) not in _MONTHS:
        return None
    day, month, yy = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    year = 1900 + yy
    if age is not None and abs((reference_year - (2000 + yy)) - age) < abs((reference_year - year) - age):
        year = 2000 + yy
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


@dataclass
class CleaningReport:
    rows_read: int = 0
    duplicates_dropped: int = 0
    rows_rejected: int = 0
    problems: list[str] = field(default_factory=list)


def clean_rows(rows: list[dict[str, str]]) -> tuple[Dataset, CleaningReport]:
    report = CleaningReport()
    data = Dataset()
    seen: set[int] = set()
    for raw in rows:
        report.rows_read += 1
        row = {k.strip().upper(): v for k, v in raw.items() if k}
        try:
            cid = int(str(row.get("ID", "")).strip())
        except ValueError:
            report.rows_rejected += 1
            report.problems.append(f"row {report.rows_read}: missing or invalid ID")
            continue
        if cid in seen:
            report.duplicates_dropped += 1
            continue
        seen.add(cid)
        age = parse_int(row.get("AGE"))
        data.customers.append({
            "customer_id": cid,
            "birth_date": parse_birth(row.get("BIRTH"), age),
            "age": age,
            "gender": normalize_gender(row.get("GENDER")),
            "married": parse_yes_no(row.get("MSTATUS")),
            "single_parent": parse_yes_no(row.get("PARENT1")),
            "kids_driving": parse_int(row.get("KIDSDRIV")),
            "kids_at_home": parse_int(row.get("HOMEKIDS")),
            "years_on_job": parse_int(row.get("YOJ")),
            "income": parse_money(row.get("INCOME")),
            "home_value": parse_money(row.get("HOME_VAL")),
            "education": normalize_education(row.get("EDUCATION")),
            "occupation": normalize_occupation(row.get("OCCUPATION")),
            "commute_minutes": parse_int(row.get("TRAVTIME")),
        })
        data.vehicles.append({
            "vehicle_id": cid,
            "customer_id": cid,
            "car_use": strip_prefix(row.get("CAR_USE")) or None,
            "car_type": normalize_car_type(row.get("CAR_TYPE")),
            "red_car": parse_yes_no(row.get("RED_CAR")),
            "car_age": parse_int(row.get("CAR_AGE")),
            "bluebook_value": parse_money(row.get("BLUEBOOK")),
            "years_insured": parse_int(row.get("TIF")),
            "urbanicity": normalize_urbanicity(row.get("URBANICITY")),
        })
        data.claims.append({
            "claim_id": cid,
            "vehicle_id": cid,
            "customer_id": cid,
            "claims_last_5y": parse_int(row.get("CLM_FREQ")),
            "past_claims_total": parse_money(row.get("OLDCLAIM")),
            "license_revoked": parse_yes_no(row.get("REVOKED")),
            "mvr_points": parse_int(row.get("MVR_PTS")),
            "claim_amount": parse_money(row.get("CLM_AMT")),
            "claim_flag": parse_int(row.get("CLAIM_FLAG")),
        })
    return data, report


def load_csv(path: str | Path) -> tuple[Dataset, CleaningReport]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return clean_rows(list(csv.DictReader(fh)))
