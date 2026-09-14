/* 材料 / 版本 / 段落 / 关系 的录入与编辑表单、详情面板 */
"use strict";

/* ---------------- 材料表单 ---------------- */
const DocumentForm = {
  open(doc) {
    const isEdit = !!doc;
    const form = h.el("form", {},
      h.el("div", { class: "grid" },
        h.el("label", { class: "field" }, h.el("b", {}, "文件编号 *"),
          h.el("input", { name: "file_no", required: true,
            value: doc?.file_no || "" })),
        h.el("label", { class: "field" }, h.el("b", {}, "材料名称"),
          h.el("input", { name: "title", value: doc?.title || "" })),
        h.el("label", { class: "field" }, h.el("b", {}, "材料类型"),
          (() => {
            const s = h.el("select", { name: "kind" });
            Object.entries(KIND_TEXT).forEach(([k, v]) =>
              s.append(h.el("option", {
                value: k, ...(doc?.kind === k ? { selected: true } : {}),
              }, v)));
            return s;
          })()),
      ),
      h.el("h3", {}, "签发日期（看不清就填最早～最晚范围）"),
      h.el("div", { class: "grid" },
        rangeFields("签发日期范围", "issued_earliest", "issued_latest",
          doc?.issued_earliest, doc?.issued_latest)),
      h.el("h3", {}, "生效区间（端点不明确同样填范围；一直有效则不填“止”）"),
      h.el("div", { class: "grid" },
        rangeFields("生效起", "effective_start_earliest",
          "effective_start_latest",
          doc?.effective_start_earliest, doc?.effective_start_latest),
        rangeFields("生效止", "effective_end_earliest",
          "effective_end_latest",
          doc?.effective_end_earliest, doc?.effective_end_latest)),
      h.el("label", { class: "field", style: "margin-top:.6rem;" },
        h.el("b", {}, "扫描图（本机文件，仅存本机 media/ 目录）"),
        h.el("input", { type: "file", name: "scan", accept: "image/*" })),
      h.el("label", { class: "field", style: "margin-top:.6rem;" },
        h.el("b", {}, "备注"),
        h.el("textarea", { name: "note", rows: 2 }, doc?.note || "")),
      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", { type: "submit" }, isEdit ? "保存修改" : "录入材料"),
        h.el("button", {
          type: "button", class: "btn-ghost",
          onclick: () => Modal.back(),
        }, "取消")));

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const fd = new FormData(form);
      // 日期范围
      ["issued", "effective_start", "effective_end"].forEach(prefix => {
        const lo = form.elements[`${prefix}_earliest`]?.value;
        const hi = form.elements[`${prefix}_latest`]?.value;
        fd.set(`${prefix}_earliest`, lo || "");
        fd.set(`${prefix}_latest`, hi || "");
      });
      try {
        if (isEdit) await Api._req("PATCH", `/api/documents/${doc.id}/`, fd);
        else await Api._req("POST", "/api/documents/", fd);
        toast(isEdit ? "已保存" : "材料已录入", "ok");
        Modal.closeAll();
        await App.refreshAll();
        App.show("documents");
      } catch (err) { toast(err.message, "error"); }
    });

    Modal.open({
      title: isEdit ? `修改材料 ${doc.file_no}` : "录入新材料",
      body: form,
    });
  },
};

/* ---------------- 材料详情（含版本、框选） ---------------- */
const DocumentDetail = {
  async open(id) {
    const d = await Api.get(`/api/documents/${id}/`);
    const body = h.el("div", {},
      h.el("div", { class: "row" },
        h.el("span", { class: `pill kind-${d.kind}` },
          KIND_TEXT[d.kind] || d.kind),
        h.el("strong", {}, d.file_no), d.title),
      h.el("p", { class: "small muted" },
        `签发：${h.rangeText(d.issued_earliest, d.issued_latest)}　生效：`,
        h.spanText(d.effective_start_earliest, d.effective_start_latest,
          d.effective_end_earliest, d.effective_end_latest)),
      d.scan ? h.el("div", { class: "row", style: "margin:.5rem 0;" },
        h.el("button", {
          onclick: () => ScanModal.open(d, d.regions,
            { selectTarget: `document:${d.id}` }),
        }, "🖼 打开扫描图 / 框选依据"),
        h.el("span", { class: "small muted" },
          `已有 ${d.regions.length} 个框选`))
        : h.el("p", { class: "muted small" }, "尚未上传扫描图"),

      h.el("h3", {}, "材料中的条款版本"),
      (() => {
        const list = h.el("div");
        (d.versions || []).forEach(v => list.append(
          h.el("div", { class: "row", style: "margin:.3rem 0;" },
            h.el("button", {
              class: "btn-ghost btn-sm",
              onclick: () => VersionDetail.open(v.id),
            }, `${v.clause_name}${v.version_label ? "（" + v.version_label + "）" : ""}`),
            h.el("span", { class: "small muted" },
              `${v.paragraphs.length} 个段落`))));
        if (!d.versions?.length) list.append(h.el("p", { class: "muted small" },
          "暂无条款版本"));
        return list;
      })(),
      h.el("button", {
        class: "btn-sm",
        onclick: () => VersionForm.open(id),
      }, "＋ 在本材料下录入条款版本"),

      h.el("h3", {}, "以本材料为依据的关系"),
      (() => {
        const list = h.el("div");
        (d.relations || []).forEach(r => list.append(h.el("div", {
          class: "small", style: "margin:.2rem 0;",
        }, `#${r.id} ${REL_TEXT[r.kind]}`)));
        if (!d.relations?.length) list.append(h.el("p", {
          class: "muted small",
        }, "暂无"));
        return list;
      })(),

      h.el("h3", {}, "框选依据"),
      RegionList.render(d.regions),

      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", {
          onclick: () => DocumentForm.open(App.docById(id)),
        }, "修改材料信息"),
        h.el("button", {
          class: "btn-danger",
          onclick: async () => {
            if (confirm("撤销录入该材料？")) {
              await App.deleteDoc(App.docById(id));
              Modal.closeAll();
            }
          },
        }, "撤销录入")));

    Modal.open({ title: `材料 ${d.file_no}`, body, wide: true });
  },
};

/* ---------------- 框选列表 ---------------- */
const RegionList = {
  render(regions) {
    const wrap = h.el("div");
    if (!regions.length) {
      wrap.append(h.el("p", { class: "muted small" },
        "暂无框选。打开扫描图后点「框选依据」，在图上拖出矩形即可。"));
      return wrap;
    }
    regions.forEach(g => wrap.append(h.el("div", {
      class: "row small", style: "margin:.2rem 0;",
    }, "🔲 ", h.el("code", {}, g.target), " ", g.label || "",
      h.el("div", { class: "spacer" }),
      h.el("button", {
        class: "btn-tiny btn-danger",
        onclick: async () => {
          if (!confirm("删除该框选？")) return;
          await Api.del(`/api/regions/${g.id}/`);
          toast("框选已删除", "ok");
          Modal.closeAll();
        },
      }, "删除"))));
    return wrap;
  },
};

/* ---------------- 条款版本表单 / 详情 ---------------- */
const VersionForm = {
  open(docId, version = null) {
    const form = h.el("form", {},
      h.el("div", { class: "grid" },
        h.el("label", { class: "field" }, h.el("b", {}, "险种 / 条款名称 *"),
          h.el("input", {
            name: "clause_name", required: true, list: "clause-names",
            value: version?.clause_name || "",
          })),
        h.el("datalist", { id: "clause-names" },
          [...new Set(App.versions.map(v => v.clause_name))]
            .map(n => h.el("option", { value: n }))),
        h.el("label", { class: "field" }, h.el("b", {}, "版本标识（可留空）"),
          h.el("input", {
            name: "version_label",
            placeholder: "如 1999版 / 批改后版",
            value: version?.version_label || "",
          }))),
      h.el("label", { class: "field", style: "margin-top:.6rem;" },
        h.el("b", {}, "备注"),
        h.el("textarea", { name: "note", rows: 2 }, version?.note || "")),
      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", { type: "submit" },
          version ? "保存修改" : "录入条款版本"),
        h.el("button", {
          type: "button", class: "btn-ghost",
          onclick: () => Modal.back(),
        }, "取消")));

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const payload = Object.fromEntries(new FormData(form).entries());
      try {
        if (version) {
          await Api.patch(`/api/versions/${version.id}/`, payload);
        } else {
          payload.document_id = docId;
          await Api.post("/api/versions/", payload);
        }
        toast("已保存", "ok");
        await App.refreshAll();
        Modal.back();
        // 回到材料详情
        DocumentDetail.open(version?.document_id || docId);
      } catch (err) { toast(err.message, "error"); }
    });
    Modal.open({
      title: version ? `修改版本 ${version.clause_name}` : "录入条款版本",
      body: form,
    });
  },
};

const VersionDetail = {
  async open(id, crumbs) {
    let v;
    try {
      v = await Api.get(`/api/versions/${id}/`);
    } catch (e) { toast(e.message, "error"); return; }
    const doc = App.docById(v.document_id) || { id: v.document_id,
      file_no: v.doc_file_no, title: "", scan: null };
    const docRegions = doc.id ? (await Api.get(
      `/api/documents/${doc.id}/`)).regions : [];
    const body = h.el("div", {},
      h.el("div", { class: "row" },
        h.el("strong", {}, v.clause_name),
        v.version_label ? h.el("span", { class: "pill kind-other" },
          v.version_label) : null,
        h.el("span", { class: "small muted" }, `来源材料：${v.doc_file_no}`)),
      h.el("div", { class: "row", style: "margin:.5rem 0;" },
        doc.scan ? h.el("button", {
          class: "btn-sm",
          onclick: () => ScanModal.open(doc,
            [...(docRegions || []), ...(v.regions || [])],
            { selectTarget: `version:${v.id}`, focusTarget: `version:${v.id}` }),
        }, "🖼 在扫描图上定位 / 框选本版本依据") :
          h.el("span", { class: "muted small" }, "来源材料未上传扫描图")),

      h.el("h3", {}, "段落（局部替换按“段落号”对齐；未涉及段落会被保留）"),
      (() => {
        const list = h.el("div");
        v.paragraphs.forEach(p => list.append(h.el("div", {
          class: "para",
        }, h.el("div", {},
          h.el("span", { class: "p-no" }, p.paragraph_no), " ",
          p.title, h.el("div", { class: "spacer" })),
          h.el("div", {}, p.text),
          h.el("div", { class: "row", style: "margin-top:.3rem;" },
            doc.scan ? h.el("button", {
              class: "btn-tiny btn-ghost",
              onclick: () => ScanModal.open(doc,
                [...(docRegions || []), ...(v.regions || [])],
                { selectTarget: `version:${v.id}:paragraph:${p.id}`,
                  focusTarget: `version:${v.id}:paragraph:${p.id}` }),
            }, "框选本段落依据") : null,
            h.el("button", {
              class: "btn-tiny btn-ghost",
              onclick: () => ParagraphForm.open(v, p, list),
            }, "改文本"),
            h.el("button", {
              class: "btn-tiny btn-danger",
              onclick: async () => {
                if (!confirm(`删除段落 ${p.paragraph_no}？`)) return;
                await Api.del(`/api/paragraphs/${p.id}/`);
                toast("段落已删除", "ok");
                Modal.back();
                VersionDetail.open(v.id);
              },
            }, "删除")))));
        if (!v.paragraphs.length) list.append(h.el("p", {
          class: "muted small",
        }, "还没有段落，建议把条款拆成带段落号的条目（如 第一条 / 第三条第2款）。"));
        return list;
      })(),
      h.el("button", {
        class: "btn-sm", style: "margin-top:.5rem;",
        onclick: () => ParagraphForm.open(v, null, null),
      }, "＋ 新增段落"),

      h.el("h3", {}, "框选依据"),
      RegionList.render(v.regions || []),

      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", {
          onclick: () => VersionForm.open(v.document_id, v),
        }, "修改版本信息"),
        h.el("button", {
          class: "btn-danger",
          onclick: async () => {
            if (confirm("撤销录入该条款版本？")) {
              await Api.del(`/api/versions/${v.id}/`);
              toast("已撤销录入", "ok");
              Modal.closeAll();
              await App.refreshAll();
            }
          },
        }, "撤销录入")));

    Modal.open({ title: `条款版本 · ${v.clause_name}`, body, wide: true,
                 crumbs: crumbs || ["核对结论", v.clause_name] });
  },
};

const ParagraphForm = {
  open(version, paragraph, _list) {
    const form = h.el("form", {},
      h.el("div", { class: "grid" },
        h.el("label", { class: "field" }, h.el("b", {}, "段落号 *"),
          h.el("input", {
            name: "paragraph_no", required: true,
            value: paragraph?.paragraph_no || "",
            placeholder: "第一条 / 3.2 / 责任免除第2项",
          })),
        h.el("label", { class: "field" }, h.el("b", {}, "段落标题"),
          h.el("input", { name: "title", value: paragraph?.title || "" }))),
      h.el("label", { class: "field", style: "margin-top:.6rem;" },
        h.el("b", {}, "段落文本"),
        h.el("textarea", { name: "text", rows: 6 }, paragraph?.text || "")),
      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", { type: "submit" }, paragraph ? "保存段落" : "新增段落"),
        h.el("button", {
          type: "button", class: "btn-ghost",
          onclick: () => Modal.back(),
        }, "取消")));
    form.addEventListener("submit", async e => {
      e.preventDefault();
      const payload = Object.fromEntries(new FormData(form).entries());
      try {
        if (paragraph) {
          await Api.patch(`/api/paragraphs/${paragraph.id}/`, payload);
        } else {
          payload.version_id = version.id;
          await Api.post("/api/paragraphs/", payload);
        }
        toast("段落已保存", "ok");
        Modal.back();
        VersionDetail.open(version.id);
      } catch (err) { toast(err.message, "error"); }
    });
    Modal.open({
      title: paragraph ? `修改段落 ${paragraph.paragraph_no}` : "新增段落",
      body: form,
    });
  },
};

/* ---------------- 关系表单 / 详情 ---------------- */
const RelationForm = {
  async open(relation) {
    const isEdit = !!relation;
    // 编辑时取详情，拿到涉及段落与各日期字段
    if (isEdit) {
      relation = { ...relation, ...(await Api.get(
        `/api/relations/${relation.id}/`)) };
    }
    const kindSel = h.el("select", { name: "kind" });
    Object.entries(REL_TEXT).forEach(([k, v]) =>
      kindSel.append(h.el("option", {
        value: k, ...(relation?.kind === k ? { selected: true } : {}),
      }, v)));

    const verSel = (name, val) => {
      const s = h.el("select", { name });
      s.append(h.el("option", { value: "" }, "— 不指定 —"));
      App.versions.forEach(v => s.append(h.el("option", {
        value: v.id, ...(val === v.id ? { selected: true } : {}),
      }, `${v.clause_name}（${v.version_label || App.docById(v.document_id)?.file_no || "#" + v.document_id}）`)));
      return s;
    };
    const docSel = h.el("select", { name: "document_id" });
    App.docs.forEach(d => docSel.append(h.el("option", {
      value: d.id,
      ...((relation?.document_id || App.docs[0]?.id) === d.id
        ? { selected: true } : {}),
    }, `${d.file_no} ${d.title || ""}`)));

    const fromSel = verSel("from_version_id", relation?.from_version_id);
    const toSel = verSel("to_version_id", relation?.to_version_id);
    const relSel = h.el("select", { name: "target_relation_id" });
    relSel.append(h.el("option", { value: "" }, "—"));
    App.relations.filter(r => !["revoke_version", "revoke_relation"]
      .includes(r.kind)).forEach(r => relSel.append(h.el("option", {
      value: r.id,
      ...(relation?.target_relation_id === r.id ? { selected: true } : {}),
    }, `#${r.id} ${REL_TEXT[r.kind]} ${r.from_label || ""} → ${r.to_label || ""}`)));

    const partialBox = h.el("div", {});

    async function refreshPartialBox() {
      partialBox.innerHTML = "";
      if (kindSel.value !== "partial") return;
      const vid = parseInt(fromSel.value, 10);
      const v = App.verById(vid);
      if (!v) {
        partialBox.append(h.el("p", { class: "muted small" },
          "请先选择新版本（局部替换只覆盖新版本中勾选的段落号）"));
        return;
      }
      let full = v;
      if (!v.paragraphs) {
        full = (await Api.get(`/api/versions/${vid}/`));
      }
      const checked = new Set(relation?.affected_paragraph_ids || []);
      const box = h.el("div", { class: "card", style: "padding:.6rem;" },
        h.el("b", { class: "small" },
          "勾选新版本中【涉及替换】的段落（未勾段落保留旧文）："));
      full.paragraphs.forEach(p => {
        box.append(h.el("label", {
          class: "row small", style: "margin:.2rem 0;",
        }, h.el("input", {
          type: "checkbox", name: "affected", value: p.id,
          ...(checked.has(p.id) ? { checked: true } : {}),
        }), `${p.paragraph_no} ${p.title}`));
      });
      if (!full.paragraphs.length) box.append(h.el("p", {
        class: "muted small",
      }, "新版本还没有段落"));
      partialBox.append(box);
    }
    fromSel.addEventListener("change", refreshPartialBox);
    kindSel.addEventListener("change", refreshPartialBox);

    const form = h.el("form", {},
      h.el("div", { class: "grid" },
        h.el("label", { class: "field" }, h.el("b", {}, "关系类型"), kindSel),
        h.el("label", { class: "field" }, h.el("b", {}, "依据材料（批单等）"),
          docSel)),
      h.el("div", { class: "grid", style: "margin-top:.6rem;" },
        h.el("label", { class: "field" },
          h.el("b", {}, "来源版本（新版本；撤销关系可留空）"), fromSel),
        h.el("label", { class: "field" },
          h.el("b", {}, "目标版本（被替换/增补/撤销的旧版本）"), toSel),
        h.el("label", { class: "field" },
          h.el("b", {}, "被撤销的关系（仅“撤销一条修改”）"), relSel)),
      partialBox,
      h.el("label", { class: "field", style: "margin-top:.6rem;" },
        h.el("b", {}, "修改说明"),
        h.el("input", {
          name: "description", value: relation?.description || "",
          placeholder: "如：将第三条责任免除中第2项改为…",
        })),
      h.el("h3", {}, "关系发生日期 / 生效区间（看不清填范围）"),
      h.el("div", { class: "grid" },
        rangeFields("关系发生日期", "date_earliest", "date_latest",
          relation?.date_lo, relation?.date_hi),
        rangeFields("生效起", "eff_start_earliest", "eff_start_latest",
          relation?.eff_start_lo, relation?.eff_start_hi),
        rangeFields("生效止", "eff_end_earliest", "eff_end_latest",
          relation?.eff_end_lo, relation?.eff_end_hi)),
      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", { type: "submit" }, isEdit ? "保存修改" : "录入关系"),
        h.el("button", {
          type: "button", class: "btn-ghost",
          onclick: () => Modal.back(),
        }, "取消")));

    refreshPartialBox();

    form.addEventListener("submit", async e => {
      e.preventDefault();
      const fd = new FormData(form);
      const payload = Object.fromEntries(fd.entries());
      payload.affected_paragraph_ids =
        [...form.querySelectorAll("input[name=affected]:checked")]
          .map(i => parseInt(i.value, 10));
      ["date", "eff_start", "eff_end"].forEach(prefix => {
        payload[`${prefix}_earliest`] =
          form.elements[`${prefix}_earliest`]?.value || null;
        payload[`${prefix}_latest`] =
          form.elements[`${prefix}_latest`]?.value || null;
      });
      ["from_version_id", "to_version_id", "target_relation_id",
       "document_id"].forEach(k => {
        payload[k] = payload[k] ? parseInt(payload[k], 10) : null;
      });
      try {
        if (isEdit) {
          await Api.patch(`/api/relations/${relation.id}/`, payload);
        } else {
          await Api.post("/api/relations/", payload);
        }
        toast(isEdit
          ? "关系已保存；受影响的下游结论已标记过期" : "关系已录入", "ok");
        Modal.closeAll();
        await App.refreshAll();
        App.show("relations");
      } catch (err) { toast(err.message, "error"); }
    });

    Modal.open({ title: isEdit ? `修改关系 #${relation.id}` : "录入沿革关系",
                 body: form, wide: true });
  },
};

const RelationDetail = {
  async open(id, crumbs) {
    let r;
    try {
      r = await Api.get(`/api/relations/${id}/`);
    } catch (e) { toast(e.message, "error"); return; }
    const doc = App.docById(r.document_id);
    const body = h.el("div", {},
      h.el("div", { class: "row" },
        h.el("strong", {}, REL_TEXT[r.kind] || r.kind),
        r.locked ? h.el("span", { class: "pill st-active" }, "🔒 已锁定") : null,
        h.el("span", { class: "small muted" },
          `依据材料 ${r.document_file_no}`)),
      h.el("p", { class: "small" },
        `来源（新）：${r.from_label || "—"}　→　目标：${r.to_label || "—"}`,
        r.target_relation_id ? h.el("br") : null,
        r.target_relation_id
          ? `被撤销关系：#${r.target_relation_id}` : null),
      r.description ? h.el("p", {}, r.description) : null,
      h.el("p", { class: "small muted" },
        "发生日期：", h.rangeText(r.date_lo, r.date_hi)),
      r.affected_paragraphs?.length ? h.el("div", {},
        h.el("b", { class: "small" }, "涉及段落："),
        h.el("span", { class: "small" },
          r.affected_paragraphs.map(p => p.paragraph_no).join("、"))) : null,

      doc?.scan ? h.el("button", {
        class: "btn-sm", style: "margin:.5rem 0;",
        onclick: async () => {
          const dfull = await Api.get(`/api/documents/${doc.id}/`);
          ScanModal.open(doc,
            [...(dfull.regions || []), ...(r.regions || [])],
            { selectTarget: `relation:${r.id}`,
              focusTarget: `relation:${r.id}` });
        },
      }, "🖼 在扫描图上定位 / 框选本关系依据") :
        h.el("p", { class: "muted small" }, "依据材料未上传扫描图"),

      h.el("h3", {}, "框选依据"),
      RegionList.render(r.regions || []),

      h.el("div", { class: "row", style: "margin-top:1rem;" },
        h.el("button", {
          onclick: () => {
            const flat = App.relations.find(x => x.id === id);
            RelationForm.open(flat);
          },
        }, "修改关系"),
        h.el("button", {
          class: r.locked ? "btn-warn" : "btn-ghost",
          onclick: () => App.toggleLock(App.relations.find(x => x.id === id)),
        }, r.locked ? "解锁" : "锁定确认"),
        h.el("button", {
          class: "btn-danger",
          onclick: async () => {
            if (r.locked) { toast("已锁定，请先解锁", "error"); return; }
            if (confirm("撤销录入该关系？")) {
              await App.deleteRelation(App.relations.find(x => x.id === id));
              Modal.closeAll();
            }
          },
        }, "撤销录入")));

    Modal.open({ title: `关系 #${r.id}`, body, wide: true,
                 crumbs: crumbs || ["核对结论", `关系 #${r.id}`] });
  },
};
