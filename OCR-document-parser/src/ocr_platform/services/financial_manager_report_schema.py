"""Structured RouterAI contract for a financial manager's report."""

from datetime import date
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

Money = Annotated[str, StringConstraints(pattern=r"^-?[0-9]+\.[0-9]{2}$")]
T = TypeVar("T")


def valid_date(value: str) -> str:
    date.fromisoformat(value)
    return value


ReportDate = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
                            AfterValidator(valid_date)]


class ReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(ReportModel):
    page: int = Field(ge=1, description="Physical PDF page, numbered from one")
    table: str | None
    row: str | None
    column: str | None
    quote: str | None


class Fact(ReportModel, Generic[T]):
    value: T | None
    source: Source | None
    reason: Literal["not_stated", "unreadable", "ambiguous", "not_applicable"] | None


class PropertyRow(ReportModel):
    name: Fact[str]
    manager_valuation: Fact[Money]
    appraiser_valuation: Fact[Money]
    sole_home: Fact[bool]
    secured_by_pledge: Fact[bool]
    pledgee: Fact[str]
    pledge_details: Fact[str]
    exclusion_text: Fact[str]
    exclusion_basis: Fact[str]


class SaleRow(ReportModel):
    name: Fact[str]
    sale_method: Fact[str]
    sale_result: Fact[str]
    contract_price: Fact[Money]
    paid_amount: Fact[Money]
    unsold_return_text: Fact[str]


RowKind = Literal["detail", "subtotal", "total"]


class PaymentRow(ReportModel):
    row_kind: RowKind
    source: Source
    queue: Fact[str]


class RegisterRow(PaymentRow):
    queue_part: Fact[str]
    creditor_name: Fact[str]
    claim_type: Fact[str]
    claim_category: Fact[Literal[
        "secured", "principal", "sanctions", "salary_or_severance", "procedure_interest", "other"
    ]]
    secured_by_pledge: Fact[bool]
    outside_register: Fact[bool]
    included_amount: Fact[Money]
    paid_amount: Fact[Money]


class CurrentPaymentRow(PaymentRow):
    name: Fact[str]
    expense_category: Fact[Literal["manager_remuneration", "engaged_services", "other"]]
    obligation_amount: Fact[Money]
    paid_amount: Fact[Money]
    unpaid_balance: Fact[Money]


class Section(ReportModel, Generic[T]):
    status: Literal["listed", "none_declared", "not_found", "unreadable", "partial"]
    source: Source | None
    rows: list[T]


class FinancialManagerReport(ReportModel):
    case_number: Fact[str]
    debtor_full_name: Fact[str]
    debtor_inn: Fact[Annotated[str, StringConstraints(pattern=r"^[0-9]{12}$")]]
    procedure_type: Fact[Literal["РИ", "РД"]]
    report_date: Fact[ReportDate]
    inventory_date: Fact[ReportDate]
    property_inventory: Section[PropertyRow]
    property_sales: Section[SaleRow]
    creditor_register: Section[RegisterRow]
    current_payments: Section[CurrentPaymentRow]


class RetriedMoneyCell(ReportModel):
    target_id: int = Field(ge=1)
    fact: Fact[Money]


class MoneyRetry(ReportModel):
    cells: list[RetriedMoneyCell]


class PageText(ReportModel):
    page: int = Field(ge=1)
    status: Literal["readable", "blank", "partial", "unreadable"]
    text: str | None


class Transcription(ReportModel):
    pages: list[PageText]
