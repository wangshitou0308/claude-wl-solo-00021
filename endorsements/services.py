"""
业务服务：操作留痕、撤销误录、结论缓存与下游失效。

要点：
* 对材料 / 版本 / 段落 / 关系 / 框选的增改删统一写 AuditLog；
* "撤销误录"按后进先出弹出最近一条未撤销记录并执行逆操作（撤销本身不入栈）；
* 关系修改后只使该关系沿"被修改版本→下游"可达条款的已存结论过期；
* 已锁定的关系禁止修改/删除/撤销（须先解锁）。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict

from django.db import transaction

from . import engine, serializers
from .models import (AuditLog, ClauseParagraph, ClauseVersion, EvidenceRegion,
                     QueryConclusion, Relation, SourceDocument)

ENTITY_MODELS = {
    "document": SourceDocument,
    "version": ClauseVersion,
    "paragraph": ClauseParagraph,
    "relation": Relation,
    "region": EvidenceRegion,
}


class LockedError(Exception):
    """关系已锁定，需先解锁。"""


# --------------------------------------------------------------------------- #
# 审计留痕
# --------------------------------------------------------------------------- #

def log_action(action, entity_type, entity_id, summary, snapshot) -> AuditLog:
    return AuditLog.objects.create(
        action=action, entity_type=entity_type, entity_id=entity_id,
        summary=summary, snapshot=snapshot)


def _dump(obj) -> dict:
    data = {}
    for f in obj._meta.concrete_fields:
        val = f.value_from_object(obj)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        elif f.name == "scan":
            # 无文件时 value_from_object 给出空 ImageFieldFile，
            # 必须转成 None/文件名，否则 JSON 快照无法序列化
            val = obj.scan.name or None
        data[f.name] = val
    if isinstance(obj, Relation):
        data["affected_paragraph_ids"] = list(
            obj.affected_paragraphs.values_list("id", flat=True))
    if isinstance(obj, ClauseParagraph):
        # 段落删除时记下它挂在哪些关系上，撤销恢复时重新挂回
        data["relation_ids"] = list(
            obj.relations.values_list("id", flat=True))
    return data


# --------------------------------------------------------------------------- #
# 写操作包装（每个写操作先记快照，便于 LIFO 撤销）
# --------------------------------------------------------------------------- #

def create_with_audit(entity_type: str, **fields):
    model = ENTITY_MODELS[entity_type]
    m2m = fields.pop("affected_paragraph_ids", None)
    obj = model.all_objects.create(**fields) if hasattr(model, "all_objects") \
        else model.objects.create(**fields)
    if m2m is not None and entity_type == "relation":
        obj.affected_paragraphs.set(m2m)
    log_action("create", entity_type, obj.pk, f"录入{entity_type} #{obj.pk}", None)
    return obj


def _guard_relation_lock(obj: Relation):
    if isinstance(obj, Relation) and obj.locked:
        raise LockedError(f"关系 #{obj.pk} 已确认锁定，请先解锁后再修改")


def update_with_audit(entity_type: str, obj, fields: dict, *,
                      relation_changed: bool = False):
    _guard_relation_lock(obj)
    before = _dump(obj)
    m2m = fields.pop("affected_paragraph_ids", None)
    for k, v in fields.items():
        setattr(obj, k, v)
    obj.save()
    if m2m is not None and entity_type == "relation":
        _guard_relation_lock(obj)
        obj.affected_paragraphs.set(m2m)
    log_action("update", entity_type, obj.pk,
               f"修改{entity_type} #{obj.pk}", before)
    if entity_type == "relation" or relation_changed:
        invalidate_downstream(obj)
    return obj


def soft_delete_with_audit(entity_type: str, obj):
    _guard_relation_lock(obj)
    before = _dump(obj)
    if isinstance(obj, ClauseParagraph):
        # 从局部替换关系上摘下，避免软删后被悬空检查报错；
        # 关系 id 已记入快照，撤销删除时会重新挂回
        obj.relations.clear()
    obj.deleted = True
    fields = ["deleted"] + (["updated_at"] if hasattr(obj, "updated_at") else [])
    obj.save(update_fields=fields)
    log_action("delete", entity_type, obj.pk,
               f"删除{entity_type} #{obj.pk}", before)
    if entity_type == "relation":
        invalidate_downstream(obj, deleting=True)


def hard_delete_region_with_audit(region: EvidenceRegion):
    before = _dump(region)
    rid = region.pk
    region.delete()
    log_action("delete", "region", rid, f"删除框选 #{rid}", before)


def set_relation_locked(relation: Relation, locked: bool):
    relation.locked = locked
    relation.save(update_fields=["locked", "updated_at"])
    log_action("lock" if locked else "unlock", "relation", relation.pk,
               ("锁定" if locked else "解锁") + f"关系 #{relation.pk}", None)


# --------------------------------------------------------------------------- #
# 撤销误录（后进先出；只撤销最近一条未撤销记录）
# --------------------------------------------------------------------------- #

@transaction.atomic
def undo_last() -> AuditLog:
    entry = AuditLog.objects.filter(undone=False).order_by("-id").first()
    if entry is None:
        raise LookupError("暂无可撤销的操作")
    model = ENTITY_MODELS.get(entry.entity_type)
    if model is None:
        raise LookupError(f"不支持撤销的实体类型：{entry.entity_type}")

    if entry.action == "create":
        obj = model.all_objects.filter(pk=entry.entity_id).first()
        if obj is None:
            raise LookupError("待撤销的对象已不存在")
        _guard_relation_lock(obj)
        if entry.entity_type == "region":
            # 框选是硬删除撤销的逆操作；创建本身只需要把框选删掉
            obj.delete()
        else:
            obj.deleted = True
            fields = ["deleted"] + (["updated_at"]
                                    if hasattr(obj, "updated_at") else [])
            obj.save(update_fields=fields)
        if entry.entity_type == "relation":
            invalidate_downstream(obj, deleting=True)

    elif entry.action == "delete":
        if entry.entity_type == "region":
            # 框选原本是硬删除，按快照重建
            obj = EvidenceRegion(pk=entry.entity_id, **{
                k: v for k, v in (entry.snapshot or {}).items()
                if k in {f.name for f in EvidenceRegion._meta.concrete_fields
                         if f.name not in ("id",)}})
            obj.save(force_insert=True)
        else:
            obj = model.all_objects.filter(pk=entry.entity_id).first()
            if obj is None:
                raise LookupError("待恢复的对象已不存在")
            obj.deleted = False
            fields = ["deleted"] + (["updated_at"]
                                    if hasattr(obj, "updated_at") else [])
            obj.save(update_fields=fields)
            if entry.entity_type == "paragraph":
                # 恢复软删时从关系上摘下的段落引用
                for rid in (entry.snapshot or {}).get("relation_ids") or []:
                    r = Relation.all_objects.filter(pk=rid).first()
                    if r is not None:
                        r.affected_paragraphs.add(obj)
        if entry.entity_type == "relation":
            invalidate_downstream(obj)

    elif entry.action == "update":
        if entry.entity_type == "region":
            obj = EvidenceRegion.objects.filter(
                pk=entry.entity_id).first()
            if obj is None and entry.snapshot:
                # 极端情况下框选已被物理删除，按旧快照重建
                obj = EvidenceRegion(pk=entry.entity_id, **{
                    k: v for k, v in entry.snapshot.items()
                    if k in {f.name for f in EvidenceRegion._meta.concrete_fields
                             if f.name not in ("id",)}})
                obj.save(force_insert=True)
            if obj is None:
                raise LookupError("待回滚的框选已不存在")
        else:
            obj = model.all_objects.filter(pk=entry.entity_id).first()
            if obj is None:
                raise LookupError("待回滚的对象已不存在")
        _guard_relation_lock(obj)
        _restore_snapshot(obj, entry.snapshot)
        if entry.entity_type == "relation":
            invalidate_downstream(obj)

    elif entry.action in ("lock", "unlock"):
        obj = model.all_objects.filter(pk=entry.entity_id).first()
        if obj is not None:
            obj.locked = (entry.action == "unlock")
            obj.save(update_fields=["locked", "updated_at"])

    entry.undone = True
    entry.save(update_fields=["undone"])
    return entry


def _restore_snapshot(obj, snap: dict):
    m2m = snap.pop("affected_paragraph_ids", None)
    relation_ids = snap.pop("relation_ids", None)
    field_map = {f.name: f for f in obj._meta.concrete_fields}
    for k, v in snap.items():
        if k not in field_map:
            continue
        field = field_map[k]
        if isinstance(v, str) and field.__class__.__name__ in (
                "DateField", "DateTimeField") and v:
            from datetime import datetime
            try:
                v = datetime.fromisoformat(v)
                if field.__class__.__name__ == "DateField":
                    v = v.date()
            except ValueError:
                pass
        if k == "scan" and isinstance(v, str):
            # ImageField 直接赋文件名即可（文件仍在 media 下）
            obj.scan.name = v
            continue
        setattr(obj, k, v)
    obj.save()
    if m2m is not None and isinstance(obj, Relation):
        obj.affected_paragraphs.set(m2m)
    if relation_ids is not None and isinstance(obj, ClauseParagraph):
        for rid in relation_ids:
            r = Relation.all_objects.filter(pk=rid).first()
            if r is not None:
                r.affected_paragraphs.add(obj)


# --------------------------------------------------------------------------- #
# 结论缓存
# --------------------------------------------------------------------------- #

def dependency_fingerprint(payload: dict) -> tuple[str, dict]:
    """
    依据结论中每个条款的链/缺口涉及的关系 id+updated_at 生成指纹，
    同时返回 clause_name -> [relation_id] 的依赖映射（供下游失效）。
    """
    dataset = serializers.build_dataset()
    rel_stamps = {r["id"]: r for r in dataset["relations"]}
    deps_by_clause: dict[str, set[int]] = defaultdict(set)

    for clause in payload.get("clauses", []):
        name = clause["clause_name"]
        for cand in clause["candidates"]:
            for node in cand["chain"]:
                if node.get("via_relation_id"):
                    deps_by_clause[name].add(node["via_relation_id"])
            for s in cand.get("supplements", []):
                for r in dataset["relations"]:
                    if r["kind"] == "supplement" and \
                            r["from_id"] == s["version_id"]:
                        deps_by_clause[name].add(r["id"])
            for ev in cand.get("evidence", []):
                # 框选目标中的关系
                t = ev.get("target", "")
                if t.startswith("relation:"):
                    rid = int(t.split(":")[1])
                    deps_by_clause[name].add(rid)
        for gap in payload.get("gaps", []):
            if gap.get("clause") != name:
                continue
            vid = gap.get("version_id")
            # 精确：只依赖"撤销了该缺口版本"的撤销关系
            for r in rel_stamps.values():
                if r["kind"] == "revoke_version" and r["to_id"] == vid:
                    deps_by_clause[name].add(r["id"])

    stamps = sorted(
        (rid, (rel_stamps.get(rid, {}).get("deleted", True)))
        for ids in deps_by_clause.values() for rid in ids)
    h = hashlib.sha256(json.dumps(stamps, sort_keys=True,
                                  default=str).encode()).hexdigest()
    return h, {k: sorted(v) for k, v in deps_by_clause.items()}


def save_conclusion(query_date, payload) -> QueryConclusion:
    fp, deps = dependency_fingerprint(payload)
    payload = dict(payload)
    payload["_dependencies"] = deps
    existing = QueryConclusion.objects.filter(
        query_date=query_date, clause_name="").first()
    if existing:
        existing.payload = payload
        existing.dependency_hash = fp
        existing.stale = False
        existing.stale_reason = ""
        existing.save()
        return existing
    return QueryConclusion.objects.create(
        query_date=query_date, clause_name="",
        payload=payload, dependency_hash=fp)


def invalidate_downstream(relation: Relation | None = None, *,
                          deleting: bool = False,
                          clause_names: set[str] | None = None,
                          date_gate=None,
                          relation_id: int | None = None,
                          reason: str = ""):
    """
    精确失效：只标记真正可能改变的已存结论为过期。

    两条判据（取并集）：
    A. 结论依赖中包含本关系 id（撤销修改时含其目标关系 id）；
    B. 结论条款名与本关系两端版本所属条款相交（覆盖新增关系，
       或关系改了指向、使旧结论依赖清单不再完整的情形）。

    日期门控：关系只能影响"不早于其最早可能发生日"的核对结论，
    query_date < date_earliest 的结论绝不判过期；日期完全不清时不过滤。
    """
    dataset = serializers.build_dataset()
    versions = {v["id"]: v for v in dataset["versions"]}
    relations = dataset["relations"]

    rid = relation.pk if relation is not None else relation_id
    rdata = next((r for r in relations if r["id"] == rid), None)

    gate = date_gate
    affected_names: set[str] = set(clause_names or [])
    direct_ids: set[int] = {rid}

    snap = None
    if rdata is None:
        # 已删除：取最近一次删除/修改快照
        log = AuditLog.objects.filter(
            entity_type="relation", entity_id=rid).order_by("-id").first()
        if log and log.snapshot:
            snap = log.snapshot

    if rdata is not None:
        for vid in (rdata["from_id"], rdata["to_id"]):
            if vid in versions:
                affected_names.add(versions[vid]["clause_name"])
        if rdata["kind"] == "revoke_relation" and rdata["target_relation_id"]:
            direct_ids.add(rdata["target_relation_id"])
            t = next((x for x in relations
                      if x["id"] == rdata["target_relation_id"]), None)
            if t:
                for vid in (t["from_id"], t["to_id"]):
                    if vid in versions:
                        affected_names.add(versions[vid]["clause_name"])
        gate = gate or rdata["date_lo"]
    elif snap:
        for key in ("from_version_id", "to_version_id"):
            vid = snap.get(key)
            if vid in versions:
                affected_names.add(versions[vid]["clause_name"])
        if snap.get("kind") == "revoke_relation" and snap.get(
                "target_relation_id"):
            direct_ids.add(snap["target_relation_id"])
        gate = gate or snap.get("date_earliest")

    verb = "删除" if deleting else "修改"
    for concl in QueryConclusion.objects.filter(stale=False):
        if gate is not None and concl.query_date < gate:
            continue  # 关系最早也在该结论日期之后，结论不可能受影响
        deps = (concl.payload or {}).get("_dependencies", {})
        dep_ids = {rid for ids in deps.values() for rid in ids}
        hit_direct = bool(direct_ids & dep_ids)
        hit_clause = bool(affected_names & set(deps.keys()))
        if hit_direct or hit_clause:
            concl.stale = True
            concl.stale_reason = reason or (
                f"关系 #{rid} 被{verb}，相关条款结论需重新生成")
            concl.save(update_fields=["stale", "stale_reason", "updated_at"])
