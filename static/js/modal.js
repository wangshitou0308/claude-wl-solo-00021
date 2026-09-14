/* 弹窗管理：面板栈支持"点击结论 → 关系 → 版本 → 原图"逐级进入 / 返回 */
"use strict";

const Modal = {
  stack: [],

  open({ title, body, footer, wide = false, crumbs = null }) {
    let mask = document.getElementById("modal-mask");
    if (!mask) {
      mask = h.el("div", { id: "modal-mask", class: "modal-mask" });
      document.body.append(mask);
      mask.addEventListener("click", e => {
        if (e.target === mask) this.closeAll();
      });
    }
    const m = h.el("div", { class: "modal" + (wide ? " wide" : ""), role: "dialog" });
    const crumbBar = h.el("div", { class: "crumbs" });
    this._renderCrumbs(crumbBar, crumbs);
    m.append(
      h.el("header", {},
        h.el("h3", {}, title),
        h.el("div", { class: "spacer" }),
        h.el("button", {
          class: "btn-ghost btn-sm",
          onclick: () => this.back(),
          title: "返回上一层",
        }, "← 返回"),
        h.el("button", {
          class: "btn-ghost btn-sm",
          onclick: () => this.closeAll(),
          title: "关闭",
        }, "✕")),
      crumbBar,
      h.el("div", { class: "body" }, body),
      footer ? h.el("footer", {}, footer) : h.el("footer", {
        class: "hidden",
      })
    );
    mask.append(m);
    mask.classList.remove("hidden");
    mask.style.display = "flex";
    this.stack.push(m);
    this._renderCrumbs(crumbBar, crumbs);
    return m;
  },

  _renderCrumbs(bar, crumbs) {
    bar.innerHTML = "";
    const depth = this.stack.length;
    if (depth === 0 && !crumbs) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
    const items = crumbs || ["核对结论"];
    items.forEach((label, i) => {
      if (i > 0) bar.append(h.el("span", { class: "sep" }, "›"));
      const last = i === items.length - 1;
      bar.append(last
        ? h.el("span", { class: "muted small" }, label)
        : h.el("button", { onclick: () => this.popTo(i) }, label));
    });
  },

  popTo(level) {
    while (this.stack.length > level + 1) this._pop();
  },

  back() {
    if (this.stack.length > 1) this._pop();
    else this.closeAll();
  },

  _pop() {
    const m = this.stack.pop();
    m?.remove();
  },

  closeAll() {
    this.stack = [];
    const mask = document.getElementById("modal-mask");
    if (mask) { mask.innerHTML = ""; mask.style.display = "none"; }
  },
};

/* ------------------------------------------------------------------------ */
/* 扫描图查看/框选弹窗                                                        */
/* ------------------------------------------------------------------------ */

const ScanModal = {
  /**
   * 打开扫描件
   * @param doc {id,file_no,title,scan}
   * @param regions 已有框选
   * @param opts {selectTarget, focusTarget}
   *   selectTarget：当前要为哪个目标新增框选（保存时提交）
   *   focusTarget：进入即聚焦的 target 字符串
   */
  viewer: null,

  async open(doc, regions = [], opts = {}) {
    if (!doc.scan) {
      toast(`材料 ${doc.file_no} 还没有上传扫描图`, "error");
      return;
    }
    const stage = h.el("div", { class: "viewer-stage" });
    const modeBtn = h.el("button", { class: "btn-ghost btn-sm" }, "框选依据");
    const labelInput = h.el("input", {
      placeholder: "给框选加个说明（可选）",
      style: "min-width:14em;",
    });
    let selecting = false;

    const viewer = new ImageViewer(stage, {
      canSelect: true,
      onSelect: rect => {
        if (!selecting || !opts.selectTarget) return;
        const label = labelInput.value.trim();
        Api.post(`/api/documents/${doc.id}/regions/`, {
          target: opts.selectTarget,
          label, ...rect,
        }).then(res => {
          toast("框选依据已保存", "ok");
          viewer.regions.push(res.region);
          viewer.setMode("pan");
          selecting = false;
          modeBtn.textContent = "框选依据";
          stage.classList.remove("mode-select");
          viewer.focus(opts.selectTarget);
        }).catch(e => toast(e.message, "error"));
      },
      onRegionClick: r => {
        if (window.App?.openTarget) window.App.openTarget(r.target);
      },
    });
    this.viewer = viewer;

    const body = h.el("div", {},
      h.el("div", { class: "viewer-toolbar" },
        h.el("strong", {}, `扫描件：${doc.file_no} ${doc.title || ""}`),
        h.el("div", { class: "spacer" }),
        h.el("button", {
          class: "btn-ghost btn-sm",
          onclick: () => viewer.zoom(1.25),
        }, "放大＋"),
        h.el("button", {
          class: "btn-ghost btn-sm",
          onclick: () => viewer.zoom(.8),
        }, "缩小－"),
        h.el("button", {
          class: "btn-ghost btn-sm",
          onclick: () => viewer.reset(),
        }, "适应窗口"),
        opts.selectTarget ? modeBtn : null,
        opts.selectTarget ? labelInput : null,
        opts.selectTarget
          ? h.el("span", { class: "muted small" },
              `框选将挂到：${opts.selectTarget}`)
          : null,
      ),
      stage,
      h.el("p", { class: "muted small" },
        "滚轮或双指缩放，按住拖动平移。点击红色框可回到其对应的版本/关系。"));

    if (opts.selectTarget) {
      modeBtn.addEventListener("click", () => {
        selecting = !selecting;
        viewer.setMode(selecting ? "select" : "pan");
        modeBtn.textContent = selecting ? "正在框选（拖出矩形）…" : "框选依据";
      });
    }

    Modal.open({
      title: doc.file_no + (doc.title ? ` · ${doc.title}` : ""),
      body, wide: true,
      footer: h.el("button", { onclick: () => Modal.back() }, "完成"),
    });

    await viewer.load(doc.scan, regions);
    if (opts.focusTarget) {
      const found = viewer.focus(opts.focusTarget);
      if (!found.length) toast("该条目在本张扫描件上暂无框选记录");
    }
  },
};
