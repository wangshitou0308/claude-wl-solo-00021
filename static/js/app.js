/* 主应用：导航、材料/版本/关系管理、表单 */
"use strict";

const App = {
  docs: [],
  versions: [],
  relations: [],

  async init() {
    document.querySelectorAll("header.topbar nav button").forEach(btn => {
      btn.addEventListener("click", () => this.show(btn.dataset.view));
    });
    document.getElementById("undo-btn")
      .addEventListener("click", () => this.undoLast());
    await this.refreshAll();
    this.show("documents");
  },

  async refreshAll() {
    await Promise.all([this.loadDocs(), this.loadVersions(), this.loadRelations()]);
    await this.refreshOverviewBadge();
  },

  async refreshOverviewBadge() {
    try {
      const o = await Api.get("/api/overview/");
      const el = document.getElementById("issue-badge");
      el.textContent = o.error_count + o.warning_count;
      el.className = "badge " +
        (o.error_count ? "" : o.warning_count ? "warn" : "zero");
      return o;
    } catch (e) { return {}; }
  },

  async loadDocs() {
    this.docs = (await Api.get("/api/documents/")).items;
  },
  async loadVersions() {
    this.versions = (await Api.get("/api/versions/")).items;
  },
  async loadRelations() {
    this.relations = (await Api.get("/api/relations/")).items;
  },

  docById(id) { return this.docs.find(d => d.id === id); },
  verById(id) { return this.versions.find(v => v.id === id); },

  async show(view) {
    document.querySelectorAll("header.topbar nav button")
      .forEach(b => b.classList.toggle("active", b.dataset.view === view));
    const root = document.getElementById("view-root");
    root.innerHTML = "";
    if (view === "documents") await this.viewDocuments(root);
    if (view === "relations") await this.viewRelations(root);
    if (view === "checks") await ChecksView.render(root);
    if (view === "query") await QueryView.render(root);
    if (view === "history") await HistoryView.render(root);
  },

  /* ----------------------------- 材料列表 ----------------------------- */
  async viewDocuments(root) {
    root.append(
      h.el("div", { class: "card" },
        h.el("h2", {}, "材料清单",
          h.el("div", { class: "spacer" }),
          h.el("button", { onclick: () => DocumentForm.open(null) }, "＋ 录入新材料")),
        h.el("p", { class: "muted small" },
          "支持保单、续期通知、批单及其他纸质材料。看不清的日期请填写“最早～最晚”范围。"),
        h.el("div", { style: "overflow-x:auto;" },
          h.el("table", { class: "data", id: "docs-table" }))));
    const table = root.querySelector("#docs-table");
    table.append(h.el("thead", {}, h.el("tr", {},
      ["文件编号", "名称", "类型", "签发日期范围", "生效区间", "条款版本", "操作"]
        .map(t => h.el("th", {}, t)))));
    const tbody = h.el("tbody");
    for (const d of this.docs) {
      const vs = this.versions.filter(v => v.document_id === d.id);
      tbody.append(h.el("tr", {},
        h.el("td", {}, h.el("strong", {}, d.file_no)),
        h.el("td", {}, d.title || "—"),
        h.el("td", {}, h.el("span", {
          class: `pill kind-${d.kind}`,
        }, KIND_TEXT[d.kind] || d.kind)),
        h.el("td", {}, h.rangeText(d.issued_earliest, d.issued_latest)),
        h.el("td", { class: "small" },
          h.spanText(d.effective_start_earliest, d.effective_start_latest,
                     d.effective_end_earliest, d.effective_end_latest)),
        h.el("td", {}, vs.length
          ? vs.map(v => h.el("div", { class: "small" },
              `${v.clause_name}${v.version_label ? "（" + v.version_label + "）" : ""}`))
          : "—"),
        h.el("td", {},
          h.el("div", { class: "row", style: "gap:.3rem;" },
            d.scan ? h.el("button", {
              class: "btn-ghost btn-sm",
              onclick: async () => {
                const full = await Api.get(`/api/documents/${d.id}/`);
                ScanModal.open(d, full.regions || [], {});
              },
            }, "看图") : null,
            h.el("button", {
              class: "btn-ghost btn-sm",
              onclick: () => DocumentDetail.open(d.id),
            }, "详情"),
            h.el("button", {
              class: "btn-ghost btn-sm",
              onclick: () => DocumentForm.open(d),
            }, "修改"),
            h.el("button", {
              class: "btn-danger btn-sm",
              onclick: () => this.deleteDoc(d),
            }, "撤销录入")))));
    }
    table.append(tbody);
    if (!this.docs.length) {
      tbody.append(h.el("tr", {}, h.el("td", {
        colspan: 7, class: "muted", style: "text-align:center;padding:1.4rem;",
      }, "还没有材料，点右上角「＋ 录入新材料」开始。")));
    }
  },

  async deleteDoc(d) {
    if (!confirm(`撤销录入材料 ${d.file_no}？\n（材料及其版本/关系会从核对中隐藏，可在「操作留痕」中恢复）`)) return;
    try {
      await Api.del(`/api/documents/${d.id}/`);
      toast("已撤销录入", "ok");
      await this.refreshAll();
      this.show("documents");
    } catch (e) { toast(e.message, "error"); }
  },

  async undoLast() {
    try {
      const r = await Api.post("/api/undo/", {});
      toast(`已撤销：${r.undone.summary}`, "ok");
      await this.refreshAll();
      const active = document.querySelector("header.topbar nav button.active");
      if (active) this.show(active.dataset.view);
    } catch (e) { toast(e.message, "error"); }
  },

  /* ----------------------------- 关系列表 ----------------------------- */
  async viewRelations(root) {
    root.append(h.el("div", { class: "card" },
      h.el("h2", {}, "替换 / 增补 / 撤销关系",
        h.el("div", { class: "spacer" }),
        h.el("button", {
          onclick: () => { if (!this.docs.length) toast("请先录入材料", "error");
            else RelationForm.open(null); },
        }, "＋ 录入关系")),
      h.el("p", { class: "muted small" },
        "关系即沿革图中的边。局部替换需勾选涉及段落；撤销只废止，不自动恢复旧文。")));
    const wrap = root.querySelector(".card");
    const table = h.el("table", { class: "data" });
    table.append(h.el("thead", {}, h.el("tr", {},
      ["类型", "来源版本（新）", "目标（旧/被撤销）", "依据材料", "关系日期", "涉及段落", "锁定", "操作"]
        .map(t => h.el("th", {}, t)))));
    const tbody = h.el("tbody");
    for (const r of this.relations) {
      const target = r.kind === "revoke_relation"
        ? `关系 #${r.target_relation_id}` : (r.to_label || "—");
      tbody.append(h.el("tr", {},
        h.el("td", {}, REL_TEXT[r.kind] || r.kind),
        h.el("td", {}, r.from_label || "—"),
        h.el("td", {}, target),
        h.el("td", { class: "small" }, r.document_file_no),
        h.el("td", { class: "small" },
          r.date_lo || r.date_hi ? h.rangeText(r.date_lo, r.date_hi) : "未填"),
        h.el("td", { class: "small" },
          r.affected_paragraph_ids?.length
            ? `${r.affected_paragraph_ids.length} 段` : ""),
        h.el("td", {}, r.locked ? "🔒 已锁定" : ""),
        h.el("td", {}, h.el("div", { class: "row", style: "gap:.3rem;" },
          h.el("button", {
            class: "btn-ghost btn-sm",
            onclick: () => RelationDetail.open(r.id),
          }, "详情"),
          h.el("button", {
            class: "btn-ghost btn-sm",
            onclick: () => RelationForm.open(r),
          }, "修改"),
          h.el("button", {
            class: r.locked ? "btn-warn btn-sm" : "btn-ghost btn-sm",
            onclick: () => this.toggleLock(r),
          }, r.locked ? "解锁" : "锁定"),
          h.el("button", {
            class: "btn-danger btn-sm",
            onclick: () => this.deleteRelation(r),
          }, "撤销录入")))));
    }
    table.append(tbody);
    wrap.append(h.el("div", { style: "overflow-x:auto;" }, table));
    if (!this.relations.length) {
      tbody.append(h.el("tr", {}, h.el("td", {
        colspan: 8, class: "muted", style: "text-align:center;padding:1.4rem;",
      }, "还没有沿革关系。")));
    }
  },

  async toggleLock(r) {
    try {
      await Api.post(`/api/relations/${r.id}/lock/`, { locked: !r.locked });
      toast(r.locked ? "已解锁" : "已锁定确认", "ok");
      await this.refreshAll();
      this.show("relations");
    } catch (e) { toast(e.message, "error"); }
  },

  async deleteRelation(r) {
    if (r.locked) { toast("该关系已锁定，请先解锁", "error"); return; }
    if (!confirm(`撤销录入关系 #${r.id}（${REL_TEXT[r.kind]}）？`)) return;
    try {
      await Api.del(`/api/relations/${r.id}/`);
      toast("已撤销录入；受影响条款的历史结论已标记过期", "ok");
      await this.refreshAll();
      this.show("relations");
    } catch (e) { toast(e.message, "error"); }
  },

  /* 点击框选依据时，按 target 跳到对应条目（供逐级回溯） */
  openTarget(target) {
    const [kind, idStr] = target.split(":");
    const id = parseInt(idStr, 10);
    if (kind === "version") VersionDetail.open(id);
    else if (kind === "relation") RelationDetail.open(id);
    else if (kind === "document") {
      const d = this.docById(id);
      if (d) ScanModal.open(d, [], {});
    }
  },
};

/* 日期范围双输入字段 */
function rangeFields(label, nameLo, nameHi, lo, hi) {
  return h.el("label", { class: "field" },
    h.el("b", {}, label),
    h.el("div", { class: "range2" },
      h.el("input", { type: "date", name: nameLo, value: h.dateInput(lo) }),
      h.el("span", { class: "muted" }, "～"),
      h.el("input", { type: "date", name: nameHi, value: h.dateInput(hi) })));
}

function collectRange(form, nameLo, nameHi) {
  return {
    [nameLo]: form.elements[nameLo]?.value || null,
    [nameHi]: form.elements[nameHi]?.value || null,
  };
}
