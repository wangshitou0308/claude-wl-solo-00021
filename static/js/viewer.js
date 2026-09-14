/*
 * 可缩放扫描图查看器
 *  - 滚轮/双指/按钮缩放，拖拽平移，触屏可用
 *  - 框选模式：在图上拖出 0~1 归一化矩形作为依据
 *  - focus(target)：打开并高亮已挂在某目标上的框，用于"逐级回到原图"
 */
"use strict";

class ImageViewer {
  constructor(stageEl, opts = {}) {
    this.stage = stageEl;
    this.opts = opts;                 // { canSelect, onSelect(region) }
    this.scale = 1;
    this.tx = 0; this.ty = 0;
    this.imgW = 0; this.imgH = 0;
    this.mode = "pan";
    this.regions = [];
    this.focusTarget = null;

    this.inner = h.el("div", { class: "viewer-inner" });
    this.img = h.el("img", { alt: "扫描件", draggable: "false" });
    this.boxLayer = h.el("div", {
      style: "position:absolute;inset:0;pointer-events:none;",
    });
    this.inner.append(this.img, this.boxLayer);
    this.stage.innerHTML = "";
    this.stage.append(this.inner);

    this._bind();
  }

  load(src, regions = []) {
    this.regions = regions;
    return new Promise((resolve, reject) => {
      this.img.onload = () => {
        this.imgW = this.img.naturalWidth;
        this.imgH = this.img.naturalHeight;
        this._fit();
        this._renderBoxes();
        resolve();
      };
      this.img.onerror = reject;
      if (src) this.img.src = src;
      else resolve();
    });
  }

  setMode(mode) {
    this.mode = mode;
    this.stage.classList.toggle("mode-select", mode === "select");
  }

  focus(target) {
    this.focusTarget = target;
    const found = this.regions.filter(r =>
      r.target === target || r.target.startsWith(target + ":"));
    this._renderBoxes();
    if (found.length) {
      const b = found[0].box || found[0];
      const cx = b.x + b.w / 2, cy = b.y + b.h / 2;
      this.scale = Math.max(this.scale, 2.2);
      this.tx = this.stage.clientWidth / 2 - cx * this.imgW * this.scale;
      this.ty = this.stage.clientHeight / 2 - cy * this.imgH * this.scale;
      this._apply();
    }
    return found;
  }

  _fit() {
    const pad = 24;
    const sx = (this.stage.clientWidth - pad) / (this.imgW || 1);
    const sy = (this.stage.clientHeight - pad) / (this.imgH || 1);
    this.scale = Math.min(1.25, Math.min(sx, sy));
    this.tx = (this.stage.clientWidth - this.imgW * this.scale) / 2;
    this.ty = (this.stage.clientHeight - this.imgH * this.scale) / 2;
    this._apply();
  }

  zoom(factor, cx, cy) {
    cx = cx ?? this.stage.clientWidth / 2;
    cy = cy ?? this.stage.clientHeight / 2;
    const ns = Math.min(8, Math.max(.15, this.scale * factor));
    const real = ns / this.scale;
    this.tx = cx - (cx - this.tx) * real;
    this.ty = cy - (cy - this.ty) * real;
    this.scale = ns;
    this._apply();
  }

  reset() { this._fit(); }

  _apply() {
    this.inner.style.transform =
      `translate(${this.tx}px, ${this.ty}px) scale(${this.scale})`;
    this.inner.style.width = this.imgW + "px";
    this.inner.style.height = this.imgH + "px";
    this.img.style.width = this.imgW + "px";
    this.img.style.height = this.imgH + "px";
  }

  _eventPoint(e) {
    const rect = this.stage.getBoundingClientRect();
    const p = e.touches ? e.touches[0] : e;
    return { x: p.clientX - rect.left, y: p.clientY - rect.top };
  }

  _toImage(pt) {
    return {
      x: (pt.x - this.tx) / this.scale,
      y: (pt.y - this.ty) / this.scale,
    };
  }

  _bind() {
    let dragging = false, start = null, startImg = null, moved = false;
    let draft = null;
    const pinch = { dist: 0, scale: 1 };

    this.stage.addEventListener("wheel", e => {
      e.preventDefault();
      const pt = this._eventPoint(e);
      this.zoom(e.deltaY < 0 ? 1.12 : .89, pt.x, pt.y);
    }, { passive: false });

    this.stage.addEventListener("pointerdown", e => {
      this.stage.setPointerCapture(e.pointerId);
      const pt = this._eventPoint(e);
      start = pt; moved = false;
      if (this.mode === "select") {
        startImg = this._toImage(pt);
        draft = h.el("div", { class: "ev-box draft" });
        this.boxLayer.append(draft);
      } else {
        dragging = true;
        this.stage.classList.add("dragging");
      }
    });

    this.stage.addEventListener("pointermove", e => {
      if (!start) return;
      const pt = this._eventPoint(e);
      if (Math.hypot(pt.x - start.x, pt.y - start.y) > 4) moved = true;
      if (this.mode === "select" && draft && startImg) {
        const cur = this._toImage(pt);
        const x1 = Math.max(0, Math.min(this.imgW, Math.min(startImg.x, cur.x)));
        const y1 = Math.max(0, Math.min(this.imgH, Math.min(startImg.y, cur.y)));
        const x2 = Math.max(0, Math.min(this.imgW, Math.max(startImg.x, cur.x)));
        const y2 = Math.max(0, Math.min(this.imgH, Math.max(startImg.y, cur.y)));
        draft.style.left = x1 + "px";
        draft.style.top = y1 + "px";
        draft.style.width = (x2 - x1) + "px";
        draft.style.height = (y2 - y1) + "px";
        draft._rect = { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
      } else if (dragging) {
        this.tx += pt.x - start.x;
        this.ty += pt.y - start.y;
        start = pt;
        this._apply();
      }
    });

    const end = e => {
      if (this.mode === "select" && draft) {
        const r = draft._rect;
        draft.remove(); draft = null;
        if (moved && r && r.w > 6 && r.h > 6 && this.opts.onSelect) {
          this.opts.onSelect({
            x: +(r.x / this.imgW).toFixed(4),
            y: +(r.y / this.imgH).toFixed(4),
            w: +(r.w / this.imgW).toFixed(4),
            h: +(r.h / this.imgH).toFixed(4),
          });
        }
      }
      dragging = false; start = null; startImg = null;
      this.stage.classList.remove("dragging");
    };
    this.stage.addEventListener("pointerup", end);
    this.stage.addEventListener("pointercancel", end);

    // 双指缩放（触屏）
    this.stage.addEventListener("touchstart", e => {
      if (e.touches.length === 2) {
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        pinch.dist = Math.hypot(dx, dy);
        pinch.scale = this.scale;
      }
    }, { passive: true });
    this.stage.addEventListener("touchmove", e => {
      if (e.touches.length === 2 && pinch.dist) {
        e.preventDefault();
        const dx = e.touches[0].clientX - e.touches[1].clientX;
        const dy = e.touches[0].clientY - e.touches[1].clientY;
        const d = Math.hypot(dx, dy);
        this.scale = Math.min(8, Math.max(.15, pinch.scale * d / pinch.dist));
        this._apply();
      }
    }, { passive: false });
  }

  _renderBoxes() {
    this.boxLayer.innerHTML = "";
    for (const r of this.regions) {
      const b = r.box || r;
      const isFocus = this.focusTarget &&
        (r.target === this.focusTarget ||
         r.target.startsWith(this.focusTarget + ":"));
      const box = h.el("div", {
        class: "ev-box" + (isFocus ? " focus" : ""),
        title: r.label || r.target,
        style: `left:${b.x * 100}%;top:${b.y * 100}%;` +
               `width:${b.w * 100}%;height:${b.h * 100}%;`,
      }, h.el("span", { class: "ev-tag" }, r.label || r.target || "依据"));
      box.style.pointerEvents = "auto";
      if (this.opts.onRegionClick) {
        box.addEventListener("click", ev => {
          ev.stopPropagation();
          this.opts.onRegionClick(r);
        });
      }
      this.boxLayer.append(box);
    }
  }
}
