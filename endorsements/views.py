"""HTTP 视图：单页应用入口 + JSON API（本机使用，不做登录鉴权）。"""

from __future__ import annotations

import json
from datetime import date

from django.http import (HttpResponseBadRequest, JsonResponse,
                         HttpResponseNotAllowed, Http404, FileResponse)
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie

from . import serializers, services
from .models import (AuditLog, ClauseParagraph, ClauseVersion, EvidenceRegion,
                     QueryConclusion, Relation, SourceDocument)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

DATE_FIELDS_DOC = ("issued_earliest", "issued_latest",
                   "effective_start_earliest", "effective_start_latest",
                   "effective_end_earliest", "effective_end_latest")
DATE_FIELDS_REL = ("date_earliest", "date_latest",
                   "eff_start_earliest", "eff_start_latest",
                   "eff_end_earliest", "eff_end_latest")


def _body(request) -> dict:
    if request.content_type.startswith("application/json"):
        return json.loads(request.body.decode("utf-8") or "{}")
    data = {}
    for k, v in request.POST.items():
        data[k] = v if v not in ("", "null") else None
    return data


def _dates(payload: dict, names) -> dict:
    out = {}
    for name in names:
        val = payload.get(name)
        if isinstance(val, str) and val:
            try:
                out[name] = date.fromisoformat(val)
            except ValueError:
                raise ValueError(f"日期格式应为 YYYY-MM-DD：{name}={val}")
        elif val is None or val == "":
            out[name] = None
        else:
            out[name] = val
    return out


def _err(message: str, status: int = 400, **extra) -> JsonResponse:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _ok(**extra) -> JsonResponse:
    payload = {"ok": True}
    payload.update(extra)
    return JsonResponse(payload, json_dumps_params={"ensure_ascii": False,
                                                     "default": str})


def _version_json(v: ClauseVersion) -> dict:
    d = serializers.version_dict(v)
    d["paragraphs"] = [p for p in d["paragraphs"]]
    return d


def _relation_json(r: Relation) -> dict:
    d = serializers.relation_dict(r)
    d["document_file_no"] = r.document.file_no
    d["from_label"] = str(r.from_version) if r.from_version_id else None
    d["to_label"] = str(r.to_version) if r.to_version_id else None
    d["target_locked"] = r.target_relation.locked if r.target_relation_id else False
    return d


def _region_json(g: EvidenceRegion) -> dict:
    d = serializers.region_dict(g)
    d["doc_file_no"] = g.document.file_no
    d["scan_url"] = g.document.scan.url if g.document.scan else None
    return d


def _document_json(d: SourceDocument) -> dict:
    data = serializers.document_dict(d)
    data["issued_earliest"] = data["issued_lo"].isoformat() \
        if data["issued_lo"] else None
    data["issued_latest"] = data["issued_hi"].isoformat() \
        if data["issued_hi"] else None
    for short, field in (("start", "effective_start"), ("end", "effective_end")):
        data[f"{field}_earliest"] = data[f"{short}_lo"].isoformat() \
            if data[f"{short}_lo"] else None
        data[f"{field}_latest"] = data[f"{short}_hi"].isoformat() \
        if data[f"{short}_hi"] else None
    return data


# --------------------------------------------------------------------------- #
# 页面
# --------------------------------------------------------------------------- #

@ensure_csrf_cookie
def index(request):
    return render(request, "endorsements/index.html", {})


# --------------------------------------------------------------------------- #
# 总览
# --------------------------------------------------------------------------- #

@require_http_methods(["GET"])
def overview(request):
    issues = serializers.run_checks()
    return _ok(
        documents=SourceDocument.objects.count(),
        versions=ClauseVersion.objects.count(),
        relations=Relation.objects.count(),
        regions=EvidenceRegion.objects.count(),
        issue_count=len(issues),
        error_count=sum(1 for i in issues if i["level"] == "error"),
        warning_count=sum(1 for i in issues if i["level"] == "warning"),
        recent_history=[_audit_json(a) for a in AuditLog.objects.all()[:10]],
    )


# --------------------------------------------------------------------------- #
# 材料
# --------------------------------------------------------------------------- #

@require_http_methods(["GET", "POST"])
def documents(request):
    if request.method == "GET":
        return _ok(items=[_document_json(d)
                          for d in SourceDocument.objects.all()])

    payload = _body(request)
    file_no = (payload.get("file_no") or "").strip()
    if not file_no:
        return _err("文件编号不能为空")
    if SourceDocument.all_objects.filter(file_no=file_no).exists():
        return _err(f"文件编号 {file_no} 已存在（含已撤销录入）")
    fields = {"file_no": file_no,
              "title": (payload.get("title") or "").strip(),
              "kind": payload.get("kind") or "other",
              "note": payload.get("note") or ""}
    try:
        fields.update(_dates(payload, DATE_FIELDS_DOC))
    except ValueError as e:
        return _err(str(e))
    scan = request.FILES.get("scan") if hasattr(request, "FILES") else None
    if scan:
        fields["scan"] = scan
    doc = services.create_with_audit("document", **fields)
    return _ok(document=_document_json(doc))


@require_http_methods(["GET", "PATCH", "DELETE"])
def document_detail(request, pk):
    doc = SourceDocument.all_objects.filter(pk=pk).first()
    if doc is None:
        raise Http404("材料不存在")

    if request.method == "GET":
        data = _document_json(doc)
        data["deleted"] = doc.deleted
        data["versions"] = [_version_json(v) for v in
                            ClauseVersion.all_objects.filter(document=doc)
                            .select_related("document")
                            .prefetch_related("paragraphs")]
        data["relations"] = [_relation_json(r) for r in
                             Relation.all_objects.filter(document=doc)]
        data["regions"] = [_region_json(g) for g in doc.regions.all()]
        return _ok(**data)

    if request.method == "DELETE":
        names = list(ClauseVersion.all_objects.filter(document=doc)
                     .values_list("clause_name", flat=True).distinct())
        services.soft_delete_with_audit("document", doc)
        QueryConclusion.objects.filter(stale=False).update(
            stale=True,
            stale_reason=f"材料 {doc.file_no} 被撤销录入，结论需重新生成")
        return _ok()

    payload = _body(request)
    fields = {}
    for k in ("title", "kind", "note"):
        if k in payload:
            fields[k] = payload[k]
    if "file_no" in payload:
        fields["file_no"] = payload["file_no"].strip()
    try:
        fields.update(_dates({k: v for k, v in payload.items()
                              if k in DATE_FIELDS_DOC}, DATE_FIELDS_DOC))
    except ValueError as e:
        return _err(str(e))
    scan = request.FILES.get("scan") if hasattr(request, "FILES") else None
    if scan:
        fields["scan"] = scan
    doc = services.update_with_audit("document", doc, fields)
    return _ok(document=_document_json(doc))


@require_http_methods(["POST"])
def document_regions(request, pk):
    doc = SourceDocument.objects.filter(pk=pk).first()
    if doc is None:
        raise Http404("材料不存在")
    payload = _body(request)
    try:
        box = {k: float(payload.get(k)) for k in ("x", "y", "w", "h")}
    except (TypeError, ValueError):
        return _err("框选坐标 x/y/w/h 必须为 0~1 的数字")
    if not all(0.0 <= v <= 1.0 for v in box.values()) or box["w"] <= 0 \
            or box["h"] <= 0:
        return _err("框选坐标必须落在 0~1 之间且宽高为正")
    target = (payload.get("target") or "").strip()
    if not target:
        return _err("必须指定框选依据挂载的目标（document/version/relation…）")
    region = EvidenceRegion.objects.create(
        document=doc, target=target, label=payload.get("label") or "", **box)
    services.log_action("create", "region", region.pk,
                        f"录入框选 #{region.pk}（{target}）", None)
    return _ok(region=_region_json(region))


# --------------------------------------------------------------------------- #
# 条款版本 / 段落
# --------------------------------------------------------------------------- #

@require_http_methods(["GET", "POST"])
def versions(request):
    if request.method == "GET":
        qs = ClauseVersion.objects.select_related("document") \
            .prefetch_related("paragraphs")
        return _ok(items=[_version_json(v) for v in qs])

    payload = _body(request)
    name = (payload.get("clause_name") or "").strip()
    doc_id = payload.get("document_id")
    if not name or not doc_id:
        return _err("条款名称与来源材料必填")
    doc = SourceDocument.objects.filter(pk=doc_id).first()
    if doc is None:
        return _err("来源材料不存在或已被撤销")
    v = services.create_with_audit(
        "version", clause_name=name,
        version_label=(payload.get("version_label") or "").strip(),
        document=doc, note=payload.get("note") or "")
    return _ok(version=_version_json(ClauseVersion.objects.get(pk=v.pk)))


@require_http_methods(["GET", "PATCH", "DELETE"])
def version_detail(request, pk):
    v = ClauseVersion.all_objects.select_related("document").filter(pk=pk).first()
    if v is None:
        raise Http404("条款版本不存在")
    if request.method == "GET":
        data = _version_json(v)
        data["deleted"] = v.deleted
        data["regions"] = [_region_json(g)
                           for g in EvidenceRegion.objects
                           .filter(target__startswith=f"version:{pk}")]
        return _ok(**data)
    if request.method == "DELETE":
        clause = v.clause_name
        services.soft_delete_with_audit("version", v)
        QueryConclusion.objects.filter(stale=False).update(
            stale=True,
            stale_reason=f"版本「{clause}」被撤销录入，结论需重新生成")
        return _ok()
    payload = _body(request)
    fields = {}
    for k in ("clause_name", "version_label", "note"):
        if k in payload:
            fields[k] = payload[k]
    v = services.update_with_audit("version", v, fields)
    return _ok(version=_version_json(v))


@require_http_methods(["POST"])
def paragraphs(request):
    payload = _body(request)
    vid = payload.get("version_id")
    no = (payload.get("paragraph_no") or "").strip()
    if not vid or not no:
        return _err("版本与段落号必填")
    v = ClauseVersion.objects.filter(pk=vid).first()
    if v is None:
        return _err("条款版本不存在或已被撤销")
    if v.paragraphs.filter(paragraph_no=no).exists():
        return _err(f"段落号 {no} 在该版本中已存在")
    p = services.create_with_audit(
        "paragraph", version=v, paragraph_no=no,
        title=(payload.get("title") or "").strip(),
        text=payload.get("text") or "",
        order=int(payload.get("order") or 0))
    QueryConclusion.objects.filter(stale=False).update(
        stale=True, stale_reason=f"版本「{v.clause_name}」段落发生增改，结论需重新生成")
    return _ok(paragraph=serializers.paragraph_dict(p))


@require_http_methods(["PATCH", "DELETE"])
def paragraph_detail(request, pk):
    p = ClauseParagraph.objects.filter(pk=pk).first()
    if p is None:
        raise Http404("段落不存在")
    if request.method == "DELETE":
        pid = p.pk
        clause = p.version.clause_name
        p.delete()
        services.log_action("delete", "paragraph", pid,
                            f"删除段落 #{pid}", None)
        QueryConclusion.objects.filter(stale=False).update(
            stale=True, stale_reason=f"版本「{clause}」段落被删除，结论需重新生成")
        return _ok()
    payload = _body(request)
    before = services._dump(p)
    for k in ("paragraph_no", "title", "text"):
        if k in payload:
            setattr(p, k, payload[k])
    if "order" in payload:
        p.order = int(payload["order"])
    p.save()
    services.log_action("update", "paragraph", p.pk,
                        f"修改段落 #{p.pk}", before)
    QueryConclusion.objects.filter(stale=False).update(
        stale=True, stale_reason=f"版本「{p.version.clause_name}」段落修改，结论需重新生成")
    return _ok(paragraph=serializers.paragraph_dict(p))


# --------------------------------------------------------------------------- #
# 沿革关系
# --------------------------------------------------------------------------- #

@require_http_methods(["GET", "POST"])
def relations(request):
    if request.method == "GET":
        qs = Relation.objects.select_related(
            "document", "from_version", "to_version", "target_relation")
        return _ok(items=[_relation_json(r) for r in qs])

    payload = _body(request)
    kind = payload.get("kind")
    doc_id = payload.get("document_id")
    if kind not in dict(Relation.Kind.choices):
        return _err("关系类型不合法")
    doc = SourceDocument.objects.filter(pk=doc_id).first()
    if doc is None:
        return _err("依据材料不存在或已被撤销")

    def _vid(key):
        val = payload.get(key)
        return int(val) if val else None

    try:
        r = Relation(document=doc, kind=kind,
                     from_version_id=_vid("from_version_id"),
                     to_version_id=_vid("to_version_id"),
                     target_relation_id=_vid("target_relation_id"),
                     description=(payload.get("description") or "").strip())
        for k, v in _dates(payload, DATE_FIELDS_REL).items():
            setattr(r, k, v)
        r.full_clean()
        r.save()
    except ValueError as e:
        return _err(str(e))
    except Exception as e:  # django.core.exceptions.ValidationError 等
        msg = getattr(e, "message_dict", None)
        if msg:
            parts = []
            for v in msg.values():
                parts.extend(v if isinstance(v, (list, tuple)) else [str(v)])
            return _err("；".join(parts))
        return _err(str(e))
    ids = payload.get("affected_paragraph_ids") or []
    if ids:
        r.affected_paragraphs.set(ClauseParagraph.objects.filter(
            id__in=ids, version_id=r.from_version_id))
    services.log_action("create", "relation", r.pk,
                        f"录入关系 #{r.pk}（{r.get_kind_display()}）", None)
    return _ok(relation=_relation_json(Relation.objects.select_related(
        "document", "from_version", "to_version",
        "target_relation").get(pk=r.pk)))


@require_http_methods(["GET", "PATCH", "DELETE"])
def relation_detail(request, pk):
    r = Relation.all_objects.select_related(
        "document", "from_version", "to_version",
        "target_relation").filter(pk=pk).first()
    if r is None:
        raise Http404("关系不存在")
    if request.method == "GET":
        data = _relation_json(r)
        data["deleted"] = r.deleted
        data["affected_paragraphs"] = [
            {"id": p.id, "paragraph_no": p.paragraph_no, "title": p.title}
            for p in r.affected_paragraphs.all()]
        data["regions"] = [_region_json(g)
                           for g in EvidenceRegion.objects
                           .filter(target__startswith=f"relation:{pk}")]
        return _ok(**data)
    if request.method == "DELETE":
        services.soft_delete_with_audit("relation", r)
        return _ok()

    payload = _body(request)
    fields = {}
    for k in ("kind", "description"):
        if k in payload:
            fields[k] = payload[k]
    for key in ("from_version_id", "to_version_id", "target_relation_id"):
        if key in payload:
            val = payload[key]
            fields[key] = int(val) if val else None
    fields.update(_dates({k: v for k, v in payload.items()
                          if k in DATE_FIELDS_REL}, DATE_FIELDS_REL))
    try:
        r = services.update_with_audit("relation", r, fields,
                                       relation_changed=True)
        if "affected_paragraph_ids" in payload:
            ids = payload["affected_paragraph_ids"] or []
            r.affected_paragraphs.set(ClauseParagraph.objects.filter(
                id__in=ids, version_id=r.from_version_id))
            services.invalidate_downstream(r)
    except services.LockedError as e:
        return _err(str(e), status=409)
    except ValueError as e:
        return _err(str(e))
    return _ok(relation=_relation_json(Relation.objects.select_related(
        "document", "from_version", "to_version",
        "target_relation").get(pk=r.pk)))


@require_http_methods(["POST"])
def relation_lock(request, pk):
    r = Relation.objects.filter(pk=pk).first()
    if r is None:
        raise Http404("关系不存在")
    locked = bool(_body(request).get("locked", True))
    services.set_relation_locked(r, locked)
    return _ok(relation={"id": r.id, "locked": r.locked})


@require_http_methods(["PATCH", "DELETE"])
def region_detail(request, pk):
    g = EvidenceRegion.objects.filter(pk=pk).first()
    if g is None:
        raise Http404("框选不存在")
    if request.method == "DELETE":
        services.hard_delete_region_with_audit(g)
        return _ok()
    payload = _body(request)
    before = services._dump(g)
    for k in ("x", "y", "w", "h"):
        if k in payload:
            setattr(g, k, float(payload[k]))
    for k in ("target", "label"):
        if k in payload:
            setattr(g, k, payload[k])
    g.save()
    services.log_action("update", "region", g.pk,
                        f"修改框选 #{g.pk}", before)
    return _ok(region=_region_json(g))


# --------------------------------------------------------------------------- #
# 核对：检查 / 沿革 / 结论缓存 / 导出
# --------------------------------------------------------------------------- #

@require_http_methods(["GET"])
def checks(request):
    return _ok(issues=serializers.run_checks())


@require_http_methods(["GET"])
def analyze(request):
    raw = request.GET.get("date") or date.today().isoformat()
    try:
        qd = date.fromisoformat(raw)
    except ValueError:
        return _err("日期格式应为 YYYY-MM-DD")
    payload = serializers.analyze(qd)
    refresh = request.GET.get("refresh") == "1"
    use_cache = request.GET.get("cache") != "0"
    cached = QueryConclusion.objects.filter(
        query_date=qd, clause_name="").first()
    if use_cache and cached is not None and not refresh and not cached.stale:
        return _ok(payload=cached.payload, cached=True, stale=False)
    concl = services.save_conclusion(qd, payload)
    return _ok(payload=concl.payload, cached=False, stale=False,
               conclusion_id=concl.id)


@require_http_methods(["GET"])
def conclusions(request):
    return _ok(items=[{
        "id": c.id,
        "query_date": c.query_date.isoformat(),
        "stale": c.stale,
        "stale_reason": c.stale_reason,
        "created_at": c.created_at.isoformat(),
        "clause_count": len(c.payload.get("clauses", [])),
    } for c in QueryConclusion.objects.all()[:50]])


@require_http_methods(["POST"])
def refresh_conclusion(request, pk):
    c = QueryConclusion.objects.filter(pk=pk).first()
    if c is None:
        raise Http404("结论不存在")
    payload = serializers.analyze(c.query_date)
    c = services.save_conclusion(c.query_date, payload)
    return _ok(conclusion_id=c.id, stale=c.stale)


@require_http_methods(["GET"])
def export_handoff(request):
    """导出含来源、适用候选和缺口的 JSON 交接件（不判断理赔结果）。"""
    raw = request.GET.get("date") or date.today().isoformat()
    try:
        qd = date.fromisoformat(raw)
    except ValueError:
        return _err("日期格式应为 YYYY-MM-DD")
    payload = serializers.analyze(qd)
    issues = payload["issues"]
    export = {
        "export_type": "old_policy_endorsement_handoff",
        "schema_version": 1,
        "generated_at": _now_iso(),
        "query_date": qd.isoformat(),
        "disclaimer": "本交接件仅整理条款沿革、适用候选与资料缺口，"
                      "不构成理赔结论，不判断理赔结果。",
        "sources": [_export_source(d)
                    for d in SourceDocument.objects.all()],
        "clauses": [{
            "clause_name": c["clause_name"],
            "status": c["status"],
            "applicable_candidates": [
                _export_candidate(cand) for cand in c["candidates"]
            ],
        } for c in payload["clauses"]],
        "gaps": payload["gaps"],
        "issues": issues,
    }
    resp = JsonResponse(export, json_dumps_params={
        "ensure_ascii": False, "indent": 2, "default": str})
    resp["Content-Disposition"] = \
        f'attachment; filename="handoff_{qd.isoformat()}.json"'
    return resp


def _export_source(doc) -> dict:
    return {
        "id": doc.id,
        "file_no": doc.file_no,
        "title": doc.title,
        "kind": doc.get_kind_display(),
        "issued_range": [doc.issued_earliest.isoformat()
                         if doc.issued_earliest else None,
                         doc.issued_latest.isoformat()
                         if doc.issued_latest else None],
        "effective_range": {
            "start": [doc.effective_start_earliest.isoformat()
                      if doc.effective_start_earliest else None,
                      doc.effective_start_latest.isoformat()
                      if doc.effective_start_latest else None],
            "end": [doc.effective_end_earliest.isoformat()
                    if doc.effective_end_earliest else None,
                    doc.effective_end_latest.isoformat()
                    if doc.effective_end_latest else None],
        },
        "scan_local_path": doc.scan.name if doc.scan else None,
        "versions": [{
            "id": v.id,
            "clause_name": v.clause_name,
            "version_label": v.version_label,
            "paragraphs": [{"no": p.paragraph_no, "title": p.title,
                            "text": p.text} for p in v.paragraphs.all()],
        } for v in doc.clause_versions.all()],
        "relations": [{
            "id": r.id, "kind": r.get_kind_display(),
            "from_version_id": r.from_version_id,
            "to_version_id": r.to_version_id,
            "target_relation_id": r.target_relation_id,
            "affected_paragraph_ids": list(
                r.affected_paragraphs.values_list("id", flat=True)),
            "date_range": [r.date_earliest.isoformat()
                           if r.date_earliest else None,
                           r.date_latest.isoformat()
                           if r.date_latest else None],
            "description": r.description,
            "locked": r.locked,
        } for r in doc.relations.all()],
    }


def _export_candidate(cand: dict) -> dict:
    return {
        "certainty": cand["certainty"],
        "status": cand["status"],
        "chain": [{"version_id": n["version_id"],
                   "label": n["version_label"] or n["doc_file_no"],
                   "doc_file_no": n["doc_file_no"],
                   "status": n["status"],
                   "revoked": n["revoked"],
                   "via_relation_id": n["via_relation_id"],
                   "via_certainty": n["via_certainty"],
                   "supplements": n["supplements_here"]}
                  for n in cand["chain"]],
        "effective_paragraphs": cand["paragraphs_effective"],
        "paragraph_conflicts": cand["paragraph_conflicts"],
        "evidence": cand["evidence"],
        "note": cand["note"],
        "gap": cand["status"] == "revoked_gap",
    }


# --------------------------------------------------------------------------- #
# 撤销误录 / 留痕
# --------------------------------------------------------------------------- #

def _audit_json(a: AuditLog) -> dict:
    return {"id": a.id, "action": a.action,
            "action_text": a.get_action_display(),
            "entity_type": a.entity_type, "entity_id": a.entity_id,
            "summary": a.summary, "undone": a.undone,
            "created_at": a.created_at.isoformat(timespec="seconds")}


@require_http_methods(["GET"])
def history(request):
    return _ok(items=[_audit_json(a) for a in AuditLog.objects.all()[:100]])


@require_http_methods(["POST"])
def undo(request):
    try:
        entry = services.undo_last()
    except LookupError as e:
        return _err(str(e), status=404)
    except services.LockedError as e:
        return _err(str(e), status=409)
    return _ok(undone=_audit_json(entry))


def _now_iso() -> str:
    from django.utils import timezone
    return timezone.now().isoformat(timespec="seconds")
