from django.db import models

class Property(models.Model):
    name = models.CharField(max_length=100, unique=True)
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    

class Tenant(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='tenants')
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)

    monthly_rent = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)  # if tenant moved out, set inactive

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.property.name})"


class TenantRent(models.Model):
    tenant = models.ForeignKey(
        "Tenant",
        on_delete=models.CASCADE,
        related_name="rent_history",
    )

    # Rent amount that becomes active starting this month
    rent_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Always store as the first day of a month (YYYY-MM-01)
    effective_from = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        indexes = [
            models.Index(fields=["tenant", "effective_from"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "effective_from"],
                name="unique_rent_per_tenant_per_month",
            )
        ]

    def __str__(self):
        return f"{self.tenant.name} — {self.rent_amount} from {self.effective_from:%Y-%m}"
    

class CommissionRate(models.Model):
    """
    Time‑effective commission percentage for rent collection.
    Used to determine the commission rate applicable at the time rent is collected.
    """
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Commission percentage (e.g. 10.00 for 10%)",
    )

    # Always store as first day of month (YYYY‑MM‑01)
    effective_from = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["effective_from"],
                name="unique_commission_rate_per_month",
            )
        ]

    def __str__(self):
        return f"{self.percentage}% from {self.effective_from:%Y-%m}"
    

def get_rent_for_month(tenant, month):
    rent_record = (
        tenant.rent_history
        .filter(effective_from__lte=month)
        .order_by("-effective_from")
        .first()
    )

    if rent_record:
        return rent_record.rent_amount

    # Fallback to legacy monthly_rent (initial rent)
    return tenant.monthly_rent



class RentPayment(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name='payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Month the payment is for (ex: March 2025)
    payment_month = models.DateField()

    # Actual date the money was received
    date_paid = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        month_str = self.payment_month.strftime('%B %Y')
        return f"{self.tenant.name} - {month_str} - {self.amount}"
    
class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name
    
class Expense(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name='expenses')
    category = models.ForeignKey(ExpenseCategory, on_delete=models.SET_NULL, null=True, related_name='expenses')

    # Optional linkage for salary expenses (and any future employee-related expenses)
    employee = models.ForeignKey(
        "Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expenses",
        help_text="Set for employee-related expenses (e.g., Salary).",
    )

    # Accounting month the expense belongs to (YYYY-MM-01). For salaries, this is the salary month.
    expense_month = models.DateField(
        null=True,
        blank=True,
        help_text="Month this expense is booked to (first day of month).",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()

    # recurring: electricity, water, garbage, internet
    # one_time: repairs, maintenance
    is_recurring = models.BooleanField(default=True)

    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Description of the expense (free‑form, e.g. Electricity bill, Repair work)",
    )

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["property", "date"]),
            models.Index(fields=["property", "expense_month"]),
            models.Index(fields=["employee", "expense_month"]),
        ]
        constraints = [
            # Prevent paying the same employee twice for the same salary month.
            # This applies only when (employee, expense_month, category) are set.
            models.UniqueConstraint(
                fields=["employee", "expense_month", "category"],
                name="unique_employee_expense_per_month_per_category",
            ),
        ]

    def __str__(self):
        date_str = self.date.strftime('%Y-%m-%d')
        category = self.category.name if self.category else "Uncategorized"
        return f"{self.property.name} - {category} - {date_str} - {self.amount}"
    


class Employee(models.Model):
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)

    property = models.ForeignKey(Property, on_delete=models.PROTECT, related_name="employees")
    monthly_salary = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)
    
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.role}"

class EmployeeSalary(models.Model):
    """
    Time‑effective salary history for an employee.
    Salary changes are NOT retroactive.
    Used when paying salary to determine correct amount for a given month.
    """
    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="salary_history",
    )

    salary_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Always store as first day of month (YYYY‑MM‑01)
    effective_from = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "effective_from"],
                name="unique_salary_per_employee_per_month",
            )
        ]

    def __str__(self):
        return f"{self.employee.name} — {self.salary_amount} from {self.effective_from:%Y-%m}"