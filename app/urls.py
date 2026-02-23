from django.urls import path
from . import views

app_name = "app"

urlpatterns = [
    path("amr/summary/",    views.SummaryView.as_view(),    name="amr-summary"),
    path("amr/records/",    views.RecordsView.as_view(),    name="amr-records"),
    path("amr/resistance/", views.ResistanceView.as_view(), name="amr-resistance"),
]