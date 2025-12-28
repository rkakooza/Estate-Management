"""
Minimal, read-only analytics helpers.
Pure aggregation only — no side effects, no writes.
"""

from datetime import date, datetime
from decimal import Decimal
from django.db.models import Sum
from .models import Expense, RentPayment


def _month_start(d):
    if isinstance(d, datetime):
        d = d.date()
    return d.replace(day=1)


def get_all_time_funds():
    """
    All-time available funds:
    total rent collected minus total expenses.
    """
    total_rent = (
        RentPayment.objects.aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    total_expenses = (
        Expense.objects.aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    return {
        "total_rent": total_rent,
        "total_expenses": total_expenses,
        "available_funds": total_rent - total_expenses,
    }


def get_month_snapshot(month_date=None):
    """
    Snapshot for a selected month:
    rent collected, expenses, and net result.
    """
    if month_date is None:
        month_date = date.today()
    m = _month_start(month_date)

    rent = (
        RentPayment.objects.filter(
            payment_month__year=m.year,
            payment_month__month=m.month,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    expenses = (
        Expense.objects.filter(
            date__year=m.year,
            date__month=m.month,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    return {
        "month": m,
        "rent": rent,
        "expenses": expenses,
        "net": rent - expenses,
    }


def get_expense_breakdown(month_date=None):
    """
    Expense totals by category for a selected month.
    Returns a list of dicts: {category, total}
    """
    if month_date is None:
        month_date = date.today()
    m = _month_start(month_date)

    qs = (
        Expense.objects.filter(
            date__year=m.year,
            date__month=m.month,
        )
        .values("category__name")
        .annotate(total=Sum("amount"))
        .order_by("-total")
    )

    return [
        {
            "category": row["category__name"] or "Uncategorized",
            "total": row["total"] or Decimal("0"),
        }
        for row in qs
    ]
