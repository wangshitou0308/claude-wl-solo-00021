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
        elif f.name in ("scan",) and obj.scan:
            val = obj.scan.name
        data[f.name] = val
    if isinstance(obj, Relation):
        data["affected_paragraph_ids"] = list(
            obj.affected_paragraphs.values_list("id", flat=True))
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
    obj.deleted = True
    obj.save(update_fields=["deleted", "updated_at"])
    log_action("delete", entity_type, obj.pk,
               f"删除{entity_type} #{obj.pk}", before)
    if entity_type == "relation":
        invalidate_downstream(obj, deleting=True)


def hard_delete_region_with_audit(region: EvidenceRegion):
    rid = region.pk
    region.delete()
    log_action("delete", "region", rid, f"删除框选 #{rid}", None)


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
            obj.delete()
        else:
            obj.deleted = True
            obj.save(update_fields=["deleted", "updated_at"])
        if entry.entity_type == "relation":
            invalidate_downstream(obj, deleting=True)

    elif entry.action == "delete":
        obj = model.all_objects.filter(pk=entry.entity_id).first()
        if obj is None:
            raise LookupError("待恢复的对象已不存在")
        obj.deleted = False
        obj.save(update_fields=["deleted", "updated_at"])
        if entry.entity_type == "relation":
            invalidate_downstream(obj)

    elif entry.action == "update":
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
    field_names = {f.name for f in obj._meta.concrete_fields}
    for k, v in snap.items():
        if k in field_names:
            setattr(obj, k, v)
    obj.save()
    if m2m is not None and isinstance(obj, Relation):
        obj.affected_paragraphs.set(m2m)


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
            if gap.get("clause") == name:
                for rid, r in rel_stamps.items():
                    if r["kind"] in ("revoke_version", "revoke_relation"):
                        deps_by_clause[name].add(rid)

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


def invalidate_downstream(relation: Relation, *, deleting: bool = False):
    """
    关系变化后，仅让受影响条款的已存结论过期。

    受影响条款 = 关系两端版本所属条款，并沿 replace/partial 边
    （新版本→旧版本方向）向下游传播；撤销关系穿透到其目标关系。
    """
    dataset = serializers.build_dataset()
    versions = {v["id"]: v for v in dataset["versions"]}
    relations = dataset["relations"]

    seeds: set[int] = set()
    rdata = next((r for r in relations if r["id"] == relation.pk), None)
    if rdata:
        for vid in (rdata["from_id"], rdata["to_id"]):
            if vid in versions:
                seeds.add(vid)
        if rdata["kind"] == "revoke_relation":
            t = next((r for r in relations
                      if r["id"] == rdata["target_relation_id"]), None)
            if t:
                for vid in (t["from_id"], t["to_id"]):
                    if vid in versions:
                        seeds.add(vid)
    else:
        # 删除时无法从新数据集定位：尝试审计快照
        log = AuditLog.objects.filter(
            entity_type="relation", entity_id=relation.pk).order_by("-id").first()
        if log and log.snapshot:
            for key in ("from_version_id", "to_version_id"):
                vid = log.snapshot.get(key)
                if vid in versions:
                    seeds.add(vid)

    # 沿 incoming（旧→新）反向找上游 + outgoing（新→旧）下游：
    # 结论可能建立在任一侧，传播到整条连通分量最稳妥
    adj: dict[int, set[int]] = defaultdict(set)
    for r in relations:
        if r["kind"] in ("replace", "partial", "supplement") \
                and r["from_id"] and r["to_id"]:
            adj[r["from_id"]].add(r["to_id"])
            adj[r["to_id"]].add(r["from_id"])
    affected_versions: set[int] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        if cur in affected_versions:
            continue
        affected_versions.add(cur)
        stack.extend(adj[cur] - affected_versions)

    clause_names = {versions[v]["clause_name"] for v in affected_versions
                    if v in versions}
    if not clause_names:
        return
    qs = QueryConclusion.objects.filter(stale=False)
    for concl in qs:
        deps = (concl.payload or {}).get("_dependencies", {})
        if clause_names & set(deps.keys()):
            concl.stale = True
            concl.stale_reason = (
                f"关系 #{relation.pk} 被" +
                ("删除" if deleting else "修改") +
                "，相关条款结论需重新生成")
            concl.save(update_fields=["stale", "stale_reason", "updated_at"])
