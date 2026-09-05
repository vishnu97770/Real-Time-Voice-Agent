from dataclasses import dataclass
from datetime import datetime, timezone

from app.underwriting import Application


@dataclass(frozen=True, slots=True)
class FinancialMetric:
    name: str
    value: float
    formula: str
    source_value_ids: tuple[str, ...]
    calculated_at: str


def calculate_financial_metrics(application: Application) -> tuple[tuple[FinancialMetric, ...], tuple[str, ...]]:
    latest: dict[str, object] = {}
    for item in application.extracted_values:
        latest[item.field_name] = item
    metrics: list[FinancialMetric] = []
    missing: set[str] = set()
    calculated_at = datetime.now(timezone.utc).isoformat()

    def ratio(name: str, numerator: str, denominator: str, formula: str) -> None:
        numerator_item = latest.get(numerator)
        denominator_item = latest.get(denominator)
        if numerator_item is None:
            missing.add(numerator)
        if denominator_item is None:
            missing.add(denominator)
        if numerator_item is None or denominator_item is None:
            return
        numerator_value = numerator_item.value
        denominator_value = denominator_item.value
        if not isinstance(numerator_value, (int, float)) or isinstance(numerator_value, bool):
            missing.add(numerator)
            return
        if not isinstance(denominator_value, (int, float)) or isinstance(denominator_value, bool) or denominator_value == 0:
            missing.add(denominator)
            return
        metrics.append(FinancialMetric(name, float(numerator_value) / float(denominator_value), formula, (numerator_item.value_id, denominator_item.value_id), calculated_at))

    ratio("profit_margin", "net_income", "revenue", "net_income / revenue")
    ratio("debt_to_revenue", "total_debt", "revenue", "total_debt / revenue")
    ratio("current_ratio", "current_assets", "current_liabilities", "current_assets / current_liabilities")
    return tuple(metrics), tuple(sorted(missing))
