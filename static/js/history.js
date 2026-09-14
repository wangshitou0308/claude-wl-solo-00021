/* 操作留痕与撤销误录（后进先出） */
"use strict";

const HistoryView = {
  async render(root) {
    const [hist, concls] = await Promise.all([
      Api.get("/api/history/"),
      Api.get("/api/conclusions/"),
    ]);
    root.append(
      h.el("div", { class: "card" },
        h.el("h2", {}, "操作留痕",
          h.el("div", { class: "spacer" }),
          h.el("button", {
            class: "btn-warn",
            onclick: () => App.undoLast(),
          }, "↩ 撤销最近一步误录")),
        h.el("p", { class: "muted small" },
          "撤销按后进先出执行：只回退最新一条未撤销操作（录入↔删除、修改↔恢复原值、锁定↔解锁）。" +
          "已锁定的关系需先解锁才能被回退。撤销关系类操作只使其下游结论过期。")),
      h.el("div", { class: "card" },
        h.el("h2", {}, "已保存的核对结论"),
        h.el("p", { class: "muted small" },
          "结论按指定日期缓存；关系修改后仅受影响条款的结论标记过期。")));
    const [histCard, conclCard] = root.querySelectorAll(".card");

    const t = h.el("table", { class: "data" });
    t.append(h.el("thead", {}, h.el("tr", {},
      ["时间", "动作", "对象", "摘要", "状态"].map(x => h.el("th", {}, x)))));
    const tb = h.el("tbody");
    hist.items.forEach(a => tb.append(h.el("tr", { class: a.undone ? "deleted" : "" },
      h.el("td", { class: "small" }, a.created_at.replace("T", " ")),
      h.el("td", {}, a.action_text),
      h.el("td", { class: "small" }, `${a.entity_type} #${a.entity_id}`),
      h.el("td", {}, a.summary),
      h.el("td", {}, a.undone ? h.el("span", { class: "muted small" }, "已撤销")
        : h.el("span", { class: "pill st-active" }, "有效")))));
    if (!hist.items.length) tb.append(h.el("tr", {}, h.el("td", {
      colspan: 5, class: "muted", style: "text-align:center;padding:1.2rem;",
    }, "暂无操作记录")));
    t.append(tb);
    histCard.append(h.el("div", { style: "overflow-x:auto;" }, t));

    const t2 = h.el("table", { class: "data" });
    t2.append(h.el("thead", {}, h.el("tr", {},
      ["核对日期", "条款数", "生成时间", "状态", "操作"].map(x => h.el("th", {}, x)))));
    const tb2 = h.el("tbody");
    concls.items.forEach(c => tb2.append(h.el("tr", {},
      h.el("td", {}, c.query_date),
      h.el("td", {}, String(c.clause_count)),
      h.el("td", { class: "small" }, c.created_at.replace("T", " ")),
      h.el("td", {}, c.stale
        ? h.el("span", { class: "pill st-possible" },
            "已过期：" + (c.stale_reason || "依赖变更"))
        : h.el("span", { class: "pill st-active" }, "最新")),
      h.el("td", {},
        h.el("button", {
          class: "btn-tiny btn-ghost",
          onclick: async () => {
            await Api.post(`/api/conclusions/${c.id}/refresh/`, {});
            toast("结论已按当前数据重新生成", "ok");
            App.show("history");
          },
        }, "重新生成")))));
    if (!concls.items.length) tb2.append(h.el("tr", {}, h.el("td", {
      colspan: 5, class: "muted", style: "text-align:center;padding:1.2rem;",
    }, "还没有生成过核对结论")));
    t2.append(tb2);
    conclCard.append(h.el("div", { style: "overflow-x:auto;" }, t2));
  },
};
