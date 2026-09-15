"""ORM 对象 → 引擎使用的纯字典数据。"""

from __future__ import annotations

from . import engine
from .models import ClauseParagraph, ClauseVersion, EvidenceRegion, Relation, SourceDocument


def document_dict(d: SourceDocument) -> dict:
    return {
        "id": d.id,
        "file_no": d.file_no,
        "title": d.title,
        "kind": d.kind,
        # 前端统一读 scan；scan_url 保留给交接件/内部使用
        "scan": d.scan.url if d.scan else None,
        "scan_url": d.scan.url if d.scan else None,
        "scan_name": d.scan.name if d.scan else None,
        "issued_lo": d.issued_earliest,
        "issued_hi": d.issued_latest,
        "start_lo": d.effective_start_earliest,
        "start_hi": d.effective_start_latest,
        "end_lo": d.effective_end_earliest,
        "end_hi": d.effective_end_latest,
        "note": d.note,
        "deleted": d.deleted,
    }


def paragraph_dict(p: ClauseParagraph) -> dict:
    return {
        "id": p.id,
        "paragraph_no": p.paragraph_no,
        "title": p.title,
        "text": p.text,
        "order": p.order,
        "version_id": p.version_id,
        "deleted": p.deleted,
    }


def version_dict(v: ClauseVersion) -> dict:
    d = v.document
    return {
        "id": v.id,
        "clause_name": v.clause_name,
        "version_label": v.version_label,
        "document_id": d.id,
        "doc_file_no": d.file_no,
        # 版本生效区间跟随其来源材料
        "start_lo": d.effective_start_earliest,
        "start_hi": d.effective_start_latest,
        "end_lo": d.effective_end_earliest,
        "end_hi": d.effective_end_latest,
        "paragraphs": [paragraph_dict(p) for p in v.paragraphs.all()],
        "paragraph_ids": [p.id for p in v.paragraphs.all()],
        "note": v.note,
        "deleted": v.deleted,
    }


def relation_dict(r: Relation) -> dict:
    return {
        "id": r.id,
        "kind": r.kind,
        "document_id": r.document_id,
        "from_id": r.from_version_id,
        "to_id": r.to_version_id,
        "target_relation_id": r.target_relation_id,
        "affected_paragraph_ids": list(
            r.affected_paragraphs.values_list("id", flat=True)),
        "description": r.description,
        "date_lo": r.date_earliest,
        "date_hi": r.date_latest,
        "eff_start_lo": r.eff_start_earliest,
        "eff_start_hi": r.eff_start_latest,
        "eff_end_lo": r.eff_end_earliest,
        "eff_end_hi": r.eff_end_latest,
        "locked": r.locked,
        "deleted": r.deleted,
    }


def region_dict(g: EvidenceRegion) -> dict:
    return {
        "id": g.id,
        "document_id": g.document_id,
        "target": g.target,
        "x": g.x, "y": g.y, "w": g.w, "h": g.h,
        "label": g.label,
    }


def build_dataset() -> dict:
    docs = SourceDocument.all_objects.all()
    doc_ids = set(docs.values_list("id", flat=True))
    versions_qs = ClauseVersion.all_objects.filter(document_id__in=doc_ids)
    version_ids = set(versions_qs.values_list("id", flat=True))
    return {
        "documents": [document_dict(d) for d in docs],
        "versions": [version_dict(v) for v in
                     versions_qs.select_related("document")
                     .prefetch_related("paragraphs")],
        "relations": [relation_dict(r) for r in
                      Relation.all_objects.filter(document_id__in=doc_ids)],
        "regions": [region_dict(g) for g in
                    EvidenceRegion.objects.filter(document_id__in=doc_ids)],
    }


def run_checks() -> list[dict]:
    return engine.run_checks(build_dataset())


def analyze(query_date) -> dict:
    return engine.analyze(build_dataset(), query_date)
