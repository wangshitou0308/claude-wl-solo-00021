from django.urls import path

from . import views

app_name = "endorsements"

urlpatterns = [
    path("", views.index, name="index"),

    # 录入 / 列表
    path("api/overview/", views.overview, name="overview"),
    path("api/documents/", views.documents, name="documents"),
    path("api/documents/<int:pk>/", views.document_detail, name="document_detail"),
    path("api/documents/<int:pk>/regions/", views.document_regions, name="document_regions"),
    path("api/versions/", views.versions, name="versions"),
    path("api/versions/<int:pk>/", views.version_detail, name="version_detail"),
    path("api/paragraphs/", views.paragraphs, name="paragraphs"),
    path("api/paragraphs/<int:pk>/", views.paragraph_detail, name="paragraph_detail"),
    path("api/relations/", views.relations, name="relations"),
    path("api/relations/<int:pk>/", views.relation_detail, name="relation_detail"),
    path("api/relations/<int:pk>/lock/", views.relation_lock, name="relation_lock"),
    path("api/regions/<int:pk>/", views.region_detail, name="region_detail"),

    # 核对
    path("api/checks/", views.checks, name="checks"),
    path("api/analyze/", views.analyze, name="analyze"),
    path("api/conclusions/", views.conclusions, name="conclusions"),
    path("api/conclusions/<int:pk>/refresh/", views.refresh_conclusion,
         name="refresh_conclusion"),
    path("api/export/", views.export_handoff, name="export_handoff"),

    # 撤销误录 / 留痕
    path("api/undo/", views.undo, name="undo"),
    path("api/history/", views.history, name="history"),
]
