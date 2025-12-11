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
    
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()

    # recurring: electricity, water, garbage, internet
    # one_time: repairs, maintenance
    is_recurring = models.BooleanField(default=True)

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        date_str = self.date.strftime('%Y-%m-%d')
        return f"{self.property.name} - {self.category.name} - {date_str} - {self.amount}"
    

class Employee(models.Model):
    name = models.CharField(max_length=100)
    role = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=20, blank=True)

    monthly_salary = models.DecimalField(max_digits=10, decimal_places=2)
    active = models.BooleanField(default=True)

    start_date = models.DateField()
    end_date = models.DateField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.role}"
    

class SalaryPayment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='salary_payments')
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Month this salary was for
    payment_month = models.DateField()

    # Actual date the payment was made
    date_paid = models.DateField()

    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        month_str = self.payment_month.strftime('%B %Y')
        return f"{self.employee.name} - {month_str} - {self.amount}"