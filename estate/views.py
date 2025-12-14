from django.shortcuts import render, redirect
from django.db import models
from django.db import transaction
from django.db.models.functions import TruncMonth
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from decimal import Decimal

from .models import (
    RentPayment,
    Expense,
    Tenant,
    Property,
    ExpenseCategory,
)

def _month_start(d: date) -> date:
    """Normalize any date/datetime to the first day of its month (date)."""
    if isinstance(d, datetime):
        d = d.date()
    return d.replace(day=1)


def _iter_month_starts(start_month: date, end_month: date):
    """Yield month starts from start_month to end_month inclusive."""
    start_month = _month_start(start_month)
    end_month = _month_start(end_month)
    m = start_month
    while m <= end_month:
        yield m
        m = (m + relativedelta(months=1)).replace(day=1)


def build_tenant_payment_status(tenants_qs, current_month_date):

    tenant_payment_status = []

    today_month = _month_start(date.today())
    selected_month_start = _month_start(current_month_date)

    for tenant in tenants_qs:
        rent = tenant.monthly_rent
        tenant_start_month = _month_start(tenant.start_date)

        # Include future months that already have payments (advance payments)
        last_paid_month = (
            RentPayment.objects.filter(tenant=tenant)
            .aggregate(m=models.Max("payment_month"))
            .get("m")
        )
        if last_paid_month:
            last_paid_month = _month_start(last_paid_month)

        # Ledger end month: max(selected, current month, last month with any payment)
        end_month = max(selected_month_start, today_month, last_paid_month or today_month)

        # Build paid-by-month dict over the ledger window
        payments_all = (
            RentPayment.objects.filter(tenant=tenant, payment_month__lte=end_month)
            .values("payment_month")
            .annotate(total=models.Sum("amount"))
        )
        paid_by_month = {
            _month_start(p["payment_month"]): (p["total"] or 0)
            for p in payments_all
        }

        # Selected month stats
        paid_selected = paid_by_month.get(selected_month_start, 0)
        due_selected = rent
        remaining_selected = due_selected - paid_selected

        partial_selected = paid_selected > 0 and paid_selected < rent

        # Evaluate all scheduled months up to the selected month (inclusive)
        # and compute cumulative outstanding accurately month-by-month.
        missed_months = 0
        missed_month_names = []
        cumulative_outstanding = 0

        for m in _iter_month_starts(tenant_start_month, selected_month_start):
            paid_m = paid_by_month.get(m, 0)
            remaining_m = rent - paid_m

            if remaining_m > 0:
                missed_months += 1
                cumulative_outstanding += remaining_m
                missed_month_names.append(m.strftime("%B %Y"))

        # Most recent first for display
        missed_month_names = list(reversed(missed_month_names))

        # HYBRID DISPLAY RULE:
        # - If selected month is partial, show only the selected month's remaining balance
        #   and only show the selected month name.
        if partial_selected:
            balance = max(rent - paid_selected, 0)
            missed_month_names = [selected_month_start.strftime("%B %Y")]
        else:
            balance = max(cumulative_outstanding, 0)

        # Status label
        if partial_selected:
            status_type = "Partial"
        elif balance > 0 and missed_months > 1:
            status_type = "Cumulative"
        elif balance > 0 and missed_months == 1:
            status_type = "Missed"
        else:
            status_type = "On Time"

        tenant_payment_status.append(
            {
                "tenant": tenant,
                "paid": paid_selected,
                "rent_due": rent,
                "balance": balance,
                "is_paid": balance <= 0,
                "missed_months": missed_months,
                "missed_month_names": missed_month_names,
                "status_type": status_type,
            }
        )

    totals = {
        "total_rent_due": sum(i["rent_due"] for i in tenant_payment_status),
        "total_paid": sum(i["paid"] for i in tenant_payment_status),
        "total_balance": sum(i["balance"] for i in tenant_payment_status),
    }

    return tenant_payment_status, totals

@staff_member_required
def dashboard(request):
    today = date.today()

    # ---------------------------------------------------------
    # 1. Read raw GET filters
    # ---------------------------------------------------------
    selected_property = request.GET.get("property")
    try:
        selected_property = int(selected_property)
    except (TypeError, ValueError):
        selected_property = None
            
    selected_month = request.GET.get("month")   # format "YYYY-MM"

    # ---------------------------------------------------------
    # 2. Standardize month parsing (ONE PLACE ONLY)
    # ---------------------------------------------------------
    if selected_month:
        try:
            # Convert "2025-03" into a date object
            parsed = datetime.strptime(selected_month, "%Y-%m")
            current_month_date = parsed.date().replace(day=1)
            year = parsed.year
            month = parsed.month
        except ValueError:
            # Invalid month → fallback to current month
            current_month_date = today.replace(day=1)
            year = current_month_date.year
            month = current_month_date.month
            selected_month = None
    else:
        # No filter → use current month
        current_month_date = today.replace(day=1)
        year = current_month_date.year
        month = current_month_date.month

    # ---------------------------------------------------------
    # Generate last 12 months for month dropdown
    # ---------------------------------------------------------

    month_choices = []
    for i in range(12):
        d = today - relativedelta(months=i)
        month_choices.append({
            "label": d.strftime("%B %Y"),
            "value": d.strftime("%Y-%m")
        })

    # ---------------------------------------------------------
    # 3. Human-readable filter labels
    # ---------------------------------------------------------
    # Property label (robust lookup)
    selected_property_name = "All Properties"
    if selected_property:
        try:
            selected_property_name = Property.objects.get(id=int(selected_property)).name
        except (ValueError, Property.DoesNotExist):
            selected_property_name = "All Properties"

    # Month label
    selected_month_label = current_month_date.strftime("%B %Y")

    # ---------------------------------------------------------
    # 4. Build filters for rent + expenses
    # ---------------------------------------------------------
    rent_filters = {
        "payment_month__year": year,
        "payment_month__month": month,
    }
    expense_filters = {
        "date__year": year,
        "date__month": month,
    }

    if selected_property:
        rent_filters["tenant__property__id"] = selected_property
        expense_filters["property__id"] = selected_property

    # ---------------------------------------------------------
    # 5. Top-level totals
    # ---------------------------------------------------------
    total_rent = (
        RentPayment.objects.filter(**rent_filters)
        .aggregate(total=models.Sum("amount"))["total"]
        or 0
    )

    total_expenses = (
        Expense.objects.filter(**expense_filters)
        .aggregate(total=models.Sum("amount"))["total"]
        or 0
    )

    net_profit = total_rent - total_expenses

    # ---------------------------------------------------------
    # 5B. All‑time Available Funds (UNFILTERED)
    # ---------------------------------------------------------
    all_time_rent = (
        RentPayment.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )
    all_time_expenses = (
        Expense.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )
    available_funds = all_time_rent - all_time_expenses

    # ---------------------------------------------------------
    # 6. Active tenants (respect property filter)
    # ---------------------------------------------------------
    tenants_qs = Tenant.objects.filter(active=True)
    if selected_property:
        tenants_qs = tenants_qs.filter(property__id=selected_property)

    active_tenants = tenants_qs.count()

    # ---------------------------------------------------------
    # 7. Property summary
    # ---------------------------------------------------------
    properties_qs = Property.objects.all()
    if selected_property:
        properties_qs = properties_qs.filter(id=selected_property)

    property_summaries = []
    for prop in properties_qs:
        rent_total = (
            RentPayment.objects.filter(
                tenant__property=prop,
                payment_month__year=year,
                payment_month__month=month
            ).aggregate(total=models.Sum("amount"))["total"] or 0
        )

        expense_total = (
            Expense.objects.filter(
                property=prop,
                date__year=year,
                date__month=month
            ).aggregate(total=models.Sum("amount"))["total"] or 0
        )

        property_summaries.append({
            "name": prop.name,
            "rent_total": rent_total,
            "expense_total": expense_total,
            "profit": rent_total - expense_total,
        })

    # ---------------------------------------------------------
    # 8. Monthly summary (CHART DATA — IGNORE FILTER MONTH)
    # ---------------------------------------------------------
    monthly_data = []
    rent_qs = RentPayment.objects.all()
    if selected_property:
        rent_qs = rent_qs.filter(tenant__property_id=selected_property)
    rent_by_month = rent_qs.annotate(
        month=TruncMonth("payment_month")
    ).values(
        "month"
    ).annotate(
        total_rent=models.Sum("amount")
    ).order_by("month")

    expense_qs = Expense.objects.all()
    if selected_property:
        expense_qs = expense_qs.filter(property_id=selected_property)
    expense_by_month = expense_qs.annotate(
        month=TruncMonth("date")
    ).values(
        "month"
    ).annotate(
        total_expense=models.Sum("amount")
    ).order_by("month")

    rent_dict = {i["month"]: i["total_rent"] for i in rent_by_month}
    expense_dict = {i["month"]: i["total_expense"] for i in expense_by_month}
    all_months = sorted(set(rent_dict.keys()) | set(expense_dict.keys()))

    for m in all_months:
        r = rent_dict.get(m, 0)
        e = expense_dict.get(m, 0)
        monthly_data.append({
            "month": m,
            "rent": r,
            "expense": e,
            "profit": r - e,
        })

    # ---------------------------------------------------------
    # 9. LATE TENANTS (Simplified logic)
    # ---------------------------------------------------------
    late_tenants = []
    for tenant in tenants_qs:
        payments = RentPayment.objects.filter(
            tenant=tenant,
            payment_month__year=current_month_date.year,
            payment_month__month=current_month_date.month,
        ).aggregate(total=models.Sum("amount"))
        total_paid_for_month = payments["total"] or 0
        if total_paid_for_month < tenant.monthly_rent:
            late_tenants.append(tenant)

    late_payments = len(late_tenants)

    # ---------------------------------------------------------
    # 10. Tenant Payment Breakdown
    # ---------------------------------------------------------
    tenant_payment_status, totals = build_tenant_payment_status(
        tenants_qs,
        current_month_date
    )

    total_rent_due = totals["total_rent_due"]
    total_paid = totals["total_paid"]
    total_balance = totals["total_balance"]
    collection_rate = round((total_paid / total_rent_due) * 100, 1) if total_rent_due else 0

    # ---------------------------------------------------------
    # 11. Recurring + one-time expenses
    # ---------------------------------------------------------
    recurring_expenses = (
        Expense.objects.filter(is_recurring=True, **expense_filters)
        .aggregate(total=models.Sum("amount"))["total"] or 0
    )
    one_time_expenses = (
        Expense.objects.filter(is_recurring=False, **expense_filters)
        .aggregate(total=models.Sum("amount"))["total"] or 0
    )

    # ---------------------------------------------------------
    # 12. Category summaries
    # ---------------------------------------------------------
    category_summaries = []
    for cat in ExpenseCategory.objects.all():
        cat_total = (
            Expense.objects.filter(category=cat, **expense_filters)
            .aggregate(total=models.Sum("amount"))["total"] or 0
        )
        category_summaries.append({"name": cat.name, "total": cat_total})

    # ---------------------------------------------------------
    # 13. Context Payload
    # ---------------------------------------------------------
    context = {
        "total_rent": total_rent,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "available_funds": available_funds,
        "late_payments": late_payments,
        "active_tenants": active_tenants,
        "property_summaries": property_summaries,
        "monthly_data": monthly_data,
        "late_tenants": late_tenants,
        "recurring_expenses": recurring_expenses,
        "one_time_expenses": one_time_expenses,
        "category_summaries": category_summaries,
        "all_properties": Property.objects.all(),
        "selected_property": str(selected_property),
        "selected_month": selected_month,
        "selected_property_name": selected_property_name,
        "selected_month_label": selected_month_label,
        "tenant_payment_status": tenant_payment_status,
        "total_rent_due": total_rent_due,
        "total_paid": total_paid,
        "total_balance": total_balance,
        "collection_rate": collection_rate,
        "month_choices": month_choices,
    }

    return render(request, "dashboard.html", context)




def analytics_view(request):
    return render(request, "analytics.html")


def payments_page(request):
    today = date.today()

    # ---------------------------------------------------------
    # Read filters
    # ---------------------------------------------------------
    selected_property = request.GET.get("property")
    try:
        selected_property = int(selected_property)
    except (TypeError, ValueError):
        selected_property = None

    selected_month = request.GET.get("month")

    # ---------------------------------------------------------
    # Parse month
    # ---------------------------------------------------------
    if selected_month:
        try:
            parsed = datetime.strptime(selected_month, "%Y-%m")
            current_month_date = parsed.date().replace(day=1)
            year = parsed.year
            month = parsed.month
        except ValueError:
            current_month_date = today.replace(day=1)
            year = current_month_date.year
            month = current_month_date.month
            selected_month = None
    else:
        current_month_date = today.replace(day=1)
        year = current_month_date.year
        month = current_month_date.month

    # ---------------------------------------------------------
    # Property dropdown list
    # ---------------------------------------------------------
    all_properties = Property.objects.all()

    # ---------------------------------------------------------
    # Build filters for payments
    # ---------------------------------------------------------
    payment_filters = {
        "payment_month__year": year,
        "payment_month__month": month,
    }
    if selected_property:
        payment_filters["tenant__property_id"] = selected_property

    # ---------------------------------------------------------
    # Load tenant payment status (same logic as dashboard)
    # ---------------------------------------------------------
    tenants_qs = Tenant.objects.filter(active=True)
    if selected_property:
        tenants_qs = tenants_qs.filter(property_id=selected_property)

    tenant_payment_status, totals = build_tenant_payment_status(
        tenants_qs,
        current_month_date
    )

    total_rent_due = totals["total_rent_due"]
    total_paid = totals["total_paid"]
    total_balance = totals["total_balance"]

    # ---------------------------------------------------------
    # Month dropdown list (12-month history)
    # ---------------------------------------------------------
    month_choices = []
    for i in range(12):
        d = today - relativedelta(months=i)
        month_choices.append({
            "label": d.strftime("%B %Y"),
            "value": d.strftime("%Y-%m")
        })

    context = {
        "all_properties": all_properties,
        "tenant_payment_status": tenant_payment_status,
        "selected_property": str(selected_property),
        "selected_month": selected_month,
        "current_month_date": current_month_date,
        "total_rent_due": total_rent_due,
        "total_paid": total_paid,
        "total_balance": total_balance,
        "month_choices": month_choices,
    }

    return render(request, "payments.html", context)


@login_required
def add_payment(request):
    tenant_id = request.GET.get("tenant") or request.POST.get("tenant")
    month = request.GET.get("month") or request.POST.get("month")

    tenant = None
    current_month_date = None

    if tenant_id:
        try:
            tenant = Tenant.objects.select_related("property").get(id=tenant_id)
        except Tenant.DoesNotExist:
            tenant = None

    # Parse month (YYYY-MM) -> first of month date
    if month:
        try:
            parsed = datetime.strptime(month, "%Y-%m")
            current_month_date = parsed.date().replace(day=1)
        except ValueError:
            current_month_date = date.today().replace(day=1)
    else:
        current_month_date = date.today().replace(day=1)

    #------testing----
    last_payment_month = (
    RentPayment.objects.filter(tenant=tenant)
    .aggregate(m=models.Max("payment_month"))
    .get("m")
    )

    if last_payment_month:
        last_payment_month = last_payment_month.replace(day=1)

    display_end_month = max(
        current_month_date,
        last_payment_month or current_month_date
    )

    #------end of testing------

    # ---------------------------------------------------------
    # LIVE Outstanding Rent Information (month-by-month truth)
    # ---------------------------------------------------------
    outstanding_months = []

    if tenant:
        tenant_start = tenant.start_date.replace(day=1)

        # We only care up to the selected month for display
        m = tenant_start
        # while m <= current_month_date: # testing
        while m <= display_end_month:
            paid = (
                RentPayment.objects.filter(tenant=tenant, payment_month=m)
                .aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
            )
            paid = Decimal(paid)
            due = Decimal(tenant.monthly_rent)
            remaining = due - paid

            if remaining > 0:
                outstanding_months.append({
                    "month": m,
                    "label": m.strftime("%B %Y"),
                    "remaining": remaining,
                    "type": "partial" if paid > 0 else "full",
                })

            m = (m + relativedelta(months=1)).replace(day=1)

    # ---------------------------------------------------------
    # Determine default "Payment For" month (UX only)
    # ---------------------------------------------------------
    default_payment_month = current_month_date

    if tenant:
        if outstanding_months:
            # Earliest unpaid / partially paid month
            default_payment_month = outstanding_months[0]["month"]
        else:
            # Tenant fully paid → suggest next month after latest fully-paid month
            last_paid = (
                RentPayment.objects.filter(tenant=tenant)
                .values("payment_month")
                .annotate(total=models.Sum("amount"))
                .order_by("payment_month")
            )

            latest_fully_paid = None
            for p in last_paid:
                if Decimal(p["total"]) >= Decimal(tenant.monthly_rent):
                    latest_fully_paid = p["payment_month"]

            if latest_fully_paid:
                default_payment_month = (
                    latest_fully_paid + relativedelta(months=1)
                ).replace(day=1)
            else:
                default_payment_month = tenant.start_date.replace(day=1)

    # Payments for the currently selected month (for display)
    if tenant:
        payments_for_month = RentPayment.objects.filter(
            tenant=tenant,
            payment_month__year=current_month_date.year,
            payment_month__month=current_month_date.month,
        ).aggregate(total=models.Sum("amount"))
        paid_for_month = Decimal(payments_for_month["total"] or 0)
        monthly_rent = tenant.monthly_rent
        remaining_balance = max(Decimal(monthly_rent) - paid_for_month, Decimal("0"))
        max_payable_amount = remaining_balance
    else:
        paid_for_month = Decimal("0")
        monthly_rent = None
        remaining_balance = Decimal("0")
        max_payable_amount = Decimal("0")

    # Handle POST: allocate the single incoming payment across months oldest-first
    if request.method == "POST" and tenant:
        amount_raw = request.POST.get("amount")
        month_raw = request.POST.get("month")
        payment_date_raw = request.POST.get("payment_date")

        # Parse amount to Decimal
        try:
            amount = Decimal(amount_raw)
        except (TypeError, InvalidOperation):
            amount = Decimal("0")

        # Non-negative only
        if amount <= 0:
            # Nothing to do; redirect back
            return redirect(f"/payments/?month={current_month_date.strftime('%Y-%m')}&property={tenant.property.id}")

        # Parse selected_month (YYYY-MM) used as the highest month to consider for allocation.
        try:
            parsed_month = datetime.strptime(month_raw, "%Y-%m")
            selected_month = parsed_month.date().replace(day=1)
        except (TypeError, ValueError):
            selected_month = current_month_date

        # Parse payment date (date when payment received)
        try:
            date_paid = datetime.strptime(payment_date_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            date_paid = date.today()

        # Determine allocation window:
        # Start at tenant.start_date (first of month) up through selected_month,
        # then allow allocation to future months if payment remains.
        tenant_start = tenant.start_date.replace(day=1)
        months = []
        m = tenant_start
        # include months up to selected_month
        while m <= selected_month:
            months.append(m)
            m = (m + relativedelta(months=1)).replace(day=1)

        # We'll extend months forward as needed when payment remains (advances / overpayments)
        remaining_amount = amount

        # Use transaction to keep allocations atomic
        with transaction.atomic():
            # First allocate across the historical window (oldest to newest)
            for m in months:
                if remaining_amount <= 0:
                    break
                paid = (
                    RentPayment.objects.filter(tenant=tenant, payment_month=m)
                    .aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
                )
                paid = Decimal(paid)
                month_due = Decimal(tenant.monthly_rent)
                remaining_due = max(month_due - paid, Decimal("0"))
                if remaining_due <= 0:
                    continue
                to_pay = min(remaining_due, remaining_amount)
                # Create allocation record
                RentPayment.objects.create(
                    tenant=tenant,
                    amount=to_pay,
                    payment_month=m,
                    date_paid=date_paid,
                )
                remaining_amount -= to_pay

            # If still have remaining_amount, allocate to future months sequentially
            if remaining_amount > 0:
                # Find the last month we considered; start from the next month after the latest existing rent-month
                if months:
                    last = months[-1]
                else:
                    last = tenant_start
                # advance to next month to allocate advances
                next_month = (last + relativedelta(months=1)).replace(day=1)
                # Keep allocating until remaining_amount exhausted
                while remaining_amount > 0:
                    month_due = Decimal(tenant.monthly_rent)
                    # Check if already any payments exist for this future month (possible if overpay previously)
                    paid = (
                        RentPayment.objects.filter(tenant=tenant, payment_month=next_month)
                        .aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
                    )
                    paid = Decimal(paid)
                    remaining_due = max(month_due - paid, Decimal("0"))
                    # If month already fully covered (unlikely), skip to next
                    if remaining_due <= 0:
                        next_month = (next_month + relativedelta(months=1)).replace(day=1)
                        continue
                    to_pay = min(remaining_due, remaining_amount)
                    RentPayment.objects.create(
                        tenant=tenant,
                        amount=to_pay,
                        payment_month=next_month,
                        date_paid=date_paid,
                    )
                    remaining_amount -= to_pay
                    if remaining_amount <= 0:
                        break
                    next_month = (next_month + relativedelta(months=1)).replace(day=1)

        # Redirect to payments page for the selected month and property
        return redirect(f"/payments/?month={selected_month.strftime('%Y-%m')}&property={tenant.property.id}")

    context = {
        "tenant": tenant,
        "property": tenant.property if tenant else None,
        "selected_month": default_payment_month.strftime("%Y-%m"),
        "selected_month_label": default_payment_month.strftime("%B %Y"),
        "monthly_rent": tenant.monthly_rent if tenant else None,
        "remaining_balance": remaining_balance,
        "paid_for_month": paid_for_month,
        "max_payable_amount": max_payable_amount,
        "outstanding_months": outstanding_months,
    }

    return render(request, "add_payment.html", context)



def tenants_view(request):
    return render(request, "tenants.html")


def settings_view(request):
    return render(request, "settings.html")

























# Legacy alias if needed (optional)
payments_view = payments_page