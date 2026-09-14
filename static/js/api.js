/* API 封装与通用小工具（无框架、无构建步骤） */
"use strict";

const Api = {
  csrfToken() {
    if (document.cookie.split("; ").some(r => {
      const [k, ...v] = r.split("=");
      if (k === "csrftoken") { this._token = v.join("="); return true; }
      return false;
    })) return this._token;
    return this._token || "";
  },

  async _req(method, url, body) {
    const opts = { method, headers: {}, credentials: "same-origin" };
    if (method !== "GET") opts.headers["X-CSRFToken"] = this.csrfToken();
    if (body instanceof FormData) {
      opts.body = body;
    } else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    let data = {};
    try { data = await r.json(); } catch (_) { /* 无响应体 */ }
    if (!r.ok || data.ok === false) {
      throw new Error(data.error || `请求失败（${r.status}）`);
    }
    return data;
  },
  get(u) { return this._req("GET", u); },
  post(u, b) { return this._req("POST", u, b ?? {}); },
  patch(u, b) { return this._req("PATCH", u, b ?? {}); },
  del(u) { return this._req("DELETE", u); },
};

const h = {
  el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "dataset") Object.assign(n.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") {
        n.addEventListener(k.slice(2).toLowerCase(), v);
      } else if (k === "html") n.innerHTML = v;
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.appendChild(typeof kid === "string" || typeof kid === "number"
        ? document.createTextNode(String(kid)) : kid);
    }
    return n;
  },

  esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  },

  dateInput(d) {
    return d || "";
  },

  /* [lo, hi] 区间 → 文本 */
  rangeText(lo, hi) {
    if (lo && hi) return lo === hi ? lo : `${lo} ～ ${hi}`;
    if (lo) return `${lo} 起`;
    if (hi) return `不晚于 ${hi}`;
    return "日期未明";
  },

  spanText(startLo, startHi, endLo, endHi) {
    const s = this.rangeText(startLo, startHi);
    const e = endLo || endHi ? this.rangeText(endLo, endHi) : "至今";
    return `${s} → ${e}`;
  },
};

const KIND_TEXT = {
  policy: "保单", renewal: "续期通知", endorsement: "批单", other: "其他材料",
};
const REL_TEXT = {
  replace: "全文替换", partial: "局部替换", supplement: "增补",
  revoke_version: "撤销版本", revoke_relation: "撤销修改",
};
const STATUS_TEXT = {
  active: "确定有效", possible: "可能有效", expired: "已失效", future: "尚未生效",
};

function toast(msg, kind = "") {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = kind;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.className = "hidden"; }, 3600);
}
