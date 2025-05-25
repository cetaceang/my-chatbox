from django.urls import path
from . import views

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'), # Optional
    path('api/manage-roles/', views.manage_user_role, name='manage-user-roles'),
    path('api/create-first-admin/', views.create_first_admin, name='create-first-admin'),
]
