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
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analytics/', views.analytics_view, name='analytics'),
    path('payments/', views.payments_view, name='payments'),
    path("payments/add/", views.add_payment, name="add_payment"),
    path('tenants/', views.tenants_view, name='tenants'),
    path('settings/', views.settings_view, name='settings'),
    path("logout/", auth_views.LogoutView.as_view(next_page="/admin/login/", http_method_names=["get"]),
    name="logout"),
]
