"""
条款沿革推演引擎（纯 Python，不依赖 ORM，便于测试）。

设计原则（对应业务约束）：
1. 看不清的日期一律为区间 [lo, hi]，端点 None 表示开口；所有判定区分
   "确定 / 可能"，绝不替用户猜测一个具体日期。
2. 局部替换按段落号对齐，只覆盖被涉及段落，未涉及段落沿用旧文。
3. 撤销只废止：被撤销处形成"缺口"，系统绝不自动恢复旧文。
4. 同一旧版本存在多条不相容的修改链时，并列输出多个候选，标注确定性。
5. 本引擎只整理沿革、候选与缺口，不判断理赔结果。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


# --------------------------------------------------------------------------- #
# 日期区间工具
# --------------------------------------------------------------------------- #

def iso(d) -> str | None:
    return d.isoformat() if d is not None else None


def pair_invalid(lo, hi) -> bool:
    """区间倒置：lo 晚于 hi。"""
    return lo is not None and hi is not None and lo > hi


def span_status(start: tuple, end: tuple, d) -> str:
    """
    版本在指定日期 d 的效力状态（区间 [start_lo,start_hi] ~ [end_lo,end_hi]）。

    返回 active（确定有效）/ possible（可能有效）/ expired（确定已失效）/
    future（确定尚未生效）。
    """
    slo, shi = start
    elo, ehi = end
    if slo is not None and slo > d:
        return "future"
    if ehi is not None and ehi < d:
        return "expired"
    after_start = shi is None or shi <= d
    before_end = elo is None or elo >= d
    if after_start and before_end:
        return "active"
    return "possible"


def occurred(rng: tuple, d) -> str:
    """
    关系（修改/撤销）在日期 d 是否已发生。
    返回 yes（确定已发生）/ maybe（可能已发生）/ no（确定未发生）。
    """
    lo, hi = rng
    if lo is None and hi is None:
        return "maybe"  # 日期完全看不清：按可能处理，交由人工核对
    if hi is not None and hi <= d:
        return "yes"
    if lo is not None and lo <= d:
        return "maybe"
    return "no"


def spans_certainly_overlap(a_start, a_end, b_start, b_end) -> bool:
    """两个生效区间必然重叠（端点均参与比较，开口端按 ±∞）。"""
    as_lo, as_hi = a_start
    ae_lo, ae_hi = a_end
    bs_lo, bs_hi = b_start
    be_lo, be_hi = b_end
    # A 必然已开始 = as_hi 有界（开口端无法"必然"，保守起见降级）
    if as_hi is None or bs_hi is None or ae_lo is None or be_lo is None:
        return False
    return as_hi <= be_lo and bs_hi <= ae_lo


def spans_may_overlap(a_start, a_end, b_start, b_end) -> bool:
    """两个生效区间有可能重叠（用于把"可能重叠"降级为提示）。"""
    as_lo, _ = a_start
    ae_lo, ae_hi = a_end
    bs_lo, _ = b_start
    be_lo, be_hi = b_end
    # 必然不重叠：A 最晚结束 < B 最早开始，或反之
    if ae_hi is not None and bs_lo is not None and ae_hi < bs_lo:
        return False
    if be_hi is not None and as_lo is not None and be_hi < as_lo:
        return False
    return True


# --------------------------------------------------------------------------- #
# 数据规整
# --------------------------------------------------------------------------- #

def _version_span(v: dict) -> tuple[tuple, tuple]:
    return (v["start_lo"], v["start_hi"]), (v["end_lo"], v["end_hi"])


def _relation_date(r: dict) -> tuple:
    return r["date_lo"], r["date_hi"]


def normalize_dataset(raw: dict) -> dict:
    """按 id 建索引，剔除已删除条目（视图层保证，但引擎再防一次）。"""
    documents = {d["id"]: d for d in raw.get("documents", []) if not d.get("deleted")}
    versions = {v["id"]: v for v in raw.get("versions", []) if not v.get("deleted")
                and v["document_id"] in documents}
    relations = {r["id"]: r for r in raw.get("relations", []) if not r.get("deleted")
                 and r["document_id"] in documents}
    regions = [g for g in raw.get("regions", []) if g["document_id"] in documents]
    return {"documents": documents, "versions": versions,
            "relations": relations, "regions": regions}


# --------------------------------------------------------------------------- #
# 全局核查：悬空引用 / 时间倒置 / 循环修改 / 同条款重叠生效
# --------------------------------------------------------------------------- #

def run_checks(data: dict) -> list[dict]:
    data = normalize_dataset(data)
    issues: list[dict] = []

    def add(level: str, code: str, message: str, refs: dict | None = None):
        issues.append({"level": level, "code": code, "message": message,
                       "refs": refs or {}})

    versions: dict[int, dict] = data["versions"]
    relations: dict[int, dict] = data["relations"]
    documents: dict[int, dict] = data["documents"]

    # --- 1. 悬空引用 -------------------------------------------------------- #
    for r in relations.values():
        kind = r["kind"]
        if kind in ("replace", "partial", "supplement"):
            if not r["from_id"] or r["from_id"] not in versions:
                add("error", "dangling",
                    f"关系 #{r['id']}（{_kind_text(kind)}）缺少有效的新版本",
                    {"relation_id": r["id"]})
            if not r["to_id"] or r["to_id"] not in versions:
                add("error", "dangling",
                    f"关系 #{r['id']}（{_kind_text(kind)}）缺少被修改的旧版本",
                    {"relation_id": r["id"]})
            if r["from_id"] in versions and r["to_id"] in versions and \
                    r["from_id"] == r["to_id"]:
                add("error", "self_ref", f"关系 #{r['id']} 的新旧版本指向同一条款版本",
                    {"relation_id": r["id"]})
        elif kind == "revoke_version":
            if not r["to_id"] or r["to_id"] not in versions:
                add("error", "dangling",
                    f"撤销关系 #{r['id']} 缺少被撤销的条款版本",
                    {"relation_id": r["id"]})
        elif kind == "revoke_relation":
            tid = r["target_relation_id"]
            if not tid or tid not in relations:
                add("error", "dangling",
                    f"撤销关系 #{r['id']} 指向了不存在或已删除的关系 #{tid}",
                    {"relation_id": r["id"]})
            elif relations[tid]["kind"] in ("revoke_version", "revoke_relation"):
                add("error", "dangling",
                    f"撤销关系 #{r['id']} 不能再撤销一条撤销关系（#{tid}）",
                    {"relation_id": r["id"]})
        if kind == "partial" and r["from_id"] in versions:
            v = versions[r["from_id"]]
            bad = [pid for pid in r["affected_paragraph_ids"]
                   if pid not in v["paragraph_ids"]]
            if bad:
                add("error", "dangling",
                    f"局部替换关系 #{r['id']} 涉及的段落 {bad} 不属于其新版本 "
                    f"{v['clause_name']}",
                    {"relation_id": r["id"], "paragraph_ids": bad})
            if not r["affected_paragraph_ids"]:
                add("warning", "empty_partial",
                    f"局部替换关系 #{r['id']} 未标注任何涉及段落，无法判断保留范围",
                    {"relation_id": r["id"]})

    # --- 2. 时间倒置 -------------------------------------------------------- #
    for d in documents.values():
        for field, lo, hi in (("issued", d["issued_lo"], d["issued_hi"]),
                              ("start", d["start_lo"], d["start_hi"]),
                              ("end", d["end_lo"], d["end_hi"])):
            if pair_invalid(lo, hi):
                add("error", "inverted_date",
                    f"材料 {d['file_no']} 的{_field_text(field)}日期区间倒置："
                    f"{lo.isoformat()} 晚于 {hi.isoformat()}",
                    {"document_id": d["id"], "field": field})

    for r in relations.values():
        for field, lo, hi in (("date", r["date_lo"], r["date_hi"]),
                              ("eff_start", r["eff_start_lo"], r["eff_start_hi"]),
                              ("eff_end", r["eff_end_lo"], r["eff_end_hi"])):
            if pair_invalid(lo, hi):
                add("error", "inverted_date",
                    f"关系 #{r['id']} 的{_field_text(field)}日期区间倒置",
                    {"relation_id": r["id"], "field": field})
        doc = documents.get(r["document_id"])
        if doc and doc["issued_lo"] and r["date_hi"]:
            if r["date_hi"] < doc["issued_lo"]:
                add("error", "inverted_date",
                    f"关系 #{r['id']} 的日期（最晚 {r['date_hi']}）早于其依据材料 "
                    f"{doc['file_no']} 的签发日（最早 {doc['issued_lo']}）",
                    {"relation_id": r["id"], "document_id": doc["id"]})
        if r["to_id"] in versions:
            tv = versions[r["to_id"]]
            (slo, shi), _ = _version_span(tv)
            if slo and r["date_hi"] and r["date_hi"] < slo:
                add("error", "inverted_date",
                    f"关系 #{r['id']}（{_kind_text(r['kind'])}）发生得比被修改版本"
                    f"「{tv['clause_name']}」的最早生效日 {slo} 还早",
                    {"relation_id": r["id"], "version_id": tv["id"]})

    # --- 3. 循环修改（迭代式 DFS，避免递归栈/重入问题） --------------------- #
    graph: dict[int, set[int]] = defaultdict(set)
    nodes_in_graph: set[int] = set()
    for r in relations.values():
        if r["from_id"] in versions and r["to_id"] in versions:
            graph[r["from_id"]].add(r["to_id"])
            nodes_in_graph.add(r["from_id"])
            nodes_in_graph.add(r["to_id"])
    cycles: list[list[int]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[int, int] = {vid: WHITE for vid in nodes_in_graph}
    for start in sorted(nodes_in_graph):
        if color[start] != WHITE:
            continue
        # 栈元素：(节点, 邻接迭代器)
        stack: list[tuple[int, Any]] = [(start, iter(sorted(graph[start])))]
        path = [start]
        color[start] = GRAY
        while stack:
            u, it = stack[-1]
            advanced = False
            for w in it:
                if color[w] == GRAY and w in path:
                    cyc = path[path.index(w):] + [w]
                    if not any(set(cyc[:-1]) == set(c[:-1]) for c in cycles):
                        cycles.append(cyc)
                elif color[w] == WHITE:
                    color[w] = GRAY
                    path.append(w)
                    stack.append((w, iter(sorted(graph[w]))))
                    advanced = True
                    break
            if not advanced:
                color[u] = BLACK
                stack.pop()
                if path and path[-1] == u:
                    path.pop()
    for cyc in cycles:
        names = " → ".join(_v_label(versions[i]) for i in cyc)
        add("error", "cycle", f"检测到循环修改：{names}",
            {"version_ids": cyc[:-1]})

    # --- 4. 同一条款重叠生效 ------------------------------------------------ #
    groups: dict[str, list[dict]] = defaultdict(list)
    for v in versions.values():
        groups[v["clause_name"]].append(v)
    for clause, vs in groups.items():
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                a, b = vs[i], vs[j]
                a_s, a_e = _version_span(a)
                b_s, b_e = _version_span(b)
                linked = any(
                    (rr["from_id"] == a["id"] and rr["to_id"] == b["id"]) or
                    (rr["from_id"] == b["id"] and rr["to_id"] == a["id"])
                    for rr in relations.values()
                    if rr["kind"] in ("replace", "partial"))
                if spans_certainly_overlap(a_s, a_e, b_s, b_e):
                    add("error" if not linked else "warning", "overlap",
                        f"「{clause}」的两个版本 {_v_label(a)} 与 {_v_label(b)} "
                        f"生效区间必然重叠" + ("（虽有替换关系，仍请核对旧版失效日）" if linked else ""),
                        {"version_ids": [a["id"], b["id"]], "linked": linked})
                elif spans_may_overlap(a_s, a_e, b_s, b_e):
                    add("warning", "possible_overlap",
                        f"「{clause}」的两个版本 {_v_label(a)} 与 {_v_label(b)} "
                        f"生效区间可能重叠（多为旧版未记载失效日），结论将按候选并列",
                        {"version_ids": [a["id"], b["id"]]})

    return issues


def _kind_text(kind: str) -> str:
    return {"replace": "全文替换", "partial": "局部替换",
            "supplement": "增补", "revoke_version": "撤销版本",
            "revoke_relation": "撤销修改"}.get(kind, kind)


def _field_text(field: str) -> str:
    return {"issued": "签发", "start": "生效起", "end": "生效止",
            "date": "发生", "eff_start": "生效起", "eff_end": "生效止"}.get(field, field)


def _v_label(v: dict) -> str:
    return f"{v['clause_name']}（{v['version_label'] or v['doc_file_no']}）"


# --------------------------------------------------------------------------- #
# 指定日期推演
# --------------------------------------------------------------------------- #

def analyze(raw: dict, query_date) -> dict:
    """按指定日期生成沿革：问题清单、各条款的并列候选链、缺口、沿革图。"""
    data = normalize_dataset(raw)
    issues = run_checks(raw)
    versions = data["versions"]
    relations = data["relations"]

    # 关系 / 版本在 query_date 的撤销状态
    rel_revoked: dict[int, str] = {}   # relation_id -> "yes" / "maybe"
    ver_revoked: dict[int, str] = {}
    for r in relations.values():
        occ = occurred(_relation_date(r), query_date)
        if occ == "no":
            continue
        level = "yes" if occ == "yes" else "maybe"
        if r["kind"] == "revoke_relation" and r["target_relation_id"] in relations:
            old = rel_revoked.get(r["target_relation_id"])
            rel_revoked[r["target_relation_id"]] = _stronger(level, old)
        elif r["kind"] == "revoke_version" and r["to_id"] in versions:
            old = ver_revoked.get(r["to_id"])
            ver_revoked[r["to_id"]] = _stronger(level, old)

    # 版本状态
    v_status: dict[int, str] = {}
    for vid, v in versions.items():
        s, e = _version_span(v)
        v_status[vid] = span_status(s, e, query_date)

    # 关系是否在 query_date 已发生 / 是否有效（未被撤销）
    def edge_occ(r: dict) -> str:
        return occurred(_relation_date(r), query_date)

    # 被撤销的关系不参与成链（撤销关系只废止，不产生新边）
    live_relations = {rid: r for rid, r in relations.items()
                      if rel_revoked.get(rid) != "yes"}

    # 按条款分组
    groups: dict[str, list[int]] = defaultdict(list)
    for vid, v in versions.items():
        groups[v["clause_name"]].append(vid)

    clauses_out = []
    gaps = []

    for clause in sorted(groups):
        vids = set(groups[clause])
        # backbone：replace/partial 边（new -> old），只取已发生且未确定撤销的
        incoming: dict[int, list[int]] = defaultdict(list)
        outgoing: dict[int, list[int]] = defaultdict(list)
        supplements: dict[int, list[int]] = defaultdict(list)  # old -> [supp]
        supplement_incoming: dict[int, list[int]] = defaultdict(list)
        edge_certainty: dict[int, str] = {}
        for r in live_relations.values():
            if r["from_id"] not in vids or r["to_id"] not in vids:
                continue
            occ = edge_occ(r)
            if occ == "no":
                continue
            cert = "def" if occ == "yes" else "maybe"
            if rel_revoked.get(r["id"]) == "maybe":
                cert = "maybe"
            if r["kind"] in ("replace", "partial"):
                outgoing[r["from_id"]].append(r["to_id"])
                # 只有"确定已发生"的替换才把新版本并入旧链根；
                # 可能已发生时新版本仍是独立根（对应"修改尚未发生"世界线）
                if occ == "yes":
                    incoming[r["to_id"]].append(r["from_id"])
                edge_certainty[r["id"]] = cert
            elif r["kind"] == "supplement":
                supplements[r["to_id"]].append(r["from_id"])
                if occ == "yes":
                    supplement_incoming[r["from_id"]].append(r["to_id"])

        for u in list(outgoing):
            outgoing[u] = sorted(set(outgoing[u]))
        # 根 = 没有"确定已发生"替换入边的版本，外加被撤销版本
        # （被撤销版本必须保留为链头：候选标记 revoked_gap，旧文不自动恢复）
        revoked_in_group = {vid for vid in vids
                            if ver_revoked.get(vid) in ("yes", "maybe")}
        roots = sorted((vids - set(incoming.keys())) | revoked_in_group,
                       key=lambda x: (-_rank(v_status[x]), x))

        # DFS 枚举 backbone 链。
        # * 分叉（同一新版本去向多条替换）= 互不相容链 = 并列候选；
        # * 关系日期"可能已发生"时，沿边/不沿边各产生一条链（可能/确定并列）；
        # * 存在环时已在 run_checks 报错，这里沿已访问集合截断，避免死循环。
        raw_paths: list[tuple[list[int], bool]] = []
        emitted: set[tuple] = set()

        def emit(path, certain):
            key = tuple(path)
            if key in emitted:
                return
            emitted.add(key)
            raw_paths.append((list(path), certain))

        def walk(node, path, certain, seen, via_maybe=False,
                 is_worldline_b=False):
            children = [c for c in outgoing.get(node, []) if c not in seen]
            if not children:
                # 经由"可能已发生"的边到达：即便末端版本已失效也保留链，
                # 这是一条需要人工核对的可能候选
                if via_maybe or len(path) == 1 or v_status[node] != "future":
                    emit(path, certain)
                return
            for child in children:
                eid = _edge_between(live_relations, node, child,
                                    ("replace", "partial"))
                edge_occ_state = edge_occ(relations[eid]) if eid else "yes"
                if edge_occ_state == "maybe" \
                        and v_status[node] in ("active", "possible"):
                    if not is_worldline_b:
                        # 世界线 B：修改尚未发生 → 停在当前版本（不确定候选）
                        emit(path, False)
                    # 世界线 A：修改已发生 → 沿边（标记为可能）
                    walk(child, path + [child], False, seen | {child}, True)
                else:
                    c = certain and (eid is None
                                     or edge_certainty.get(eid, "def") == "def")
                    walk(child, path + [child], c, seen | {child}, via_maybe)

        # 可能已发生的替换边所指向的旧版本：其"未被修改"世界线本身也是可能的
        maybe_incoming: set[int] = set()
        for r in live_relations.values():
            if r["kind"] in ("replace", "partial") and \
                    r["from_id"] in vids and r["to_id"] in vids and \
                    edge_occ(r) == "maybe":
                maybe_incoming.add(r["to_id"])

        for root in roots:
            # 与指定日期无关的版本不产生候选：确定尚未生效、未被撤销、
            # 且没有一条在该日已发生/可能发生的出边
            has_live_out = any(
                edge_occ(relations[_edge_between(
                    live_relations, root, child,
                    ("replace", "partial"))]) in ("yes", "maybe")
                for child in outgoing.get(root, []))
            if v_status[root] == "future" and not has_live_out \
                    and root not in revoked_in_group:
                continue
            # 增补版本独立成根时，仅当增补关系在该日已发生/可能发生
            supp_occ_states = []
            for old in supplement_incoming.get(root, []):
                eid = _edge_between(live_relations, root, old,
                                    ("supplement",))
                if eid:
                    supp_occ_states.append(edge_occ(relations[eid]))
            if supp_occ_states and not any(
                    s in ("yes", "maybe") for s in supp_occ_states) \
                    and not outgoing.get(root) \
                    and root not in revoked_in_group:
                continue
            # 该根可能已被一条日期不清的关系修改：独立世界线标"可能"
            root_certain = root not in maybe_incoming
            walk(root, [root], root_certain, {root})

        candidates = []
        for path, certain in raw_paths:
            cand = _build_candidate(data, query_date, path, certain, supplements,
                                    live_relations, edge_certainty, v_status,
                                    ver_revoked, rel_revoked, edge_occ)
            if cand.get("_drop"):
                continue
            candidates.append(cand)
            if cand["status"] == "revoked_gap":
                rv = cand["revoked_at_version"]
                gaps.append({
                    "clause": clause,
                    "kind": "revoked",
                    "message": f"「{clause}」在 {query_date.isoformat()} 适用的版本"
                               f"已被撤销，系统不自动恢复旧文，存在条款缺口",
                    "version_id": rv,
                    "candidate_index": len(candidates) - 1,
                })

        # 去重（同一版本路径可能由不同根重复触达增补链）
        candidates = _dedupe_candidates(candidates)

        active_versions = [vid for vid in vids
                           if v_status[vid] in ("active", "possible")]
        if not active_versions:
            status = "no_effective_version"
            if any(v_status[vid] == "expired" for vid in vids):
                gaps.append({
                    "clause": clause, "kind": "no_coverage",
                    "message": f"「{clause}」在 {query_date.isoformat()} "
                               f"没有记载中有效的版本（历史版本均已失效或日期未明）",
                })
        elif candidates and all(c["status"] == "revoked_gap" for c in candidates):
            status = "revoked_gap"
        elif not candidates:
            status = "no_effective_version"
        else:
            status = "effective"

        graph = _build_graph(data, query_date, vids, v_status,
                             relations, rel_revoked, ver_revoked, edge_occ)

        clauses_out.append({
            "clause_name": clause,
            "status": status,
            "candidate_count": len(candidates),
            "candidates": candidates,
            "graph": graph,
        })

    payload = {
        "query_date": query_date.isoformat(),
        "clauses": clauses_out,
        "issues": issues,
        "gaps": gaps,
        "documents": _documents_index(data),
    }
    return payload


def _stronger(a: str, b: str | None) -> str:
    """撤销强度合并：确定撤销优先于可能撤销。"""
    if b == "yes" or a == "yes":
        return "yes"
    return a


def _rank(status: str) -> int:
    return {"active": 3, "possible": 2, "future": 1, "expired": 0}.get(status, 0)


def _edge_between(relations: dict, new_id: int, old_id: int, kinds) -> int | None:
    for rid, r in relations.items():
        if r["from_id"] == new_id and r["to_id"] == old_id and r["kind"] in kinds:
            return rid
    return None


def _build_candidate(data, query_date, path, certain, supplements, live_relations,
                     edge_certainty, v_status, ver_revoked, rel_revoked, edge_occ):
    """对一条新版本→旧版本的链做段落重放，并汇总依据。"""
    versions = data["versions"]
    relations = data["relations"]

    # 链上节点状态：最新→最旧；找第一个被撤销的节点
    revoked_at = None
    for vid in path:
        if ver_revoked.get(vid) == "yes":
            revoked_at = vid
            certain = certain and False
            break
        if ver_revoked.get(vid) == "maybe":
            certain = False

    # 增补版本（挂在链上各节点），取已发生的
    attached: list[tuple[int, int]] = []  # (supp_version_id, at_version_id)
    for vid in path:
        for sup in supplements.get(vid, []):
            edge = _edge_between(live_relations, sup, vid, ("supplement",))
            if edge is None:
                continue
            occ = edge_occ(relations[edge])
            if occ == "no":
                continue
            if occ == "maybe":
                certain = False
            if ver_revoked.get(sup) == "yes":
                continue
            if ver_revoked.get(sup) == "maybe":
                certain = False
            attached.append((sup, vid))

    # 段落重放（无缺口时）：沿新版本→旧版本填充，被涉及段落才覆盖
    paragraphs = {}
    para_provenance: dict[str, int | None] = {}
    conflicts = []

    def absorb(vid: int, via_relation_id, only_nos: set[str] | None):
        v = versions[vid]
        for p in v["paragraphs"]:
            no = p["paragraph_no"]
            if only_nos is not None and no not in only_nos:
                continue
            if no in paragraphs:
                # 增补与主链对同一段落号给出不同文本：无法自动合并，提示人工核对
                if via_relation_id and \
                        relations[via_relation_id]["kind"] == "supplement":
                    conflicts.append(no)
                continue
            paragraphs[no] = {
                "paragraph_no": no,
                "title": p["title"],
                "text": p["text"],
                "version_id": vid,
                "document_id": v["document_id"],
                "via_relation_id": via_relation_id,
            }
            para_provenance[no] = via_relation_id

    # 链新→旧：先放各节点的增补，再按关系类型处理节点自身
    for idx, vid in enumerate(path):
        edge_id = None
        if idx > 0:
            edge_id = _edge_between(live_relations, path[idx - 1], vid,
                                    ("replace", "partial"))
        # 该节点附带的增补先吸收（增补内容与主链并行）
        for sup, at in attached:
            if at == vid:
                se = _edge_between(live_relations, sup, vid, ("supplement",))
                absorb(sup, se, None)
        if idx == 0:
            absorb(vid, None, None)
        else:
            r = relations[edge_id]
            if edge_certainty.get(edge_id) == "maybe":
                certain = False
            if r["kind"] == "partial":
                new_v = versions[path[idx - 1]]
                # 被涉及段落号 = 新版本中挂在该关系上段落的段落号；
                # 旧版本只吸收这些段落号之外的内容（未涉及段落不被抹去）
                affected_nos = {
                    p["paragraph_no"] for p in new_v["paragraphs"]
                    if p["id"] in set(r["affected_paragraph_ids"])}
                old_nos = {p["paragraph_no"]
                           for p in versions[vid]["paragraphs"]}
                absorb(vid, edge_id, old_nos - affected_nos)
            else:
                absorb(vid, edge_id, None)

    # 组装链描述（含增补分支标记）
    chain_desc = []
    for idx, vid in enumerate(path):
        edge_id = None
        edge_cert = None
        if idx > 0:
            edge_id = _edge_between(live_relations, path[idx - 1], vid,
                                    ("replace", "partial"))
            edge_cert = edge_certainty.get(edge_id)
        v = versions[vid]
        chain_desc.append({
            "version_id": vid,
            "clause_name": v["clause_name"],
            "version_label": v["version_label"],
            "document_id": v["document_id"],
            "doc_file_no": v["doc_file_no"],
            "status": v_status[vid],
            "revoked": ver_revoked.get(vid),
            "via_relation_id": edge_id,
            "via_certainty": edge_cert,
            "supplements_here": [s for s, at in attached if at == vid],
        })

    involved_version_ids = set(path) | {s for s, _ in attached}
    involved_relation_ids = {c["via_relation_id"] for c in chain_desc
                             if c["via_relation_id"]}
    for s, at in attached:
        eid = _edge_between(live_relations, s, at, ("supplement",))
        if eid:
            involved_relation_ids.add(eid)
    if revoked_at is not None:
        for r in relations.values():
            if r["kind"] == "revoke_version" and r["to_id"] == revoked_at:
                involved_relation_ids.add(r["id"])

    evidence = _collect_evidence(data, involved_version_ids,
                                 involved_relation_ids)

    active_states = [v_status[v] for v in path] + \
                    [v_status[s] for s, _ in attached]
    if revoked_at is not None:
        status = "revoked_gap"
    elif any(s in ("active", "possible") for s in active_states):
        status = "effective"
    else:
        status = "not_in_force"

    # 头部版本确定尚未生效（如孤立的增补/未来版），该候选与指定日期无关
    drop = v_status[path[0]] == "future" and revoked_at is None

    return {
        "_drop": drop,
        "certainty": "definite" if certain and
                     all(v_status[v] == "active" for v in path) and
                     all(v_status[s] == "active" for s, _ in attached)
                     else "possible",
        "status": status,
        "chain": chain_desc,
        "supplements": [{"version_id": s, "at_version_id": at}
                        for s, at in attached],
        "revoked_at_version": revoked_at,
        "paragraphs_effective": [] if revoked_at is not None
        else list(paragraphs.values()),
        "paragraph_conflicts": sorted(set(conflicts)),
        "evidence": evidence,
        "note": ("新版本被撤销，旧文不自动恢复，以下旧链仅供逐级回溯核对"
                 if revoked_at is not None else ""),
    }


def _collect_evidence(data, version_ids, relation_ids) -> list[dict]:
    """汇总链上版本/段落/关系/材料对应的全部框选依据。"""
    version_prefixes = tuple(f"version:{vid}" for vid in version_ids)
    relation_prefixes = tuple(f"relation:{rid}" for rid in relation_ids)
    out = []
    for g in data["regions"]:
        t = g["target"]
        if t.startswith(version_prefixes) or t.startswith(relation_prefixes):
            out.append(_region_ref(data, g))
    return out


def _region_ref(data, g: dict) -> dict:
    doc = data["documents"].get(g["document_id"], {})
    return {
        "region_id": g["id"],
        "document_id": g["document_id"],
        "doc_file_no": doc.get("file_no"),
        "scan": doc.get("scan_url"),
        "target": g["target"],
        "box": {"x": g["x"], "y": g["y"], "w": g["w"], "h": g["h"]},
        "label": g["label"],
    }


def _build_graph(data, query_date, vids, v_status, all_relations,
                 rel_revoked, ver_revoked, edge_occ) -> dict:
    nodes, edges = [], []
    for vid in sorted(vids):
        v = data["versions"][vid]
        nodes.append({
            "id": vid,
            "label": _v_label(v),
            "clause_name": v["clause_name"],
            "version_label": v["version_label"],
            "doc_file_no": v["doc_file_no"],
            "document_id": v["document_id"],
            "status": v_status[vid],
            "revoked": ver_revoked.get(vid),
        })
    # 被撤销的边同样画出（虚线 + 已撤销标记），便于人工核对，绝不装作不存在
    for rid, r in all_relations.items():
        # 只保留两端都属于本条款的边（含撤销版本：from 可空，to 在组内）
        if r["kind"] == "revoke_version":
            if r["to_id"] not in vids:
                continue
        else:
            if r["from_id"] not in vids or r["to_id"] not in vids:
                continue
        occ = edge_occ(r)
        edges.append({
            "id": rid,
            "kind": r["kind"],
            "from": r["from_id"],
            "to": r["to_id"],
            "target_relation_id": r["target_relation_id"],
            "occurred": occ,
            "revoked": rel_revoked.get(rid),
            "description": r["description"],
            "locked": r["locked"],
        })
    return {"nodes": nodes, "edges": edges}


def _documents_index(data) -> list[dict]:
    return [{
        "id": d["id"], "file_no": d["file_no"], "title": d["title"],
        "kind": d["kind"], "scan": d["scan_url"],
        "issued": [iso(d["issued_lo"]), iso(d["issued_hi"])],
        "effective": {"start": [iso(d["start_lo"]), iso(d["start_hi"])],
                      "end": [iso(d["end_lo"]), iso(d["end_hi"])]},
        "note": d["note"],
    } for d in data["documents"].values()]


def _dedupe_candidates(candidates: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for c in candidates:
        key = (tuple(x["version_id"] for x in c["chain"]),
               tuple(s["version_id"] for s in c["supplements"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# --------------------------------------------------------------------------- #
# 结论缓存依赖指纹
# --------------------------------------------------------------------------- #

def touched_clause_names(raw: dict) -> dict[int, set[str]]:
    """每条关系影响到的条款名（撤销关系穿透到其目标关系，供下游过期判断）。"""
    data = normalize_dataset(raw)
    versions = data["versions"]
    relations = data["relations"]
    result: dict[int, set[str]] = {}
    for rid, r in relations.items():
        names = set()
        for vid in (r["from_id"], r["to_id"]):
            if vid in versions:
                names.add(versions[vid]["clause_name"])
        if r["kind"] == "revoke_relation" and r["target_relation_id"] in relations:
            t = relations[r["target_relation_id"]]
            for vid in (t["from_id"], t["to_id"]):
                if vid in versions:
                    names.add(versions[vid]["clause_name"])
        result[rid] = names
    return result
