from django.urls import path
from . import views

app_name = 'growers'

urlpatterns = [
    path('', views.grower_list, name='grower_list'),
    path('<str:grower_number>/', views.grower_detail, name='grower_detail'),
] 