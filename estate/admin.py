from django.contrib import admin
from django.core.exceptions import PermissionDenied
from .models import (
    Property,
    Tenant,
    RentPayment,
    ExpenseCategory,
    Expense,
    Employee,
    TenantRent,
    CommissionRate,
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
    list_display_links = ("tenant", "payment_month") 
    list_filter = ("payment_month", "tenant__property")
    search_fields = ("tenant__name",)
    readonly_fields = ("created_at",)
    ordering = ("-date_paid", "-created_at")
    date_hierarchy = "payment_month"

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

@admin.register(TenantRent)
class TenantRentAdmin(admin.ModelAdmin):
    list_display = ("tenant", "effective_from", "rent_amount")
    list_display_links = ("tenant", "effective_from")
    list_filter = ("effective_from", "tenant__property")
    search_fields = ("tenant__name",)
    ordering = ("-effective_from", "tenant")
    date_hierarchy = "effective_from"

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

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



@admin.register(CommissionRate)
class CommissionRateAdmin(admin.ModelAdmin):
    list_display = ("percentage", "effective_from")
    ordering = ("-effective_from",)