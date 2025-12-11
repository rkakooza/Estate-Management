from django.shortcuts import render
from django.db import models
from django.db.models.functions import TruncMonth
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.contrib.admin.views.decorators import staff_member_required

from .models import (
    RentPayment,
    Expense,
    Tenant,
    Property,
    ExpenseCategory,
)

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
            current_month_date = parsed.replace(day=1)
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
            payment_month=current_month_date
        ).aggregate(total=models.Sum("amount"))
        total_paid_for_month = payments["total"] or 0
        if total_paid_for_month < tenant.monthly_rent:
            late_tenants.append(tenant)

    late_payments = len(late_tenants)

    # ---------------------------------------------------------
    # 10. Tenant Payment Breakdown
    # ---------------------------------------------------------
    tenant_payment_status = []
    for tenant in tenants_qs:
        payments = RentPayment.objects.filter(
            tenant=tenant,
            payment_month=current_month_date
        ).aggregate(total=models.Sum("amount"))
        paid = payments["total"] or 0
        due = tenant.monthly_rent
        balance = due - paid

        # -----------------------------------------------------
        # FULL CUMULATIVE MISSED MONTH CALCULATION
        # -----------------------------------------------------

        # 1. Establish tenant active period
        tenant_start = tenant.start_date.replace(day=1)
        selected_month_start = current_month_date.replace(day=1)

        # 2. Count active months between start_date and selected_month
        active_months = (
            (selected_month_start.year - tenant_start.year) * 12
            + (selected_month_start.month - tenant_start.month)
            + 1
        )
        if active_months < 0:
            active_months = 0

        # 3. Determine how many distinct months the tenant has fully paid
        monthly_rent_amount = tenant.monthly_rent
        payments_all = (
            RentPayment.objects.filter(tenant=tenant)
            .values("payment_month")
            .annotate(total=models.Sum("amount"))
        )

        fully_paid_months = 0
        for entry in payments_all:
            if entry["total"] >= monthly_rent_amount:
                fully_paid_months += 1

        # 4. Cumulative missed months
        missed_months = max(active_months - fully_paid_months, 0)

        # 5. Missed month names (most recent first)
        missed_month_names = []
        for i in range(missed_months):
            missed_month_date = selected_month_start - relativedelta(months=i)
            missed_month_names.append(missed_month_date.strftime("%B %Y"))

        # -----------------------------
        # HYBRID LOGIC FOR PARTIAL PAYMENT
        # -----------------------------
        # partial_payment_exists = tenant paid something for current month
        partial_payment_exists = paid > 0 and paid < monthly_rent_amount

        if partial_payment_exists:
            # If cumulative logic shows at least 1 missed month, reduce by 1
            if missed_months > 0:
                missed_months -= 1

            # Missed month name becomes only the CURRENT month
            missed_month_names = [selected_month_start.strftime("%B %Y")]

            # Balance = remaining amount for the current month
            balance = monthly_rent_amount - paid
        else:
            # No partial payment → standard cumulative balance
            balance = missed_months * monthly_rent_amount

        # Determine status label for clarity
        if partial_payment_exists:
            status_type = "Partial"
        elif missed_months > 1:
            status_type = "Cumulative"
        elif missed_months == 1:
            status_type = "Missed"
        else:
            status_type = "On Time"

        tenant_payment_status.append({
            "tenant": tenant,
            "paid": paid,
            "rent_due": due,
            "balance": balance,
            "is_paid": balance <= 0,
            "missed_months": missed_months,
            "missed_month_names": missed_month_names,
            "status_type": status_type,
        })

    total_rent_due = sum(i["rent_due"] for i in tenant_payment_status)
    total_paid = sum(i["paid"] for i in tenant_payment_status)
    total_balance = sum(i["balance"] for i in tenant_payment_status)
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
        "selected_property": selected_property,
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