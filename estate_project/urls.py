"""
URL configuration for estate_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from estate import views
from estate.admin import admin_site
from estate.views import ForcePasswordChangeView
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect

urlpatterns = [
    path("", lambda request: redirect("login"), name="root"),
    path("login/", views.login_view, name="login"),
    path('admin/', admin.site.urls),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analytics/', views.analytics_view, name='analytics'),
    path('payments/', views.payments_view, name='payments'),
    path("payments/add/", views.add_payment, name="add_payment"),
    path("expenses/add/", views.add_expense, name="add_expense"),
    path("employees/add/", views.add_employee, name="add_employee"),
    path("employees/<int:employee_id>/pay/", views.pay_salary, name="pay_salary"),
    path("employees/<int:employee_id>/salary/change/", views.change_salary, name="change_salary"),
    path("expenses/", views.expenses_ledger, name="expenses_ledger"),
    path("expenses/employees/", views.employees_list, name="employees_list"),
    path("expenses/employees/<int:employee_id>/toggle/", views.toggle_employee_active, name="toggle_employee_active"),
    path("payments/history/", views.payments_history, name="payments_history"),
    path("payments/history/csv/", views.payments_history_csv, name="payments_history_csv"),
    path('tenants/', views.tenants_view, name='tenants'),
    path('tenants/<int:tenant_id>/', views.tenant_details, name='tenant_details'),
    path("tenants/add/", views.add_tenant, name="add_tenant"),
    path('tenants/<int:tenant_id>/toggle-active/', views.toggle_tenant_active, name='toggle_tenant_active'),
    path("tenants/<int:tenant_id>/edit/", views.edit_tenant, name="edit_tenant"),
    path("logout/", LogoutView.as_view(next_page="/login/"), name="logout"),
    path("forgot-password/", views.forgot_password_view, name="forgot_password"),
]


urlpatterns += [
    path(
        "password/change/",
        ForcePasswordChangeView.as_view(),
        name="password_change",
    ),
    path(
        "password/change/done/",
        ForcePasswordChangeView.as_view(),
        name="password_change_done",
    ),
]