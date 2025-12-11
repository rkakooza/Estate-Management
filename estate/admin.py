from django.contrib import admin
from .models import (
    Property,
    Tenant,
    RentPayment,
    ExpenseCategory,
    Expense,
    Employee,
    SalaryPayment
)

@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "location", "created_at")
    search_fields = ("name", "location")

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "property", "monthly_rent", "active", "start_date")
    list_filter = ("property", "active")
    search_fields = ("name", "phone", "email")

@admin.register(RentPayment)
class RentPaymentAdmin(admin.ModelAdmin):
    list_display = ("tenant", "payment_month", "amount", "date_paid")
    list_filter = ("payment_month", "tenant__property")
    search_fields = ("tenant__name",)
    date_hierarchy = "payment_month"

@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("property", "category", "amount", "date", "is_recurring")
    list_filter = ("property", "category", "is_recurring", "date")
    search_fields = ("notes",)
    date_hierarchy = "date"
   
@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name",)

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "monthly_salary", "active", "start_date")
    list_filter = ("active", "role")
    search_fields = ("name", "role", "phone")


@admin.register(SalaryPayment)
class SalaryPaymentAdmin(admin.ModelAdmin):
    list_display = ("employee", "payment_month", "amount", "date_paid")
    list_filter = ("payment_month", "employee__role")
    search_fields = ("employee__name",)
    date_hierarchy = "payment_month"