from django.urls import path
from . import views

app_name = "app"

urlpatterns = [
    path("amr/summary/",    views.SummaryView.as_view(),    name="amr-summary"),
    path("amr/records/",    views.RecordsView.as_view(),    name="amr-records"),
    path("amr/resistance/", views.ResistanceView.as_view(), name="amr-resistance"),
    # 
    path("amr/facilities/", views.FacilitiesView.as_view(), name="amr-facilities"),
    path("amr/pathogens/",  views.PathogensView.as_view(),  name="amr-pathogens"),
    path("amr/trend/",      views.TrendView.as_view(),      name="amr-trend"),
    path("amr/detail/",     views.DetailView.as_view(),     name="amr-detail"),
]