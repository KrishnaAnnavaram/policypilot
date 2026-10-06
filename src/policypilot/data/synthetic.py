"""Deterministic synthetic insurance data for the offline demo and the tests.

The generator produces typed rows for the canonical model (customers, vehicles,
claims, all keyed by ``customer_id``). Values are drawn from a seeded RNG, so the
same seed always yields the same database; nothing here comes from a real dataset.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date

from ..schema import CAR_TYPE, EDUCATION, OCCUPATION

REFERENCE_YEAR = 2025


@dataclass
class Dataset:
    customers: list[dict] = field(default_factory=list)
    vehicles: list[dict] = field(default_factory=list)
    claims: list[dict] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {"customers": len(self.customers), "vehicles": len(self.vehicles), "claims": len(self.claims)}


def _weighted(rng: random.Random, options: tuple[str, ...], weights: tuple[float, ...]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def generate(n_customers: int = 300, seed: int = 7, second_car_rate: float = 0.2) -> Dataset:
    if n_customers < 1:
        raise ValueError("n_customers must be >= 1")
    rng = random.Random(seed)
    data = Dataset()
    vehicle_id = 0
    for customer_id in range(1, n_customers + 1):
        age = rng.randint(18, 80)
        birth = date(REFERENCE_YEAR - age, rng.randint(1, 12), rng.randint(1, 28))
        education = _weighted(rng, EDUCATION, (0.3, 0.28, 0.2, 0.08, 0.14))
        occupation = _weighted(rng, OCCUPATION, (0.22, 0.15, 0.03, 0.08, 0.1, 0.12, 0.14, 0.08, 0.08))
        base_income = {"Doctor": 120_000, "Lawyer": 105_000, "Manager": 85_000, "Professional": 75_000,
                       "Clerical": 34_000, "Blue Collar": 52_000, "Home Maker": 6_000, "Student": 5_000,
                       "Unknown": 45_000}[occupation]
        income = max(0, int(rng.gauss(base_income, base_income * 0.25)) // 10 * 10)
        married = rng.random() < 0.6
        kids_home = rng.choice((0, 0, 0, 1, 1, 2, 3))
        single_parent = (not married) and kids_home > 0 and rng.random() < 0.7
        owns_home = rng.random() < 0.7
        data.customers.append({
            "customer_id": customer_id,
            "birth_date": birth.isoformat(),
            "age": age,
            "gender": rng.choice(("F", "M")),
            "married": int(married),
            "single_parent": int(single_parent),
            "kids_driving": min(kids_home, rng.choice((0, 0, 0, 1, 2))) if age > 35 else 0,
            "kids_at_home": kids_home,
            "years_on_job": max(0, min(age - 18, int(rng.gauss(10, 4)))),
            "income": income,
            "home_value": int(rng.gauss(160_000, 60_000)) // 100 * 100 if owns_home else 0,
            "education": education,
            "occupation": occupation,
            "commute_minutes": max(5, int(rng.gauss(33, 15))),
        })
        n_cars = 2 if rng.random() < second_car_rate else 1
        for _ in range(n_cars):
            vehicle_id += 1
            car_type = _weighted(rng, CAR_TYPE, (0.26, 0.08, 0.17, 0.11, 0.28, 0.10))
            commercial = rng.random() < (0.6 if occupation == "Blue Collar" else 0.3)
            urban = rng.random() < 0.8
            mvr = rng.choice((0, 0, 0, 1, 1, 2, 3, 4, 5, 7))
            past_claims = rng.choice((0, 0, 0, 0, 1, 2, 3))
            crash_p = 0.12 + 0.03 * mvr + (0.12 if urban else -0.08) + (0.08 if commercial else 0.0)
            crashed = rng.random() < max(0.02, min(0.9, crash_p))
            data.vehicles.append({
                "vehicle_id": vehicle_id,
                "customer_id": customer_id,
                "car_use": "Commercial" if commercial else "Private",
                "car_type": car_type,
                "red_car": int(rng.random() < 0.3),
                "car_age": max(0, int(rng.gauss(8, 4))),
                "bluebook_value": max(1_500, int(rng.gauss(16_000, 7_000)) // 10 * 10),
                "years_insured": rng.randint(1, 20),
                "urbanicity": "Urban" if urban else "Rural",
            })
            data.claims.append({
                "claim_id": vehicle_id,
                "vehicle_id": vehicle_id,
                "customer_id": customer_id,
                "claims_last_5y": past_claims,
                "past_claims_total": sum(rng.randint(500, 9_000) for _ in range(past_claims)),
                "license_revoked": int(rng.random() < 0.1),
                "mvr_points": mvr,
                "claim_amount": rng.randint(1_000, 30_000) if crashed else 0,
                "claim_flag": int(crashed),
            })
    return data
