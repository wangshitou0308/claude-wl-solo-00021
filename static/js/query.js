/* 指定日期核对视图：问题清单、沿革图、并列候选、缺口、逐级回溯、导出 */
"use strict";

const ChecksView = {
  async render(root) {
    const data = await Api.get("/api/checks/");
    root.append(h.el("div", { class: "card" },
      h.el("h2", {}, "全库核查（悬空引用 / 时间倒置 / 循环修改 / 同条款重叠生效）"),
      h.el("p", { class: "muted small" },
        "错误（红）会影响沿革推演，建议先修正；提示（黄）多因日期看不清或旧版未记载失效日，结论会以多个候选并列。")));
    const card = root.querySelector(".card");
    if (!data.issues.length) {
      card.append(h.el("div", { class: "issue-warning", style: "background:var(--green-soft);border-color:var(--green);" },
        "✓ 未发现结构性问题。"));
      return;
    }
    data.issues.forEach(i => card.append(h.el("div", {
      class: i.level === "error" ? "issue-error" : "issue-warning",
    },
      h.el("strong", {}, i.level === "error" ? "错误 · " : "提示 · ",
        i.code === "dangling" ? "悬空引用" :
        i.code === "inverted_date" ? "时间倒置" :
        i.code === "cycle" ? "循环修改" :
        i.code === "overlap" ? "同条款重叠生效" :
        i.code === "possible_overlap" ? "可能重叠" :
        i.code === "empty_partial" ? "局部替换范围缺失" : i.code),
      h.el("div", {}, i.message),
      (i.refs?.relation_id || i.refs?.document_id)
        ? h.el("button", {
            class: "btn-tiny btn-ghost", style: "margin-top:.3rem;",
            onclick: () => i.refs.relation_id
              ? RelationDetail.open(i.refs.relation_id)
              : DocumentDetail.open(i.refs.document_id),
          }, "打开核对") : null)));
  },
};

const QueryView = {
  payload: null,
  queryDate: null,

  async render(root) {
    const today = new Date().toISOString().slice(0, 10);
    const dateInput = h.el("input", {
      type: "date", id: "query-date", value: today,
    });
    root.append(
      h.el("div", { class: "card" },
        h.el("h2", {}, "按日期核对条款沿革"),
        h.el("div", { class: "row" },
          h.el("label", { class: "field" }, h.el("b", {}, "指定日期"), dateInput),
          h.el("button", {
            style: "margin-top:1.1em;",
            onclick: () => this.run(false),
          }, "生成沿革图与适用候选"),
          h.el("button", {
            class: "btn-ghost", style: "margin-top:1.1em;",
            onclick: () => this.run(true),
          }, "强制重新生成"),
          h.el("div", { class: "spacer" }),
          h.el("button", {
            class: "btn-warn", style: "margin-top:1.1em;",
            onclick: () => this.exportHandoff(),
          }, "⬇ 导出 JSON 交接件")),
        h.el("p", { class: "muted small" },
          "本页只整理“该日可能适用哪版条款、依据在哪、还缺什么材料”，不判断理赔结果。"),
        h.el("div", { id: "stale-banner" })),
      h.el("div", { id: "query-out" }));
  },

  async run(force) {
    const d = document.getElementById("query-date").value;
    if (!d) { toast("请先选择日期", "error"); return; }
    this.queryDate = d;
    const out = document.getElementById("query-out");
    out.innerHTML = '<p class="muted">正在推演…</p>';
    try {
      const r = await Api.get(`/api/analyze/?date=${d}` +
        (force ? "&refresh=1" : ""));
      this.payload = r.payload;
      this.renderResult(out, d, r);
    } catch (e) { out.innerHTML = ""; toast(e.message, "error"); }
  },

  renderResult(out, d, meta) {
    const p = this.payload;
    out.innerHTML = "";

    // 过期横幅
    const banner = document.getElementById("stale-banner");
    banner.innerHTML = "";
    if (meta.stale) {
      banner.append(h.el("div", { class: "issue-warning" },
        "该结论所依赖的关系后来被修改，已标记过期，请重新生成。"));
    }

    // 问题摘要
    const errs = p.issues.filter(i => i.level === "error").length;
    const warns = p.issues.filter(i => i.level === "warning").length;
    out.append(h.el("div", { class: "card" },
      h.el("h2", {}, `核对日期：${d}`),
      h.el("div", { class: "row" },
        h.el("span", { class: "pill st-revoked" }, `结构错误 ${errs}`),
        h.el("span", { class: "pill st-possible" }, `提示 ${warns}`),
        h.el("span", { class: "pill st-gap" }, `缺口 ${p.gaps.length}`),
        h.el("span", { class: "muted small" },
          "点击候选中的版本、关系箭头、段落来源均可逐级回到原图依据"))));

    // 缺口
    if (p.gaps.length) {
      const gc = h.el("div", { class: "card" },
        h.el("h2", {}, "资料 / 条款缺口"));
      p.gaps.forEach(g => gc.append(h.el("div", { class: "issue-error" },
        h.el("strong", {}, "缺口 · "), g.message)));
      out.append(gc);
    }

    // 每个条款
    p.clauses.forEach(c => out.append(this.renderClause(c, d)));

    if (!p.clauses.length) {
      out.append(h.el("div", { class: "card muted" },
        "库中还没有条款版本。请先在「材料清单」录入保单与批单。"));
    }
  },

  renderClause(c, d) {
    const card = h.el("div", { class: "card" });
    const statusPill = {
      effective: ["st-active", "有适用版本"],
      revoked_gap: ["st-gap", "撤销缺口（不自动恢复旧文）"],
      no_effective_version: ["st-revoked", "该日无记载中有效版本"],
    }[c.status] || ["st-possible", c.status];

    card.append(h.el("h2", {}, c.clause_name,
      h.el("span", { class: `pill ${statusPill[0]}` }, statusPill[1]),
      h.el("span", { class: "small muted" },
        `并列候选 ${c.candidate_count} 条`),
      h.el("div", { class: "spacer" }),
      h.el("button", {
        class: "btn-ghost btn-sm",
        onclick: () => this.showGraph(c),
      }, "查看沿革图")));

    if (!c.candidates.length) {
      card.append(h.el("p", { class: "muted" },
        c.status === "no_effective_version"
          ? `在 ${d} 没有记载中有效的版本，可能缺少保单/批单材料。`
          : "无可展示链。"));
      card.append(this.graphInline(c));
      return card;
    }

    c.candidates.forEach((cand, idx) =>
      card.append(this.renderCandidate(c, cand, idx)));

    card.append(this.graphInline(c));
    return card;
  },

  renderCandidate(c, cand, idx) {
    const box = h.el("div", {
      class: `candidate ${cand.status === "revoked_gap"
        ? "revoked_gap" : cand.certainty}`,
    });
    box.append(h.el("div", { class: "row" },
      h.el("strong", {}, `候选 ${idx + 1}`),
      h.el("span", {
        class: `pill ${cand.certainty === "definite"
          ? "definite" : "certainty-possible"}`,
      }, cand.certainty === "definite" ? "日期确定" : "含不确定日期（可能）"),
      cand.status === "revoked_gap"
        ? h.el("span", { class: "pill st-revoked" }, "新版本已撤销")
        : cand.status === "not_in_force"
          ? h.el("span", { class: "pill st-expired" }, "该日未生效/已失效")
          : h.el("span", { class: "pill st-active" }, "适用"),
      h.el("div", { class: "spacer" }),
      h.el("button", {
        class: "btn-tiny btn-ghost",
        onclick: () => this.showEvidence(cand),
      }, `来源依据（${cand.evidence.length}）`)));

    if (cand.note) box.append(h.el("p", { class: "small conflict-note" }, cand.note));

    // 链：新版本 → 旧版本
    const flow = h.el("div", { class: "chain-flow" });
    cand.chain.forEach((node, i) => {
      if (i > 0) {
        const arrow = h.el("div", {
          class: "chain-arrow" +
            (node.via_certainty === "maybe" ? " uncertain" : ""),
          title: node.via_certainty === "maybe"
            ? "关系日期不确定" : "点击查看该关系及原图依据",
          onclick: () => node.via_relation_id
            && RelationDetail.open(node.via_relation_id,
              ["核对结论", c.clause_name, `候选${idx + 1}`, "修改关系"]),
        }, "➜");
        flow.append(arrow);
      }
      const n = h.el("div", {
        class: "chain-node" + (node.revoked ? " revoked" : ""),
        title: "点击查看版本段落与原图依据",
        onclick: () => VersionDetail.open(node.version_id,
          ["核对结论", c.clause_name, `候选${idx + 1}`,
           node.version_label || node.doc_file_no]),
      },
        h.el("div", { class: "cn-label" },
          node.version_label || `版本#${node.version_id}`),
        h.el("div", { class: "cn-doc" }, node.doc_file_no),
        h.el("span", {
          class: `pill st-${node.status}`,
          style: "margin-top:.2rem;",
        }, STATUS_TEXT[node.status] || node.status),
        node.revoked
          ? h.el("span", { class: "pill st-revoked" },
              node.revoked === "yes" ? "已撤销" : "可能已撤销")
          : null);
      flow.append(n);
      // 增补分支
      if (node.supplements_here?.length) {
        node.supplements_here.forEach(sid => {
          const sv = App.verById(sid);
          flow.append(h.el("div", {
            class: "supplement-branch",
            style: "cursor:pointer;",
            title: "点击查看增补版本",
            onclick: () => VersionDetail.open(sid,
              ["核对结论", c.clause_name, `候选${idx + 1}`, "增补版本"]),
          }, "＋增补：", sv ? (sv.version_label || sv.clause_name)
            : `版本#${sid}`, "（并行有效）"));
        });
      }
    });
    box.append(flow);

    // 有效段落表（局部替换后应含保留下来的旧段落）
    if (cand.paragraphs_effective.length) {
      const list = h.el("div", { style: "margin-top:.4rem;" });
      list.append(h.el("div", { class: "small muted" },
        "该候选在指定日期的有效条文（虚线框为局部替换中沿用旧版的段落）："));
      cand.paragraphs_effective.forEach(p => {
        const viaPartial = p.via_relation_id != null &&
          this._relationKind(p.via_relation_id) === "partial";
        list.append(h.el("div", {
          class: "para" + (viaPartial ? " inherited" : ""),
        },
          h.el("div", {},
            h.el("span", { class: "p-no" }, p.paragraph_no), " ", p.title,
            h.el("span", { class: "p-src" },
              "　来源：材料 #", p.document_id,
              p.via_relation_id ? `（经关系 #${p.via_relation_id} 保留）` : "")),
          h.el("div", {}, p.text)));
      });
      if (cand.paragraph_conflicts.length) {
        list.append(h.el("p", { class: "conflict-note small" },
          "增补文本与主链在段落 ",
          cand.paragraph_conflicts.join("、"),
          " 上不一致，已保留来源，请人工核对，不自动取舍。"));
      }
      box.append(list);
    } else if (cand.status === "revoked_gap") {
      box.append(h.el("p", { class: "conflict-note" },
        "该版本被撤销且不自动恢复旧文；上方链中旧版本仅用于回溯核对，" +
        "不作为自动生效结论。请在交接件中标注此缺口。"));
    }

    return box;
  },

  _relationKind(rid) {
    return App.relations.find(r => r.id === rid)?.kind;
  },

  showEvidence(cand) {
    const body = h.el("div", {});
    if (!cand.evidence.length) {
      body.append(h.el("p", { class: "muted" }, "该候选还没有框选依据。"));
    }
    const byDoc = {};
    cand.evidence.forEach(e => (byDoc[e.document_id] ||= []).push(e));
    Object.entries(byDoc).forEach(([docId, evs]) => {
      const doc = App.docById(parseInt(docId, 10));
      if (!doc) return;
      body.append(h.el("h3", {}, `${doc.file_no} ${doc.title || ""}`));
      evs.forEach(e => body.append(h.el("button", {
        class: "btn-ghost btn-sm", style: "margin:.2rem .3rem .2rem 0;",
        onclick: () => ScanModal.open(doc, evs, { focusTarget: e.target }),
      }, "🔲 ", e.label || e.target)));
    });
    Modal.open({ title: "来源依据（点击即回到原图对应位置）", body });
  },

  /* ---------------- 沿革图（SVG 分层布局） ---------------- */

  graphInline(c) {
    const wrap = h.el("div", { class: "graph-wrap", id: `graph-${c.clause_name}` });
    wrap.append(this._svg(c.graph, 300));
    const legend = h.el("div", { class: "legend" },
      h.el("span", {}, h.el("i", { style: "border-color:var(--brand)" }), "替换"),
      h.el("span", {}, h.el("i", {
        style: "border-top-style:dashed;border-color:var(--green)",
      }), "增补"),
      h.el("span", {}, h.el("i", { style: "border-color:var(--red)" }), "撤销"),
      h.el("span", {}, "虚灰线 = 已撤销的修改（不自动恢复旧文）"),
      h.el("span", {}, "橙线 = 日期不确定"),
      h.el("span", {}, "点节点/边可下钻到原图"));
    const box = h.el("details", {},
      h.el("summary", {
        style: "cursor:pointer;font-weight:600;color:var(--brand);",
      }, "条款沿革图（点击展开/收起）"),
      wrap, legend);
    return box;
  },

  showGraph(c) {
    const body = h.el("div", {},
      h.el("div", { class: "graph-wrap" }, this._svg(c.graph, 560)),
      h.el("div", { class: "legend" },
        "节点颜色：绿=确定有效，黄=可能有效，灰=已失效，蓝=尚未生效，红框=已撤销"));
    Modal.open({ title: `沿革图 · ${c.clause_name}`, body, wide: true });
  },

  _svg(graph, height) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("width", "100%");
    svg.setAttribute("height", height);
    svg.setAttribute("viewBox", `0 0 ${Math.max(640, graph.nodes.length * 210)} ${height}`);
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML =
      '<marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" ' +
      'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#115e63"/></marker>' +
      '<marker id="arrow-red" markerWidth="10" markerHeight="10" refX="8" refY="3" ' +
      'orient="auto"><path d="M0,0 L8,3 L0,6 Z" fill="#b91c1c"/></marker>';
    svg.append(defs);

    // 布局：按“最新在右”分层（沿 from→to 计算深度）
    const nodes = graph.nodes;
    const idx = Object.fromEntries(nodes.map(n => [n.id, n]));
    const outgoing = {};
    nodes.forEach(n => outgoing[n.id] = []);
    graph.edges.forEach(e => {
      if (e.from && idx[e.from] && idx[e.to || 0]) {
        outgoing[e.from].push(e.to);
      }
    });
    const depth = {};
    const calcDepth = id => {
      if (id in depth) return depth[id];
      depth[id] = 0;
      const ds = outgoing[id].map(calcDepth);
      depth[id] = (Math.max(-1, ...ds) + 1);
      return depth[id];
    };
    nodes.forEach(n => calcDepth(n.id));
    const layers = {};
    nodes.forEach(n => (layers[depth[n.id]] ||= []).push(n));
    const NW = 168, NH = 64, GX = 220, GY = 92;
    const pos = {};
    Object.entries(layers).forEach(([dep, ns]) => {
      ns.forEach((n, i) => {
        pos[n.id] = { x: 40 + dep * GX, y: 30 + i * GY };
      });
    });

    // 边
    graph.edges.forEach(e => {
      if (e.kind === "revoke_version") {
        const t = pos[e.to];
        if (!t) return;
        const g = document.createElementNS(NS, "path");
        g.setAttribute("class", "g-edge revoke" +
          (e.revoked ? " revoked" : "") +
          (e.occurred === "no" ? " future" : ""));
        g.setAttribute("d", `M ${t.x - 26} ${t.y - 10} q -34 -26 2 -44`);
        g.addEventListener("click", () => RelationDetail.open(e.id));
        svg.append(g);
        return;
      }
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      const x1 = a.x, y1 = a.y + NH / 2;
      const x2 = b.x + NW, y2 = b.y + NH / 2;
      const mx = (x1 + x2) / 2;
      const p = document.createElementNS(NS, "path");
      const cls = ["g-edge",
        e.kind === "supplement" ? "supplement" : "",
        e.kind === "revoke_relation" || e.kind === "revoke_version" ? "revoke" : "",
        e.revoked ? "revoked" : "",
        e.occurred === "maybe" ? "uncertain" : "",
        e.occurred === "no" ? "future" : ""].filter(Boolean).join(" ");
      p.setAttribute("class", cls);
      p.setAttribute("d", `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`);
      p.addEventListener("click", () => RelationDetail.open(e.id));
      svg.append(p);

      const label = document.createElementNS(NS, "text");
      label.setAttribute("x", mx);
      label.setAttribute("y", (y1 + y2) / 2 - 6);
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("fill", e.occurred === "maybe" ? "#92400e" : "#555");
      label.textContent = REL_TEXT[e.kind] + (e.revoked ? "（已撤销）" : "");
      svg.append(label);
    });

    // 节点
    nodes.forEach(n => {
      const p = pos[n.id];
      const g = document.createElementNS(NS, "g");
      g.setAttribute("class", "g-node");
      g.addEventListener("click", () => VersionDetail.open(n.id));
      const rect = document.createElementNS(NS, "rect");
      rect.setAttribute("x", p.x); rect.setAttribute("y", p.y);
      rect.setAttribute("width", NW); rect.setAttribute("height", NH);
      rect.setAttribute("rx", 10);
      const fill = n.revoked ? "#fdecec"
        : n.status === "active" ? "#e8f5ec"
        : n.status === "possible" ? "#fdf3e2"
        : n.status === "future" ? "#e8f0fb" : "#f0efeb";
      const stroke = n.revoked ? "#b91c1c"
        : n.status === "active" ? "#166534"
        : n.status === "possible" ? "#b45309"
        : n.status === "future" ? "#1e40af" : "#8a8f98";
      rect.setAttribute("fill", fill);
      rect.setAttribute("stroke", stroke);
      g.append(rect);
      const t1 = document.createElementNS(NS, "text");
      t1.setAttribute("x", p.x + 10); t1.setAttribute("y", p.y + 25);
      t1.textContent = (n.version_label || n.clause_name).slice(0, 12);
      g.append(t1);
      const t2 = document.createElementNS(NS, "text");
      t2.setAttribute("x", p.x + 10); t2.setAttribute("y", p.y + 46);
      t2.setAttribute("fill", "#666");
      t2.textContent = `${n.doc_file_no} · ${STATUS_TEXT[n.status] || n.status}`
        + (n.revoked ? " · 已撤销" : "");
      g.append(t2);
      svg.append(g);
    });
    return svg;
  },

  async exportHandoff() {
    const date = document.getElementById("query-date").value ||
      new Date().toISOString().slice(0, 10);
    window.location.href = `/api/export/?date=${date}`;
  },
};
