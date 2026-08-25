/* Stake Code Claimer — Mini App (vanilla JS, no framework).
 * Auth: Telegram initData -> POST /auth -> session JWT in MEMORY ONLY.
 * Tabs: Accounts · Buy · Manage · Stats · Drop. Dark, professional.
 * CSP: no inline <style>/style= attributes — styling via classes (+ el.style CSSOM).
 */
(function () {
  "use strict";

  var API = "/app/api/v1";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var token = null, isAdmin = false, refreshTimer = null, current = "accounts", renderSeq = 0;
  var LANGS = [
    ["en", "🇬🇧 English"], ["ja", "🇯🇵 日本語"], ["zh", "🇨🇳 中文"], ["ko", "🇰🇷 한국어"],
    ["hi", "🇮🇳 हिन्दी"], ["pl", "🇵🇱 Polski"], ["vi", "🇻🇳 Tiếng Việt"], ["es", "🇪🇸 Español"],
    ["it", "🇮🇹 Italiano"], ["pt", "🇧🇷 Português"], ["fr", "🇫🇷 Français"], ["tr", "🇹🇷 Türkçe"]
  ];

  // ── i18n (Mini App is localized independently of the bot) ────────────────
  // English is the complete source; t() falls back to English for any missing
  // key, so partially-translated languages still render. High-visibility strings
  // (nav + section titles) are translated for all 12; add more keys any time.
  var MSG = {
    en: { nav_accounts: "Accounts", nav_buy: "Buy", nav_manage: "Manage", nav_stats: "Stats", nav_drop: "Drop",
      language: "Language", acc_subs: "My Subscriptions", buy_title: "Buy a Slot",
      manage_title: "Manage your slots", stats_title: "Claim Dashboard", drop_title: "Drop a code" },
    ja: { nav_accounts: "アカウント", nav_buy: "購入", nav_manage: "管理", nav_stats: "統計", nav_drop: "ドロップ",
      language: "言語", acc_subs: "マイサブスク", buy_title: "スロット購入", manage_title: "スロット管理",
      stats_title: "クレーム統計", drop_title: "コードをドロップ" },
    zh: { nav_accounts: "账户", nav_buy: "购买", nav_manage: "管理", nav_stats: "统计", nav_drop: "掉落",
      language: "语言", acc_subs: "我的订阅", buy_title: "购买槽位", manage_title: "管理槽位",
      stats_title: "领取面板", drop_title: "投放代码" },
    ko: { nav_accounts: "계정", nav_buy: "구매", nav_manage: "관리", nav_stats: "통계", nav_drop: "드롭",
      language: "언어", acc_subs: "내 구독", buy_title: "슬롯 구매", manage_title: "슬롯 관리",
      stats_title: "클레임 대시보드", drop_title: "코드 드롭" },
    hi: { nav_accounts: "खाते", nav_buy: "खरीदें", nav_manage: "प्रबंधन", nav_stats: "आँकड़े", nav_drop: "ड्रॉप",
      language: "भाषा", acc_subs: "मेरी सदस्यताएँ", buy_title: "स्लॉट खरीदें", manage_title: "स्लॉट प्रबंधन",
      stats_title: "क्लेम डैशबोर्ड", drop_title: "कोड ड्रॉप करें" },
    pl: { nav_accounts: "Konta", nav_buy: "Kup", nav_manage: "Zarządzaj", nav_stats: "Statystyki", nav_drop: "Drop",
      language: "Język", acc_subs: "Moje subskrypcje", buy_title: "Kup slot", manage_title: "Zarządzaj slotami",
      stats_title: "Panel odbioru", drop_title: "Wrzuć kod" },
    vi: { nav_accounts: "Tài khoản", nav_buy: "Mua", nav_manage: "Quản lý", nav_stats: "Thống kê", nav_drop: "Thả mã",
      language: "Ngôn ngữ", acc_subs: "Gói của tôi", buy_title: "Mua slot", manage_title: "Quản lý slot",
      stats_title: "Bảng nhận thưởng", drop_title: "Thả mã" },
    es: { nav_accounts: "Cuentas", nav_buy: "Comprar", nav_manage: "Gestionar", nav_stats: "Estadísticas", nav_drop: "Soltar",
      language: "Idioma", acc_subs: "Mis suscripciones", buy_title: "Comprar slot", manage_title: "Gestionar slots",
      stats_title: "Panel de reclamos", drop_title: "Soltar un código" },
    it: { nav_accounts: "Account", nav_buy: "Acquista", nav_manage: "Gestisci", nav_stats: "Statistiche", nav_drop: "Drop",
      language: "Lingua", acc_subs: "I miei abbonamenti", buy_title: "Acquista slot", manage_title: "Gestisci slot",
      stats_title: "Pannello richieste", drop_title: "Rilascia un codice" },
    pt: { nav_accounts: "Contas", nav_buy: "Comprar", nav_manage: "Gerir", nav_stats: "Estatísticas", nav_drop: "Soltar",
      language: "Idioma", acc_subs: "Minhas assinaturas", buy_title: "Comprar slot", manage_title: "Gerir slots",
      stats_title: "Painel de resgates", drop_title: "Soltar um código" },
    fr: { nav_accounts: "Comptes", nav_buy: "Acheter", nav_manage: "Gérer", nav_stats: "Stats", nav_drop: "Drop",
      language: "Langue", acc_subs: "Mes abonnements", buy_title: "Acheter un slot", manage_title: "Gérer les slots",
      stats_title: "Tableau de réclamations", drop_title: "Larguer un code" },
    tr: { nav_accounts: "Hesaplar", nav_buy: "Satın Al", nav_manage: "Yönet", nav_stats: "İstatistik", nav_drop: "Drop",
      language: "Dil", acc_subs: "Aboneliklerim", buy_title: "Slot Satın Al", manage_title: "Slotları Yönet",
      stats_title: "Talep Paneli", drop_title: "Kod Bırak" }
  };
  function t(key) {
    var l = lang();
    return (MSG[l] && MSG[l][key]) || MSG.en[key] || key;
  }

  // ── helpers ────────────────────────────────────────────────────────────
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(id) { return document.getElementById(id); }
  function stale(seq) { return seq !== renderSeq; }
  function view(html) {
    var v = el("view"); v.innerHTML = html; v.setAttribute("aria-busy", "false");
  }
  function toast(msg, bad) {
    var t = el("toast");
    t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(t._t); t._t = setTimeout(function () { t.className = "toast hidden"; }, 3200);
  }
  function haptic(kind) { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) {} }
  function icon(name, cls) { return '<svg class="' + (cls ? "ic " + cls : "ic") + '" aria-hidden="true" focusable="false"><use href="#i-' + name + '"/></svg>'; }
  function lang() { try { return localStorage.getItem("scc_lang") || "en"; } catch (e) { return "en"; } }
  function langLabel(code) { for (var i = 0; i < LANGS.length; i++) if (LANGS[i][0] === code) return LANGS[i][1]; return "🇬🇧 English"; }
  function fmtExpiry(iso) {
    if (!iso) return "No expiry";
    var t = Date.parse(iso); if (isNaN(t)) return "—";
    var ms = t - Date.now();
    if (ms <= 0) return "Expired";
    var d = Math.floor(ms / 86400000), h = Math.floor((ms % 86400000) / 3600000);
    if (d >= 1) return d + "d " + h + "h left";
    var m = Math.floor((ms % 3600000) / 60000);
    return h + "h " + m + "m left";
  }
  function num(v) { var n = Number(v); return isNaN(n) ? 0 : n; }

  // ── theme ────────────────────────────────────────────────────────────────
  function applyTheme() {
    var scheme = (tg && tg.colorScheme === "light") ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", scheme);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", scheme === "light" ? "#f3f6fa" : "#0a0f17");
  }

  // ── API layer ──────────────────────────────────────────────────────────
  function api(path, opts) {
    opts = opts || {};
    var headers = {};
    if (token) headers["Authorization"] = "Bearer " + token;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(API + path, {
      method: opts.method || "GET", headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, code: "BACKEND_UNAVAILABLE" }; })
        .then(function (j) {
          if (j && j.ok) return j.data;
          var e = new Error((j && j.error) || "error");
          e.code = (j && j.code) || "BACKEND_UNAVAILABLE"; e.status = r.status; throw e;
        });
    });
  }
  function readInitData() {
    if (tg && tg.initData) return tg.initData;
    try {
      var h = (window.location.hash || "").replace(/^#/, "");
      var m = /(?:^|&)tgWebAppData=([^&]*)/.exec(h);
      if (m && m[1]) return decodeURIComponent(m[1]);
    } catch (e) {}
    return "";
  }
  function authenticate() {
    var initData = readInitData();
    if (!initData) {
      var e = new Error("Please open this Mini App inside Telegram (menu button or 'Open App').");
      e.noInit = true; return Promise.reject(e);
    }
    return api("/auth", { method: "POST", body: { initData: initData } }).then(function (d) {
      token = d.token; isAdmin = !!d.is_admin; scheduleRefresh(); return d;
    });
  }
  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      api("/refresh", { method: "POST" }).then(function (d) {
        token = d.token; isAdmin = !!d.is_admin; scheduleRefresh();
      }).catch(function () {
        authenticate().catch(function () { fatal("Session expired. Please reopen the app."); });
      });
    }, 25 * 60 * 1000);
  }
  function fatal(msg) { view('<div class="empty">' + icon("alert", "empty-ic") + '<div class="empty-t">Something went wrong</div><div class="empty-s">' + esc(msg) + '</div></div>'); }

  // ── header (account chip + language pill) ────────────────────────────────
  function renderHeader() {
    var u = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || {};
    var name = esc(u.first_name || "Account");
    var uname = u.username ? ("@" + esc(u.username)) : ("ID " + esc(u.id || "—"));
    var hb = el("headerbar");
    hb.className = "headerbar";
    hb.innerHTML =
      '<div class="acct"><div class="acct-av">' + esc((u.first_name || "A").charAt(0).toUpperCase()) + '</div>' +
      '<div class="acct-meta"><div class="acct-name">' + name + '</div><div class="acct-sub">' + uname + '</div></div></div>' +
      '<button class="lang-pill" id="langPill" type="button">' + esc(langLabel(lang())) + '</button>';
    el("langPill").addEventListener("click", openLangSheet);
  }
  function openLangSheet() {
    var rows = LANGS.map(function (l) {
      return '<button class="lang-row" data-code="' + l[0] + '" type="button">' + esc(l[1]) +
        (l[0] === lang() ? ' <span class="lang-cur">' + icon("check") + '</span>' : '') + '</button>';
    }).join("");
    openSheet(t("language"), '<div class="lang-list">' + rows + '</div>');
    Array.prototype.forEach.call(document.querySelectorAll(".lang-row"), function (b) {
      b.addEventListener("click", function () {
        try { localStorage.setItem("scc_lang", b.getAttribute("data-code")); } catch (e) {}
        closeSheet();
        // Re-render the WHOLE Mini App in the new language (nav + current view).
        renderHeader(); renderNav(); markNav(); go(current);
        toast("Language set");
      });
    });
  }

  // ── bottom sheet (modal) ─────────────────────────────────────────────────
  function openSheet(title, bodyHtml) {
    var s = el("sheet");
    s.innerHTML = '<div class="sheet-card"><div class="sheet-head"><div class="sheet-title">' + esc(title) +
      '</div><button class="sheet-x" id="sheetX" type="button">×</button></div><div class="sheet-body">' + bodyHtml + '</div></div>';
    s.className = "sheet";
    el("sheetX").addEventListener("click", closeSheet);
    s.addEventListener("click", function (e) { if (e.target === s) closeSheet(); });
  }
  function closeSheet() { var s = el("sheet"); s.className = "sheet hidden"; s.innerHTML = ""; }

  // ── bottom navigation ─────────────────────────────────────────────────────
  var TABS = [
    ["accounts", "home"], ["buy", "wallet"],
    ["manage", "key"], ["stats", "chart"], ["drop", "ticket"]
  ];
  function renderNav() {
    el("bottomnav").innerHTML = TABS.map(function (tb) {
      return '<button class="navbtn" data-tab="' + tb[0] + '" type="button">' + icon(tb[1]) +
        '<span>' + esc(t("nav_" + tb[0])) + '</span></button>';
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll(".navbtn"), function (b) {
      b.addEventListener("click", function () { go(b.getAttribute("data-tab")); });
    });
  }
  function markNav() {
    Array.prototype.forEach.call(document.querySelectorAll(".navbtn"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-tab") === current);
    });
  }
  function go(tab) {
    current = tab; renderSeq++; markNav();
    el("view").scrollTop = 0;
    if (tab === "accounts") viewAccounts();
    else if (tab === "buy") viewBuy();
    else if (tab === "manage") viewManage();
    else if (tab === "stats") viewStats();
    else if (tab === "drop") viewDrop();
  }

  function loading() { view('<div class="loader"><div class="spinner"></div></div>'); }
  function planTiles(plans, onPick) {
    return (plans || []).map(function (p, i) {
      var price = p.price_usd != null ? ("$" + p.price_usd) : "Soon";
      // Show ≈/day only when the plan HAS a per-day rate. A priced plan without one
      // (e.g. Stream Special) shows nothing — not a misleading "$0.86/day" or "soon".
      var per = p.per_day_usd != null ? ("≈$" + p.per_day_usd + " / day")
        : (p.price_usd != null ? (p.features && p.features[0] ? p.features[0] : "") : "Pricing soon");
      return '<button class="plan" data-i="' + i + '" type="button"' + (p.price_usd == null ? ' disabled' : '') + '>' +
        '<div class="plan-badge">' + esc(p.badge || "") + '</div>' +
        '<div class="plan-name">' + esc(p.label) + '</div>' +
        '<div class="plan-price">' + esc(price) + '</div>' +
        '<div class="plan-per">' + esc(per) + '</div></button>';
    }).join("");
  }

  // ── Accounts (home) ───────────────────────────────────────────────────────
  function viewAccounts() {
    var seq = renderSeq; loading();
    Promise.all([api("/capacity").catch(function () { return null; }),
                 api("/slots").catch(function () { return { slots: [] }; })])
      .then(function (r) {
        if (stale(seq)) return;
        var cap = r[0] || {}, slots = (r[1] && r[1].slots) || [];
        var avail = cap.available != null ? cap.available : "—";
        var priceRow = (cap.plans || []).filter(function (p) { return p.price_usd != null; })
          .map(function (p) { return '<span class="pchip">' + esc(p.label) + ' <b>$' + esc(p.price_usd) + '</b></span>'; }).join("");
        var html = '<section class="cap"><div class="cap-dot"></div><div class="cap-main"><div class="cap-lbl">SLOT POOL AVAILABILITY</div>' +
          '<div class="cap-val">' + esc(avail) + ' slots available</div></div>' + icon("shield", "cap-ic") + '</section>';
        if (priceRow) html += '<div class="prices">' + priceRow + '</div>';
        html += '<h2 class="sec">'+esc(t("acc_subs"))+'</h2>';
        if (!slots.length) {
          html += '<div class="empty"><div class="empty-t">No subscriptions yet</div>' +
            '<div class="empty-s">Automate your Stake claims with one of our plans.</div>' +
            '<button class="btn primary" id="getStarted" type="button">Get Started</button></div>';
        } else {
          html += '<div class="subs">' + slots.map(subCard).join("") + '</div>';
        }
        view(html);
        var gs = el("getStarted"); if (gs) gs.addEventListener("click", function () { go("buy"); });
        Array.prototype.forEach.call(document.querySelectorAll(".sub-gear"), function (b) {
          b.addEventListener("click", function () { go("manage"); });
        });
      }).catch(function () { if (!stale(seq)) fatal("Could not load your dashboard."); });
  }
  function subCard(s) {
    var online = s.online && !s.expired;
    return '<div class="sub">' +
      '<div class="sub-top"><div class="sub-name">' + esc(s.stake_username || "—") + '</div>' +
      '<span class="dot ' + (online ? "on" : "off") + '"></span>' +
      '<span class="sub-status">' + (s.expired ? "Expired" : (online ? "Online" : "Offline")) + '</span>' +
      '<button class="sub-gear" type="button" aria-label="Manage">' + icon("key") + '</button></div>' +
      '<div class="sub-meta"><span class="tag">' + esc(s.plan || "plan") + '</span>' +
      '<span class="tag alt">' + esc(fmtExpiry(s.expires_at)) + '</span>' +
      '<span class="tag ghost">' + esc(s.worker_label || "Worker") + '</span></div></div>';
  }

  // ── Buy ────────────────────────────────────────────────────────────────────
  var buyState = { token: null, username: null, plan: null, plans: [] };
  function viewBuy() {
    var seq = renderSeq; loading();
    api("/capacity").then(function (cap) {
      if (stale(seq)) return;
      buyState = { token: null, username: null, plan: null, plans: cap.plans || [] };
      renderBuyStep1(cap);
    }).catch(function () { if (!stale(seq)) fatal("Could not load plans."); });
  }
  function renderBuyStep1(cap) {
    var html = '<h2 class="sec">'+esc(t("buy_title"))+'</h2>' +
      '<div class="cap-mini">🟢 ' + esc(cap.available != null ? cap.available : "—") + ' slots available</div>' +
      '<div class="card"><label class="lab">1 · Your Stake API key</label>' +
      '<input class="inp" id="buyKey" type="password" placeholder="Paste your Stake API key" autocomplete="off">' +
      '<button class="btn" id="verifyBtn" type="button">Verify key</button>' +
      '<div class="verify-out" id="verifyOut"></div></div>' +
      '<div id="buyRest"></div>';
    view(html);
    el("verifyBtn").addEventListener("click", doVerify);
  }
  function doVerify() {
    var key = (el("buyKey").value || "").trim();
    var out = el("verifyOut");
    if (!key) { out.textContent = "Enter your API key first."; out.className = "verify-out bad"; return; }
    out.textContent = "Verifying…"; out.className = "verify-out";
    el("verifyBtn").disabled = true;
    api("/verify-token", { method: "POST", body: { token: key } }).then(function (d) {
      el("verifyBtn").disabled = false;
      if (d.valid) {
        buyState.token = key; buyState.username = d.username || null;
        out.innerHTML = icon("check") + " Verified: <b>" + esc(d.username || "account") + "</b>" +
          '<div class="scrollcue">' + icon("chevron", "cue-arrow") + " Scroll down to choose your plan</div>";
        out.className = "verify-out good"; haptic("success");
        renderBuyStep2();
        // Nudge the buyer to the plan section that just appeared below.
        setTimeout(function () {
          try { var pr = el("buyRest"); if (pr) pr.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (e) {}
        }, 120);
      } else {
        buyState.token = null;
        out.textContent = d.reason === "unavailable"
          ? "Verification temporarily unavailable — please try again shortly."
          : "Invalid API key — please check and try again.";
        out.className = "verify-out bad"; haptic("error");
      }
    }).catch(function () { el("verifyBtn").disabled = false; out.textContent = "Verification failed — try again."; out.className = "verify-out bad"; });
  }
  function renderBuyStep2() {
    var rest = el("buyRest");
    rest.innerHTML = '<label class="lab">2 · Choose a plan</label><div class="plans">' + planTiles(buyState.plans) + '</div>' +
      '<div id="buyStep3"></div>';
    Array.prototype.forEach.call(rest.querySelectorAll(".plan"), function (b) {
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(rest.querySelectorAll(".plan"), function (x) { x.classList.remove("sel"); });
        b.classList.add("sel");
        buyState.plan = buyState.plans[Number(b.getAttribute("data-i"))];
        renderBuyStep3();
      });
    });
  }
  // Full currency set — mirrors the userscript's AVAILABLE_CURRENCIES (17).
  var CURRENCIES = ["usdt", "btc", "eth", "ltc", "sol", "doge", "xrp", "trx", "eos",
                    "bnb", "usdc", "dai", "link", "shib", "uni", "pol", "trump"];
  function currencyOptions(sel) {
    return CURRENCIES.map(function (c) {
      return '<option value="' + c + '"' + (c === sel ? " selected" : "") + '>' + c.toUpperCase() + '</option>';
    }).join("");
  }
  function renderBuyStep3() {
    var s3 = el("buyStep3");
    s3.innerHTML = '<label class="lab">3 · Configure this slot</label><div class="card">' +
      cfgFields({}) +
      '<button class="btn primary" id="payBtn" type="button">Continue to payment · ' +
      (buyState.plan ? "$" + esc(buyState.plan.price_usd) : "") + '</button></div>';
    wireToggles(s3);   // FIX: Buy-flow toggles were never wired (Manage/Drop were)
    el("payBtn").addEventListener("click", doBuyPay);
  }
  function cfgFields(v) {
    v = v || {};
    return '<div class="row2"><div><label class="lab sm">Withdrawal currency</label>' +
      '<select class="inp" id="cfgWc">' + currencyOptions(v.withdrawal_currency || "usdt") + '</select></div>' +
      '<div><label class="lab sm">Reload currency</label><select class="inp" id="cfgRc">' + currencyOptions(v.reload_currency || "usdt") + '</select></div></div>' +
      '<label class="lab sm">Minimum value to claim (0 = all)</label>' +
      '<input class="inp" id="cfgVf" type="number" min="0" step="0.01" value="' + esc(v.value_filter != null ? v.value_filter : "") + '" placeholder="0">' +
      toggleRow("cfgAv", "Auto Vault", !!v.auto_vault) +
      toggleRow("cfgAb", "Auto Bonus", !!v.auto_bonus) +
      toggleRow("cfgAr", "Auto Reload", !!v.auto_reload);
  }
  function toggleRow(id, label, on) {
    return '<div class="tog" data-id="' + id + '" data-on="' + (on ? 1 : 0) + '"><span>' + esc(label) +
      '</span><span class="sw ' + (on ? "on" : "") + '"><span class="knob"></span></span></div>';
  }
  function wireToggles(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll(".tog"), function (t) {
      t.addEventListener("click", function () {
        var on = t.getAttribute("data-on") === "1" ? 0 : 1;
        t.setAttribute("data-on", String(on));
        t.querySelector(".sw").classList.toggle("on", !!on);
      });
    });
  }
  function readCfg() {
    function g(id) { var e = el(id); return e ? e.value : ""; }
    function tog(id) { var e = document.querySelector('.tog[data-id="' + id + '"]'); return !!(e && e.getAttribute("data-on") === "1"); }
    return {
      withdrawal_currency: g("cfgWc") || "usdt", reload_currency: g("cfgRc") || "usdt",
      value_filter: g("cfgVf") || null, auto_vault: tog("cfgAv"),
      auto_bonus: tog("cfgAb"), auto_reload: tog("cfgAr")
    };
  }
  function doBuyPay() {
    if (!buyState.token || !buyState.plan) { toast("Verify a key and pick a plan first", true); return; }
    var cfg = readCfg();
    var btn = el("payBtn"); btn.disabled = true; btn.textContent = "Creating order…";
    api("/order/begin", { method: "POST", body: { plan_code: buyState.plan.code, token: buyState.token, config: cfg } })
      .then(function (d) {
        return api("/order/pay", { method: "POST", body: { order_id: d.order_id } }).then(function (p) {
          return { order_id: d.order_id, pay: p };
        });
      }).then(function (r) {
        if (r.pay.already) { toast("Already active"); go("accounts"); return; }
        var url = r.pay.pay_url;
        if (url) { try { tg ? tg.openLink(url) : window.open(url, "_blank"); } catch (e) { window.open(url, "_blank"); } }
        pollOrder(r.order_id);
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = "Continue to payment";
        toast(e.code === "no_capacity" ? "No slots available right now" : "Could not start payment", true);
      });
  }
  function pollOrder(orderId) {
    view('<div class="empty">' + icon("wallet", "empty-ic") + '<div class="empty-t">Waiting for payment…</div>' +
      '<div class="empty-s">Complete the payment in the window that opened. This screen updates automatically.</div>' +
      '<div class="loader"><div class="spinner"></div></div></div>');
    var tries = 0, seq = renderSeq;
    var iv = setInterval(function () {
      if (stale(seq)) { clearInterval(iv); return; }
      tries++;
      api("/order/" + orderId).then(function (d) {
        if (d.status === "allocated") {
          clearInterval(iv); haptic("success");
          view('<div class="empty">' + icon("check", "empty-ic") + '<div class="empty-t">Subscription active!</div>' +
            '<div class="empty-s">' + esc(d.stake_username || "Your slot") + ' is now claiming.</div>' +
            '<button class="btn primary" id="doneBtn" type="button">View my subscriptions</button></div>');
          el("doneBtn").addEventListener("click", function () { go("accounts"); });
        } else if (d.status === "failed" || d.status === "refunded" || d.status === "reservation_expired") {
          clearInterval(iv);
          toast("Payment not completed", true); go("buy");
        }
      }).catch(function () {});
      if (tries > 120) clearInterval(iv);   // ~10 min
    }, 5000);
  }

  // ── Manage ───────────────────────────────────────────────────────────────
  function viewManage() {
    var seq = renderSeq; loading();
    api("/slots").then(function (r) {
      if (stale(seq)) return;
      var slots = (r && r.slots) || [];
      if (!slots.length) {
        view('<h2 class="sec">Manage</h2><div class="empty"><div class="empty-t">No slots to manage</div>' +
          '<div class="empty-s">Buy a slot first, then configure it here.</div></div>');
        return;
      }
      view('<h2 class="sec">'+esc(t("manage_title"))+'</h2><div class="subs">' + slots.map(function (s) {
        return '<button class="sub tap" data-id="' + s.slot_id + '" type="button">' +
          '<div class="sub-top"><div class="sub-name">' + esc(s.stake_username || "—") + '</div>' +
          '<span class="sub-status">' + (s.expired ? "Expired" : "Active") + '</span>' + icon("chevron", "chev") + '</div>' +
          '<div class="sub-meta"><span class="tag">' + esc(s.plan || "") + '</span><span class="tag alt">' + esc(fmtExpiry(s.expires_at)) + '</span></div></button>';
      }).join("") + '</div>');
      Array.prototype.forEach.call(document.querySelectorAll(".sub.tap"), function (b) {
        var s = slots.filter(function (x) { return String(x.slot_id) === b.getAttribute("data-id"); })[0];
        b.addEventListener("click", function () { openManageSheet(s); });
      });
    }).catch(function () { if (!stale(seq)) fatal("Could not load your slots."); });
  }
  function openManageSheet(s) {
    if (s.expired) { toast("This slot has expired", true); return; }
    openSheet(s.stake_username || "Slot", '<div class="card flat">' + cfgFields(s) +
      '<label class="lab sm">Replace API key (optional — re-verified)</label>' +
      '<input class="inp" id="cfgKey" type="password" placeholder="Leave blank to keep current" autocomplete="off">' +
      '<button class="btn primary" id="saveCfg" type="button">Save changes</button></div>');
    wireToggles();
    el("saveCfg").addEventListener("click", function () {
      var body = readCfg();
      var nk = (el("cfgKey").value || "").trim();
      if (nk) body.stake_access_token = nk;
      var btn = el("saveCfg"); btn.disabled = true; btn.textContent = "Saving…";
      api("/slots/" + s.slot_id + "/config", { method: "POST", body: body }).then(function () {
        closeSheet(); toast("Saved"); go("manage");
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = "Save changes";
        toast(e.code && e.code.indexOf("verify") === 0 ? "New key could not be verified" : "Save failed", true);
      });
    });
  }

  // ── Stats ────────────────────────────────────────────────────────────────
  var statsType = "all";
  function viewStats() {
    renderStats("24h");
  }
  function renderStats(win) {
    var seq = renderSeq; loading();
    api("/stats?window=" + win + "&type=" + statsType).then(function (d) {
      if (stale(seq)) return;
      var earned = d.earned || {};
      var earnedRows = Object.keys(earned).length
        ? Object.keys(earned).map(function (c) { return '<span class="ecur">' + esc(c.toUpperCase()) + ' <b>' + esc(earned[c]) + '</b></span>'; }).join("")
        : '<span class="ecur muted">No earnings yet</span>';
      var windows = [["24h", "24h / Today"], ["7d", "7 Days"], ["30d", "30 Days"]];
      var tabs = windows.map(function (w) {
        return '<button class="stat-tab' + (w[0] === win ? " active" : "") + '" data-w="' + w[0] + '" type="button">' + esc(w[1]) + '</button>';
      }).join("");
      var types = [["all", "All Types"], ["drop", "Drops"], ["reload", "Reload"]];
      var typeBtns = types.map(function (tp) {
        return '<button class="chip-btn' + (tp[0] === statsType ? " active" : "") + '" data-t="' + tp[0] + '" type="button">' + esc(tp[1]) + '</button>';
      }).join("");
      var recent = (d.recent_codes || []);
      var recentHtml = recent.length ? recent.map(function (r) {
        var ok = r.claimed;
        return '<div class="rc"><div class="rc-code">' + esc(r.code) + '</div>' +
          '<div class="rc-res ' + (ok ? "ok" : "no") + '">' + esc(ok ? "Claimed" : (r.result || "—")) + '</div>' +
          '<div class="rc-amt">' + (r.amount != null ? esc(r.amount) + " " + esc((r.currency || "").toUpperCase()) : "") + '</div></div>';
      }).join("") : '<div class="empty-s pad">No codes in the last 7 days.</div>';

      view('<h2 class="sec">'+esc(t("stats_title"))+'</h2>' +
        '<div class="stat-tabs">' + tabs + '</div>' +
        '<div class="chips">' + typeBtns + '</div>' +
        '<section class="earn"><div class="earn-lbl">TOTAL EARNED</div><div class="earn-vals">' + earnedRows + '</div>' +
        '<div class="earn-sub">' + esc(d.successful_claims || 0) + ' successful claims</div></section>' +
        '<h3 class="sec sm">Recent codes (last 7 days)</h3><div class="recent">' + recentHtml + '</div>');

      Array.prototype.forEach.call(document.querySelectorAll(".stat-tab"), function (b) {
        b.addEventListener("click", function () { renderStats(b.getAttribute("data-w")); });
      });
      Array.prototype.forEach.call(document.querySelectorAll(".chip-btn"), function (b) {
        b.addEventListener("click", function () { statsType = b.getAttribute("data-t"); renderStats(win); });
      });
    }).catch(function () { if (!stale(seq)) fatal("Could not load stats."); });
  }

  // ── Drop ─────────────────────────────────────────────────────────────────
  function viewDrop() {
    view('<h2 class="sec">'+esc(t("drop_title"))+'</h2>' +
      '<div class="note">' + icon("shield") +
      ' This code is sent <b>ONLY to your own Stake API keys</b> (your slots) — it is never shared with anyone else.</div>' +
      '<div class="card"><label class="lab">Code</label>' +
      '<input class="inp" id="dropCode" type="text" placeholder="e.g. abc123" maxlength="64" autocomplete="off">' +
      '<div class="hint">A <b>drop</b> is a normal Stake bonus-drop code. We instantly claim it on every one of your slots.</div>' +
      '<div class="tog" data-id="dropBonus" data-on="0"><span>This is a bonus code</span><span class="sw"><span class="knob"></span></span></div>' +
      '<div class="hint">Turn this on <b>only</b> for weekly / monthly stream &amp; bonus-reward codes — they are claimed through Stake’s bonus flow instead of the drop flow.</div>' +
      '<button class="btn primary" id="dropBtn" type="button">Drop to my slots</button></div>');
    wireToggles();
    el("dropBtn").addEventListener("click", function () {
      var code = (el("dropCode").value || "").trim();
      if (!code) { toast("Enter a code", true); return; }
      var bonus = document.querySelector('.tog[data-id="dropBonus"]').getAttribute("data-on") === "1";
      var btn = el("dropBtn"); btn.disabled = true; btn.textContent = "Dropping…";
      api("/drop", { method: "POST", body: { code: code, couponType: bonus ? "bonus" : "drop" } }).then(function (d) {
        btn.disabled = false; btn.textContent = "Drop to my slots";
        haptic("success");
        toast(d.slots ? ("Dropped to " + d.slots + " slot(s)") : "No active slots to drop to");
        el("dropCode").value = "";
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = "Drop to my slots";
        toast(e.code === "INVALID_CODE" ? "Invalid code" : "Drop failed", true);
      });
    });
  }

  // ── boot ───────────────────────────────────────────────────────────────
  function boot() {
    try { if (tg) { tg.ready(); tg.expand(); } } catch (e) {}
    applyTheme();
    if (tg) { try { tg.onEvent("themeChanged", applyTheme); } catch (e) {} }
    var rb = el("refreshBtn"); if (rb) rb.addEventListener("click", function () { go(current); });
    renderNav(); markNav();
    authenticate().then(function () {
      renderHeader(); go("accounts");
    }).catch(function (e) {
      renderHeader();
      fatal(e.message || "Please open inside Telegram.");
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
