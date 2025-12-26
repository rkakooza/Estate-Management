from django.db import migrations
from datetime import date


def backfill_employee_salary(apps, schema_editor):
    Employee = apps.get_model("estate", "Employee")
    EmployeeSalary = apps.get_model("estate", "EmployeeSalary")

    for employee in Employee.objects.all():
        if employee.monthly_salary is None:
            continue

        # Normalize to first day of month (YYYY-MM-01)
        effective_from = employee.start_date.replace(day=1)

        # Avoid duplicates if migration is re-run
        exists = EmployeeSalary.objects.filter(
            employee=employee,
            effective_from=effective_from,
        ).exists()

        if not exists:
            EmployeeSalary.objects.create(
                employee=employee,
                salary_amount=employee.monthly_salary,
                effective_from=effective_from,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("estate", "0007_employeesalary"), 
    ]

    operations = [
        migrations.RunPython(backfill_employee_salary),
    ]