from django.contrib import admin

from .models import (AuditLog, ClauseParagraph, ClauseVersion, EvidenceRegion,
                     QueryConclusion, Relation, SourceDocument)


@admin.register(SourceDocument)
class SourceDocumentAdmin(admin.ModelAdmin):
    list_display = ("file_no", "title", "kind", "issued_earliest",
                    "issued_latest", "deleted")
    list_filter = ("kind", "deleted")
    search_fields = ("file_no", "title")


class ParagraphInline(admin.TabularInline):
    model = ClauseParagraph
    extra = 0


@admin.register(ClauseVersion)
class ClauseVersionAdmin(admin.ModelAdmin):
    list_display = ("clause_name", "version_label", "document", "deleted")
    list_filter = ("deleted",)
    search_fields = ("clause_name", "version_label")
    inlines = [ParagraphInline]


@admin.register(Relation)
class RelationAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "from_version", "to_version",
                    "target_relation", "document", "locked", "deleted")
    list_filter = ("kind", "locked", "deleted")
    filter_horizontal = ("affected_paragraphs",)


@admin.register(EvidenceRegion)
class EvidenceRegionAdmin(admin.ModelAdmin):
    list_display = ("id", "document", "target", "label")
    search_fields = ("target", "label")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("id", "action", "entity_type", "entity_id",
                    "summary", "undone", "created_at")
    list_filter = ("action", "undone", "entity_type")


@admin.register(QueryConclusion)
class QueryConclusionAdmin(admin.ModelAdmin):
    list_display = ("id", "query_date", "clause_name", "stale",
                    "stale_reason", "updated_at")
    list_filter = ("stale",)
