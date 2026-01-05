from django.shortcuts import render, redirect
from django.db import models
from django.db import transaction
from django.db.models.functions import TruncMonth
from datetime import date, datetime
from dateutil.relativedelta import relativedelta
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
from decimal import Decimal, InvalidOperation
import csv
from estate.models import TenantRent
from django.shortcuts import get_object_or_404
from django.contrib.auth import authenticate, login
from django.contrib.auth.views import LogoutView
from django.contrib import messages

from .models import (
    RentPayment,
    Expense,
    Tenant,
    Property,
    ExpenseCategory,
    CommissionRate,
    Employee,
    EmployeeSalary,
    OtherIncome,
)
from django.dispatch import receiver
from django.contrib.auth.views import PasswordChangeView
from django.urls import reverse_lazy

        
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect("dashboard")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, "login.html")

def get_commission_rate_for_date(date_paid: date) -> Decimal:
    """
    Returns the commission percentage applicable on the given payment date.
    Falls back to 0 if no rate is configured.
    """
    row = (
        CommissionRate.objects
        .filter(effective_from__lte=date_paid.replace(day=1))
        .order_by("-effective_from")
        .values_list("percentage")
        .first()
    )
    return row[0] if row else Decimal("0")

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



def get_rent_for_month(tenant, month_date):
    row = (
        TenantRent.objects
        .filter(tenant=tenant, effective_from__lte=month_date)
        .order_by("-effective_from")
        .values_list("rent_amount")
        .first()
    )
    return row[0] if row else Decimal("0")

def get_salary_for_month(employee, month_date):
    month_start = month_date.replace(day=1)

    row = (
        EmployeeSalary.objects
        .filter(employee=employee, effective_from__lte=month_start)
        .order_by("-effective_from")
        .values_list("salary_amount")
        .first()
    )
    return row[0] if row else None

def build_tenant_payment_status(tenants_qs, current_month_date):

    tenant_payment_status = []

    today_month = _month_start(date.today())
    selected_month_start = _month_start(current_month_date)

    for tenant in tenants_qs:
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
        due_selected = get_rent_for_month(tenant, selected_month_start)

        # Safety: Month before tenant start
        if due_selected is None:
            due_selected = Decimal("0")
            remaining_selected = Decimal("0")
            partial_selected = False
        else:
            remaining_selected = due_selected - paid_selected
            partial_selected = paid_selected > 0 and paid_selected < due_selected

        # Evaluate all scheduled months up to the selected month (inclusive)
        # and compute cumulative outstanding accurately month-by-month.
        missed_months = 0
        missed_month_names = []
        cumulative_outstanding = 0

        for m in _iter_month_starts(tenant_start_month, selected_month_start):
            paid_m = paid_by_month.get(m, 0)
            rent_m = get_rent_for_month(tenant, m)
            # Safety skip for months with no applicable rent
            if rent_m is None:
                continue

            remaining_m = rent_m - paid_m

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
            balance = max(due_selected - paid_selected, 0)
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
                "rent_due": due_selected,
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

    all_time_other_income = (
        OtherIncome.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )

    all_time_expenses = (
        Expense.objects.aggregate(total=models.Sum("amount"))["total"] or 0
    )
    available_funds = (all_time_rent + all_time_other_income  - all_time_expenses)

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




@login_required
def analytics_view(request):
    """
    Minimal, read-only analytics view.
    Uses pure helper functions from analytics.py.
    """
    from .analytics import (
        get_all_time_funds,
        get_month_snapshot,
        get_expense_breakdown,
    )

    # --- Parse selected month (YYYY-MM) ---
    selected_month = request.GET.get("month")
    today = date.today().replace(day=1)

    if selected_month:
        try:
            parsed = datetime.strptime(selected_month, "%Y-%m")
            month_date = parsed.date().replace(day=1)
        except ValueError:
            month_date = today
    else:
        month_date = today

    # --- Analytics data ---
    all_time = get_all_time_funds()
    monthly = get_month_snapshot(month_date)
    expense_breakdown = get_expense_breakdown(month_date)

    context = {
        # Month
        "selected_month": month_date.strftime("%Y-%m"),
        "selected_month_label": month_date.strftime("%B %Y"),

        # All‑time
        "all_time_rent": all_time["total_rent"],
        "all_time_expenses": all_time["total_expenses"],
        "available_funds": all_time["available_funds"],

        # Monthly snapshot
        "monthly_rent": monthly["rent"],
        "monthly_expenses": monthly["expenses"],
        "monthly_net": monthly["net"],

        # Expense breakdown
        "expense_breakdown": expense_breakdown,
    }

    return render(request, "analytics.html", context)


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

    # Fix UnboundLocalError for selected_month
    selected_month = current_month_date

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
            due = Decimal(get_rent_for_month(tenant, m))
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
                month_start = p["payment_month"].replace(day=1)
                rent_due = Decimal(get_rent_for_month(tenant, month_start))
                paid_total = Decimal(p["total"] or 0)

                if paid_total >= rent_due:
                    latest_fully_paid = month_start
                else:
                    break

            if latest_fully_paid:
                default_payment_month = (
                    latest_fully_paid + relativedelta(months=1)
                ).replace(day=1)
            else:
                default_payment_month = tenant.start_date.replace(day=1)
                

    # Month actually used for calculations (reflects Payment For)
    effective_month = default_payment_month

    # Payments for the currently selected month (for display)
    if tenant:
        payments_for_month = RentPayment.objects.filter(
            tenant=tenant,
            payment_month__year=effective_month.year,
            payment_month__month=effective_month.month,
        ).aggregate(total=models.Sum("amount"))
        paid_for_month = Decimal(payments_for_month["total"] or 0)
        # Fix incorrect rent lookup and Decimal consistency
        monthly_rent = Decimal(get_rent_for_month(tenant, effective_month) or 0)
        remaining_balance = max(monthly_rent - paid_for_month, Decimal("0"))
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

        # Step 1: Track total collected amount
        collected_amount = Decimal("0")

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
                month_due = Decimal(get_rent_for_month(tenant, m))
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
                # Step 2: Add to collected_amount
                collected_amount += to_pay
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
                    month_due = Decimal(get_rent_for_month(tenant, next_month))
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
                    # Step 2: Add to collected_amount
                    collected_amount += to_pay
                    remaining_amount -= to_pay
                    if remaining_amount <= 0:
                        break
                    next_month = (next_month + relativedelta(months=1)).replace(day=1)

            # Step 3: After allocations, create rent collection commission expense
            # Automatically record rent collection commission (time-effective)
            commission_percentage = get_commission_rate_for_date(date_paid)
            commission_amount = (
                collected_amount * commission_percentage / Decimal("100")
            ).quantize(Decimal("0.01"))

            if commission_amount > 0:
                commission_category, _ = ExpenseCategory.objects.get_or_create(
                    name="Financial & Fees"
                )
                Expense.objects.create(
                    amount=commission_amount,
                    description="Rent Collection Fee",
                    is_recurring=True,
                    date=date_paid,
                    property=tenant.property,
                    category=commission_category,
                )

        # Redirect to payments page for the default payment month and property
        return redirect(f"/payments/?month={default_payment_month.strftime('%Y-%m')}&property={tenant.property.id}")

    # Safety fallback for default_payment_month
    if not default_payment_month:
        default_payment_month = current_month_date

    context = {
        "tenant": tenant,
        "property": tenant.property if tenant else None,
        "selected_month": effective_month.strftime("%Y-%m"),
        "selected_month_label": effective_month.strftime("%B %Y"),
        "monthly_rent": monthly_rent if tenant else None,
        "remaining_balance": remaining_balance,
        "paid_for_month": paid_for_month,
        "max_payable_amount": max_payable_amount,
        "outstanding_months": outstanding_months,
        "current_month_date": current_month_date, # remove if problem occur
    }

    return render(request, "add_payment.html", context)


# ------------------- Start of Payment History (Read-only) -------------------
@login_required
def payments_history(request):
    """
    Read-only chronological payment history.
    Default: tenant-scoped if tenant id provided.
    """

    tenant_id = request.GET.get("tenant")

    # Load all tenants for the dropdown/filter
    all_tenants = Tenant.objects.select_related("property").order_by("name")

    payments_qs = RentPayment.objects.select_related(
        "tenant",
        "tenant__property",
    ).order_by("-date_paid", "id")

    tenant = None
    if tenant_id:
        try:
            tenant = Tenant.objects.get(id=tenant_id)
            payments_qs = payments_qs.filter(tenant=tenant)
        except Tenant.DoesNotExist:
            tenant = None

    context = {
        "payments": payments_qs,
        "tenant": tenant,
        "all_tenants": all_tenants,
    }

    return render(request, "payments_history.html", context)

# ------------------- CSV Export for Payment History -------------------
@login_required
def payments_history_csv(request):
    """
    Export payment history as CSV.
    Respects optional tenant filter.
    """
    tenant_id = request.GET.get("tenant")

    payments_qs = RentPayment.objects.select_related(
        "tenant",
        "tenant__property",
    ).order_by("-date_paid", "id")

    if tenant_id:
        try:
            tenant = Tenant.objects.get(id=tenant_id)
            payments_qs = payments_qs.filter(tenant=tenant)
        except Tenant.DoesNotExist:
            pass

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="payment_history.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "payment_id",
        "tenant_name",
        "property_name",
        "payment_for_month",
        "amount",
        "date_paid",
    ])

    for p in payments_qs:
        writer.writerow([
            p.id,
            p.tenant.name,
            p.tenant.property.name if p.tenant.property else "",
            p.payment_month.strftime("%Y-%m"),
            p.amount,
            p.date_paid,
        ])

    return response


# ------------------- Start of Tenants View-------------------
@login_required
def tenants_view(request):
    tenants = (
        Tenant.objects
        .select_related("property")
        .order_by("property__name", "name")
    )

    context = {
        "tenants": tenants,
    }

    return render(request, "tenants.html", context)


# ------------------- Add Tenant View  -------------------
@login_required
def add_tenant(request):
    """
    Add a new tenant and initialize rent history.
    NOTE:
    - Tenant creation must always be paired with an initial TenantRent.
    """
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        property_id = request.POST.get("property")
        start_date_raw = request.POST.get("start_date")
        phone = request.POST.get("phone")
        email = request.POST.get("email")
        rent_raw = request.POST.get("initial_rent")

        # --- Basic validation ---
        if not name or not property_id or not start_date_raw or not rent_raw:
            return render(request, "add_tenant.html", {
                "properties": Property.objects.all().order_by("name"),
                "error": "Name, property, start date, and rent are required.",
                "form_data": request.POST,
            })

        try:
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return render(request, "add_tenant.html", {
                "properties": Property.objects.all().order_by("name"),
                "error": "Invalid start date.",
                "form_data": request.POST,
            })

        try:
            initial_rent = Decimal(rent_raw)
            if initial_rent <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            return render(request, "add_tenant.html", {
                "properties": Property.objects.all().order_by("name"),
                "error": "Initial rent must be a positive number.",
                "form_data": request.POST,
            })

        # Normalize effective rent month = tenant start month (accounting rule)
        effective_month = start_date.replace(day=1)

        with transaction.atomic():
            tenant = Tenant.objects.create(
                name=name,
                property_id=int(property_id),
                start_date=start_date,
                phone=phone,
                email=email,
                active=True,
                monthly_rent=initial_rent, 
            )

            TenantRent.objects.create(
                tenant=tenant,
                rent_amount=initial_rent,
                effective_from=effective_month,
            )

        return redirect("tenant_details", tenant_id=tenant.id)

    # GET: render empty Add Tenant form
    properties = Property.objects.all().order_by("name")

    context = {
        "properties": properties,
    }

    return render(request, "add_tenant.html", context)

# ------------------- Edit Tenant View (with Rent Change Validation) -------------------
@login_required
def edit_tenant(request, tenant_id):
    """
    Edit tenant core details and optionally schedule a rent change.
    IMPORTANT RULES:
    - Rent changes NEVER affect past months.
    - New rent becomes effective from a selected future month (inclusive).
    - Only one rent schedule per tenant per month (no duplicates).
    - Rent changes are non-retroactive: effective_from >= current month.
    - All changes are atomic (all-or-nothing).
    """
    tenant = get_object_or_404(Tenant, id=tenant_id)
    properties = Property.objects.all().order_by("name")
    
    if request.method == "GET":
        today_month = date.today().replace(day=1)
        current_rent_entry = (
            TenantRent.objects
            .filter(tenant=tenant, effective_from__lte=today_month)
            .order_by("-effective_from")
            .first()
        )
        current_rent = (
            current_rent_entry.rent_amount
            if current_rent_entry
            else tenant.monthly_rent
        )
        current_rent_effective = (
            current_rent_entry.effective_from
            if current_rent_entry
            else tenant.start_date
        )
        return render(request, "edit_tenant.html", {
            "tenant": tenant,
            "properties": properties,
            "current_rent": current_rent,
            "current_rent_effective": current_rent_effective,
        })

    if request.method == "POST":
        with transaction.atomic():
            # --- Basic tenant fields ---
            tenant.name = request.POST.get("name", tenant.name)
            tenant.phone = request.POST.get("phone", tenant.phone)
            tenant.email = request.POST.get("email", tenant.email)

            property_id = request.POST.get("property")
            if property_id:
                tenant.property_id = int(property_id)

            # --- Optional rent change ---
            rent_amount_raw = request.POST.get("new_rent")
            effective_month_raw = request.POST.get("rent_effective_month")

            if rent_amount_raw and effective_month_raw:
                try:
                    new_rent = Decimal(rent_amount_raw)
                    # Normalize effective_month to first of month (accounting rule)
                    effective_month = datetime.strptime(
                        effective_month_raw, "%Y-%m"
                    ).date().replace(day=1)
                except (InvalidOperation, ValueError):
                    new_rent = None
                    effective_month = None

                if new_rent is not None and effective_month is not None:
                    today_month = date.today().replace(day=1)
                    # Backend validation: new_rent must be > 0
                    if new_rent <= 0:
                        return render(
                            request,
                            "edit_tenant.html",
                            {
                                "tenant": tenant,
                                "properties": properties,
                                "error": "Rent amount must be greater than zero.",
                                "form_data": request.POST,
                            },
                        )
                    # Backend validation: effective_month must be current or future month
                    if effective_month < today_month:
                        return render(
                            request,
                            "edit_tenant.html",
                            {
                                "tenant": tenant,
                                "properties": properties,
                                "error": "Rent changes must start from the current or a future month.",
                                "form_data": request.POST,
                            },
                        )

                    # Only one rent schedule per tenant per month (no duplicates)
                    rent_obj = TenantRent.objects.filter(
                        tenant=tenant,
                        effective_from=effective_month
                    ).first()
                    if rent_obj:
                        # If a record for this tenant+month exists, update it (accounting rule)
                        rent_obj.rent_amount = new_rent
                        rent_obj.save()
                    else:
                        # Otherwise, create a new rent record (history-preserving)
                        TenantRent.objects.create(
                            tenant=tenant,
                            rent_amount=new_rent,
                            effective_from=effective_month,
                        )

            tenant.save()
        return redirect("tenant_details", tenant_id=tenant.id)

    return redirect("tenant_details", tenant_id=tenant.id)

# ------------------- Tenant Detail View -------------------
@login_required
def tenant_details(request, tenant_id):
    """
    Tenant detail / edit page.
    - Read-only payment history
    - Rent history (future-effective only)
    - Deactivate tenant support
    """
    try:
        tenant = (
            Tenant.objects
            .select_related("property")
            .get(id=tenant_id)
        )
    except Tenant.DoesNotExist:
        return render(request, "404.html", status=404)

    # Determine current effective rent (non-retroactive)
    today_month = date.today().replace(day=1)
    current_rent_entry = (
        TenantRent.objects
        .filter(tenant=tenant, effective_from__lte=today_month)
        .order_by("-effective_from")
        .first()
    )
    current_rent = (
        current_rent_entry.rent_amount
        if current_rent_entry
        else tenant.monthly_rent
    )
    current_rent_effective = (
        current_rent_entry.effective_from
        if current_rent_entry
        else tenant.start_date
    )

    # Compute outstanding balance and payment status for current month
    from decimal import Decimal
    current_month_date = date.today().replace(day=1)
    tenant_status_list, _ = build_tenant_payment_status(
        Tenant.objects.filter(id=tenant.id),
        current_month_date
    )
    if tenant_status_list:
        outstanding_balance = tenant_status_list[0].get("balance", Decimal("0"))
        status_type = tenant_status_list[0].get("status_type", "On Time")
    else:
        outstanding_balance = Decimal("0")
        status_type = "On Time"

    # Rent history (chronological)
    rent_history = (
        TenantRent.objects
        .filter(tenant=tenant)
        .order_by("effective_from")
    )

    # Payment history (most recent first, read-only)
    payments = (
        RentPayment.objects
        .filter(tenant=tenant)
        .order_by("-date_paid", "-id")
    )
    

    context = {
        "tenant": tenant,
        "rent_history": rent_history,
        "payments": payments,
        "outstanding_balance": outstanding_balance,
        "payment_status": status_type,
        "current_rent": current_rent,
        "current_rent_effective": current_rent_effective,
    }

    return render(request, "tenant_details.html", context)

# ------------------- Toggle Tenant Active Status -------------------
from django.shortcuts import get_object_or_404
@login_required
def toggle_tenant_active(request, tenant_id):
    """
    Toggle the active status of a tenant.
    If POST: deactivate or activate and set end_date accordingly.
    Otherwise: redirect to tenant details.
    """
    # Only allow POST
    if request.method != "POST":
        return redirect("tenant_details", tenant_id=tenant_id)

    try:
        tenant = Tenant.objects.get(id=tenant_id)
    except Tenant.DoesNotExist:
        return redirect("tenants_view")

    if tenant.active:
        tenant.active = False
        tenant.end_date = date.today()
    else:
        tenant.active = True
        tenant.end_date = None
    tenant.save()
    return redirect("tenant_details", tenant_id=tenant_id)


# ------------------- Add Employee View -------------------
@login_required
def add_employee(request):
    """
    Add a new employee and initialize salary history.
    RULES:
    - Employee must be assigned to a property
    - Initial salary is stored in EmployeeSalary (non‑retroactive)
    - Salary effective month = start_date month
    """
    properties = Property.objects.all().order_by("name")

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        role = request.POST.get("role", "").strip()
        phone = request.POST.get("phone", "").strip()
        property_id = request.POST.get("property")
        salary_raw = request.POST.get("monthly_salary")
        start_date_raw = request.POST.get("start_date")

        # --- Validation ---
        if not name or not property_id or not salary_raw or not start_date_raw:
            return render(request, "add_employee.html", {
                "properties": properties,
                "error": "Name, property, salary, and start date are required.",
                "form_data": request.POST,
            })

        try:
            salary_amount = Decimal(salary_raw)
            if salary_amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            return render(request, "add_employee.html", {
                "properties": properties,
                "error": "Salary must be a positive number.",
                "form_data": request.POST,
            })

        try:
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d").date()
        except ValueError:
            return render(request, "add_employee.html", {
                "properties": properties,
                "error": "Invalid start date.",
                "form_data": request.POST,
            })

        # Normalize salary effective month (accounting rule)
        effective_month = start_date.replace(day=1)

        with transaction.atomic():
            employee = Employee.objects.create(
                name=name,
                role=role,
                phone=phone,
                property_id=int(property_id),
                monthly_salary=salary_amount, 
                start_date=start_date,
                active=True,
            )

            EmployeeSalary.objects.create(
                employee=employee,
                salary_amount=salary_amount,
                effective_from=effective_month,
            )

        return redirect("employees_list")

    # GET
    return render(request, "add_employee.html", {
        "properties": properties,
    })

# ------------------- Pay Salary View -------------------
@login_required
def pay_salary(request, employee_id):
    """
    Pay salary for a specific employee for a selected month.
    This creates an Expense record (accounting-safe).
    """
    employee = get_object_or_404(Employee, id=employee_id, active=True)

    if request.method == "POST":
        month_raw = request.POST.get("month")
        date_paid_raw = request.POST.get("date_paid")

        # Parse salary month (YYYY-MM)
        try:
            parsed = datetime.strptime(month_raw, "%Y-%m")
            salary_month = parsed.date().replace(day=1)
        except (TypeError, ValueError):
            salary_month = date.today().replace(day=1)

        # Immediately after parsing salary_month
        effective_month = salary_month
        selected_month_label = effective_month.strftime("%B %Y")

        # Fetch salary AFTER parsing month
        salary_amount = get_salary_for_month(employee, salary_month)

        if salary_amount is None:
            return render(
                request,
                "pay_salary.html",
                {
                    "employee": employee,
                    "default_month": salary_month.strftime("%Y-%m"),
                    "error": "No salary configured for this employee for the selected month.",
                },
            )

        # ---- BLOCK DOUBLE SALARY PAYMENTS ----
        # salary_label = f"Salary — {employee.name} ({salary_month.strftime('%B %Y')})"

        # already_paid = Expense.objects.filter(
        #     description=salary_label,
        # ).exists()
        salary_category = ExpenseCategory.objects.filter(name__iexact="salary").first()

        marker = f"[Emp #{employee.id}]"
        salary_label = f"Salary — {employee.name} {marker} ({salary_month.strftime('%B %Y')})"

        already_paid = Expense.objects.filter(
            category=salary_category,
            date=salary_month,               # salary month (ledger month)
            description__contains=marker,     # stable even if name changes
        ).exists()

        if already_paid:
            return render(
                request,
                "pay_salary.html",
                {
                    "employee": employee,
                    "default_month": effective_month.strftime("%Y-%m"),
                    "salary_amount": salary_amount,
                    "selected_month_label": selected_month_label,
                    "error": "Salary has already been paid for this employee for the selected month.",
                },
            )

        # Parse payment date
        try:
            date_paid = datetime.strptime(date_paid_raw, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            date_paid = date.today()

        # Create salary expense
        Expense.objects.create(
            # employee=employee,
            amount=salary_amount,
            description=salary_label,
            is_recurring=False,
            # date=date_paid,
            date=salary_month,
            property=employee.property,
            category=salary_category,
        )

        return redirect("expenses_ledger")

    # GET: Determine default salary month (same UX rule as tenants)
    month_raw = request.GET.get("month")

    # --- Determine default salary month (same UX rule as tenants) ---
    last_paid_expense = (
        Expense.objects
        .filter(
            category__name__iexact="salary",
            description__startswith=f"Salary — {employee.name} ("
        )
        .order_by("-date")
        .first()
    )

    if month_raw:
        try:
            salary_month = datetime.strptime(month_raw, "%Y-%m").date().replace(day=1)
        except ValueError:
            salary_month = date.today().replace(day=1)
    elif last_paid_expense:
        # If already paid, default to NEXT month
        salary_month = (last_paid_expense.date.replace(day=1) + relativedelta(months=1))
    else:
        # Never paid → start from current month
        salary_month = date.today().replace(day=1)

    # After determining salary_month
    effective_month = salary_month
    selected_month_label = effective_month.strftime("%B %Y")

    salary_amount = get_salary_for_month(employee, salary_month)

    # ---------------------------------------
    # Determine last paid salary month (UX)
    # ---------------------------------------
    last_paid_expense = (
        Expense.objects
        .filter(
            category__name__iexact="salary",
            description__startswith=f"Salary — {employee.name} ("
        )
        .order_by("-date")
        .first()
    )

    last_paid_month = None
    if last_paid_expense:
        try:
            # Extract YYYY-MM from description
            last_paid_month = last_paid_expense.description.split("—")[-1].strip()
        except Exception:
            last_paid_month = None

    context = {
        "employee": employee,
        "default_month": salary_month.strftime("%Y-%m"),
        "salary_amount": salary_amount,
        "last_paid_month": last_paid_month,
        "selected_month_label": selected_month_label,
    }
    return render(request, "pay_salary.html", context)

# ------------------- Change Salary / Raise (non-retroactive) -------------------
@login_required
def change_salary(request, employee_id):
    """
    Schedule a salary change (raise or adjustment) for an employee.
    RULES:
    - Non-retroactive: effective month must be current or future.
    - One salary record per employee per effective month.
    """
    employee = get_object_or_404(Employee, id=employee_id)

    if request.method != "POST":
        return redirect("pay_salary", employee_id=employee.id)

    new_salary_raw = request.POST.get("new_salary")
    effective_month_raw = request.POST.get("effective_month")

    if not new_salary_raw or not effective_month_raw:
        return redirect("pay_salary", employee_id=employee.id)

    try:
        new_salary = Decimal(new_salary_raw)
        if new_salary <= 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError):
        return redirect("pay_salary", employee_id=employee.id)

    try:
        effective_month = (
            datetime.strptime(effective_month_raw, "%Y-%m")
            .date()
            .replace(day=1)
        )
    except (TypeError, ValueError):
        return redirect("pay_salary", employee_id=employee.id)

    today_month = date.today().replace(day=1)
    if effective_month < today_month:
        # Block retroactive changes
        return redirect("pay_salary", employee_id=employee.id)

    with transaction.atomic():
        salary_obj = (
            EmployeeSalary.objects
            .filter(employee=employee, effective_from=effective_month)
            .first()
        )

        if salary_obj:
            salary_obj.salary_amount = new_salary
            salary_obj.save()
        else:
            EmployeeSalary.objects.create(
                employee=employee,
                salary_amount=new_salary,
                effective_from=effective_month,
            )

        # Keep legacy field in sync for display only
        employee.monthly_salary = new_salary
        employee.save(update_fields=["monthly_salary"])

    return redirect(
        f"/employees/{employee.id}/pay/?month={effective_month.strftime('%Y-%m')}"
    )

# ------------------- Expense Ledger (Read-only) -------------------
@login_required
def expenses_ledger(request):
    """
    Monthly expense ledger (read-only).
    Shows all expenses for a selected month, optionally filtered by property.
    """
    today = date.today().replace(day=1)
    

    # --- Read filters ---
    selected_month = request.GET.get("month")
    selected_property = request.GET.get("property")

    try:
        selected_property = int(selected_property)
    except (TypeError, ValueError):
        selected_property = None

    # --- Parse month ---
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

    # --- Base queryset ---
    expenses_qs = Expense.objects.filter(
        date__year=year,
        date__month=month,
    ).select_related("property", "category").order_by("-date", "-id")

    if selected_property:
        expenses_qs = expenses_qs.filter(property_id=selected_property)

    # --- Totals ---
    total_expenses = (
        expenses_qs.aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
    )

    recurring_total = (
        expenses_qs.filter(is_recurring=True)
        .aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
    )

    one_time_total = (
        expenses_qs.filter(is_recurring=False)
        .aggregate(total=models.Sum("amount"))["total"] or Decimal("0")
    )

    # --- Month ---
    month_choices = []
    for i in range(12):
        m = today - relativedelta(months=i)
        month_choices.append({
            "label": m.strftime("%B %Y"),
            "value": m.strftime("%Y-%m")
        })

    context = {
        "expenses": expenses_qs,
        "total_expenses": total_expenses,
        "recurring_total": recurring_total,
        "one_time_total": one_time_total,
        "all_properties": Property.objects.all(),
        "selected_property": str(selected_property),
        "selected_month": selected_month,
        "selected_month_label": current_month_date.strftime("%B %Y"),
        "month_choices": month_choices,
    }

    return render(request, "expenses_ledger.html", context)

# ------------------- Add Expense View -------------------
@login_required
def add_expense(request):
    """
    Add a new expense (recurring or one-time).
    Expenses always subtract from Available Funds.
    """
    properties = Property.objects.all().order_by("name")
    categories = ExpenseCategory.objects.all().order_by("name")

    if request.method == "POST":
        amount_raw = request.POST.get("amount")
        description = request.POST.get("description", "").strip()
        expense_type = request.POST.get("expense_type")  # 'recurring' or 'one_time'
        category_id = request.POST.get("category")
        property_id = request.POST.get("property")
        date_raw = request.POST.get("date")

        # --- Validation ---
        try:
            amount = Decimal(amount_raw)
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            return render(request, "add_expense.html", {
                "properties": properties,
                "categories": categories,
                "error": "Expense amount must be a positive number.",
                "form_data": request.POST,
            })

        if not description:
            return render(request, "add_expense.html", {
                "properties": properties,
                "categories": categories,
                "error": "Description is required.",
                "form_data": request.POST,
            })

        if not property_id:
            return render(request, "add_expense.html", {
                "properties": properties,
                "categories": categories,
                "error": "Property is required.",
                "form_data": request.POST,
            })

        if expense_type not in ("recurring", "one_time"):
            return render(request, "add_expense.html", {
                "properties": properties,
                "categories": categories,
                "error": "Please select an expense type.",
                "form_data": request.POST,
            })

        try:
            expense_date = (
                datetime.strptime(date_raw, "%Y-%m-%d").date()
                if date_raw else date.today()
            )
        except ValueError:
            expense_date = date.today()

        is_recurring = expense_type == "recurring"

        # --- Create expense ---
        Expense.objects.create(
            amount=amount,
            description=description,
            is_recurring=is_recurring,
            date=expense_date,
            property_id=int(property_id),
            category_id=int(category_id) if category_id else None,
        )

        return redirect("dashboard")

    # GET request
    context = {
        "properties": properties,
        "categories": categories,
    }
    return render(request, "add_expense.html", context)

# ------------------- Employee List (Read-only) -------------------
@login_required
def employees_list(request):
    """
    Read-only list of employees.
    Salaries are paid via Expense records.
    """
    employees = (
        Employee.objects
        .order_by("name")
    )

    context = {
        "employees": employees,
    }

    return render(request, "employees_list.html", context)

# ------------------- Toggle Employee Active Status -------------------
@login_required
def toggle_employee_active(request, employee_id):
    if request.method != "POST":
        return redirect("employees_list")

    employee = get_object_or_404(Employee, id=employee_id)

    if employee.active:
        employee.active = False
        employee.end_date = date.today()
    else:
        employee.active = True
        employee.end_date = None

    employee.save()
    return redirect("employees_list")


# Legacy alias if needed (optional)
payments_view = payments_page


class ForcePasswordChangeView(PasswordChangeView):
    template_name = "password_change.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        if hasattr(self.request.user, "userprofile"):
            self.request.user.userprofile.must_change_password = False
            self.request.user.userprofile.save()

        messages.success(
            self.request,
            "Your password has been updated successfully."
        )
        return response
    


def forgot_password_view(request):
    """
    Static guidance page for users who forgot their password.
    Passwords are reset by an administrator.
    """
    return render(request, "forgot_password.html")