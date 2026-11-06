/* Keith's alerts on every admin page (§489, decided item 7; the owner
 * approved this design 2026-11-06): every open incident, worst first, with
 * its plan, until each is resolved. "I fixed it - check now" asks Keith to
 * re-check; closing only hides the pop-up for this page view, and the badge
 * stays. Reads `/api/admin/incidents` with the page's own admin session;
 * before the owner signs in it gets a 404 and shows nothing. */
(function () {
  "use strict";
  var LINK = { errors: "/admin#errors", usage: "/admin#services",
               reports: "/admin#feedback", tracking: "/admin#errors" };
  var shownThisLoad = false, closedThisLoad = false, latest = null;

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === "style") n.style.cssText = attrs[k];
      else if (k === "text") n.textContent = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function when(at) {
    if (!at) return "";
    return new Date(at * 1000).toLocaleString([], { month: "short", day: "numeric",
                                                    hour: "2-digit", minute: "2-digit" });
  }
  var C = { panel: "var(--panel,#1C1926)", line: "var(--line,#3A3348)",
            ground: "var(--ground,#14111C)", surface: "var(--surface,#262233)",
            ink: "var(--ink,#F1EEF7)", muted: "var(--muted,#A79EBA)",
            faint: "var(--faint,#7C7391)", amber: "var(--amber,#E9BC63)",
            amberDk: "var(--amber-dk,#8A6A22)", rose: "var(--rose,#DE8FA8)",
            roseBg: "var(--rose-bg,#33202A)",
            mono: "ui-monospace,Menlo,Consolas,monospace",
            serif: "Georgia,'Times New Roman',serif" };
  var SEV = {
    urgent: "background:" + C.roseBg + ";color:" + C.rose + ";border:1px solid " + C.rose,
    warn: "background:#3a2f17;color:" + C.amber + ";border:1px solid " + C.amberDk,
    notice: "background:" + C.surface + ";color:" + C.muted + ";border:1px solid " + C.line
  };
  var BTN = "background:" + C.surface + ";border:1px solid " + C.line +
            ";border-radius:8px;padding:5px 10px;font-size:12px;color:" + C.ink + ";cursor:pointer";

  function api(path, body) {
    var headers = { "Accept": "application/json" };
    if (body) headers["Content-Type"] = "application/json";
    return fetch(path, { method: body ? "POST" : "GET", headers: headers,
                         credentials: "same-origin",
                         body: body ? JSON.stringify(body) : undefined })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); });
  }

  function planBox(plan) {
    var rows = [["Problem", plan.problem], ["A listener notices", plan.listener_notices],
                ["Tried", plan.tried], ["Recommended", plan.recommended],
                ["Cost and risk", plan.cost_and_risk], ["Needs from you", plan.needs_from_owner]];
    var box = el("div", { "class": "keith-plan", style: "font-size:12.5px;line-height:1.5;color:" + C.muted +
      ";background:" + C.ground + ";border:1px solid " + C.line + ";border-radius:8px;padding:8px 10px;margin-top:6px" });
    rows.forEach(function (r) {
      if (!r[1]) return;
      box.appendChild(el("b", { text: r[0] + ": ", style: "color:" + C.ink + ";font-weight:600" }));
      box.appendChild(document.createTextNode(r[1] + " "));
    });
    return box;
  }

  function item(inc) {
    var meta = ["open since " + when(inc.opened_at), "seen " + inc.seen + " time" + (inc.seen === 1 ? "" : "s")];
    if (inc.reopened) meta.push("reopened " + inc.reopened + "×");
    if (inc.emailed_at) meta.push("emailed " + when(inc.emailed_at));
    if (inc.fixed_claim_at) meta.push("you said fixed " + when(inc.fixed_claim_at) + " - Keith is watching");
    var note = (inc.detail || {}).after_your_fix;
    var plan = planBox(inc.plan || {});
    var open = inc.severity === "urgent";
    plan.hidden = !open;
    var said = el("div", { "class": "keith-said", style: "font:11px " + C.mono + ";color:" + C.amber + ";margin-top:6px" });
    var toggle = el("button", { type: "button", style: BTN, text: open ? "Hide the plan" : "Show the plan" });
    toggle.onclick = function () { plan.hidden = !plan.hidden; toggle.textContent = plan.hidden ? "Show the plan" : "Hide the plan"; };
    var fixed = el("button", { type: "button", "class": "keith-fixed",
      style: BTN + ";border-color:" + C.amberDk + ";color:" + C.amber, text: "I fixed it — check now" });
    fixed.onclick = function () {
      fixed.disabled = true; said.textContent = "Keith is checking…";
      api("/api/admin/incidents/fixed", { cause: inc.cause })
        .then(function (r) { said.textContent = r.message || r.state; setTimeout(refresh, 1500); })
        .catch(function (e) { said.textContent = "The check did not run: " + e.message; fixed.disabled = false; });
    };
    var acts = el("div", { style: "display:flex;gap:8px;margin-top:8px;flex-wrap:wrap" }, [toggle, fixed]);
    if (LINK[inc.source]) acts.appendChild(el("a", { href: LINK[inc.source], style: BTN + ";text-decoration:none", text: "Open on the page" }));
    return el("div", { "class": "keith-item", "data-cause": inc.cause,
                       style: "padding:12px 18px;border-bottom:1px solid " + C.line }, [
      el("span", { text: inc.severity, style: "font:10px " + C.mono + ";letter-spacing:.1em;text-transform:uppercase;border-radius:6px;padding:2px 7px;margin-right:8px;" + (SEV[inc.severity] || SEV.notice) }),
      el("span", { text: inc.title, style: "font-weight:600" }),
      el("div", { text: meta.join(" · "), style: "font:11px " + C.mono + ";color:" + C.faint + ";margin:4px 0 0" }),
      note ? el("div", { text: note, style: "font:11.5px " + C.mono + ";color:" + C.rose + ";margin-top:4px" }) : null,
      plan, acts, said]);
  }

  function badge(count) {
    var b = document.getElementById("keith-badge");
    if (!count) { if (b) b.remove(); return; }
    if (!b) {
      b = el("button", { id: "keith-badge", type: "button",
        style: "position:fixed;top:12px;right:12px;z-index:9998;font:12px " + C.mono +
               ";background:" + C.roseBg + ";border:1px solid " + C.rose + ";color:" + C.ink +
               ";border-radius:20px;padding:5px 12px;cursor:pointer" });
      b.onclick = function () { closedThisLoad = false; show(latest); };
      document.body.appendChild(b);
    }
    b.textContent = "⚑ " + count + " open alert" + (count === 1 ? "" : "s");
  }

  function close() {
    var s = document.getElementById("keith-alerts");
    if (s) s.remove();
    closedThisLoad = true;
  }

  function show(data) {
    var open = (data && data.open) || [];
    var old = document.getElementById("keith-alerts");
    if (old) old.remove();
    if (!open.length || closedThisLoad) return;
    var email = data.email || {};
    var list = el("div", { style: "max-height:62vh;overflow-y:auto" }, open.map(item));
    var closeBtn = el("button", { type: "button", "class": "keith-close", style: BTN, text: "Close until the next page I open" });
    closeBtn.onclick = close;
    var foot = el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 18px;font-size:12px;color:" + C.muted }, [
      el("span", { text: "Closed this week: " + ((data.closed || []).length) +
        (email.ready ? " · urgent alerts are emailed to " + email.to
                     : " · urgent email is off: " + (email.why || "not set up")) }),
      closeBtn]);
    var modal = el("div", { role: "dialog", "aria-modal": "true", "aria-labelledby": "keith-title",
      style: "width:min(720px,94vw);background:" + C.panel + ";border:1px solid " + C.line +
             ";border-radius:16px;box-shadow:0 20px 60px rgba(0,0,0,.5);color:" + C.ink + ";font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif" }, [
      el("div", { style: "display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 18px;border-bottom:1px solid " + C.line }, [
        el("h2", { id: "keith-title", text: "Keith: " + open.length + " open alert" + (open.length === 1 ? "" : "s"),
                   style: "font-family:" + C.serif + ";font-size:19px;margin:0" }),
        el("small", { text: "shown on every admin page until each is resolved", style: "font:11px " + C.mono + ";color:" + C.muted })]),
      list, foot]);
    var scrim = el("div", { id: "keith-alerts",
      style: "position:fixed;inset:0;z-index:9999;background:rgba(10,8,14,.72);display:flex;align-items:flex-start;justify-content:center;padding:40px 12px;overflow-y:auto" }, [modal]);
    scrim.addEventListener("click", function (e) { if (e.target === scrim) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });
    document.body.appendChild(scrim);
  }

  function refresh() {
    return api("/api/admin/incidents").then(function (data) {
      latest = data;
      var open = data.open || [];
      badge(open.length);
      if (!shownThisLoad || document.getElementById("keith-alerts")) {
        shownThisLoad = shownThisLoad || open.length > 0;
        show(data);
      }
      return true;
    });
  }

  // Until the owner has signed in the API answers 404: ask again until it
  // answers, then keep the badge fresh every five minutes.
  function start() {
    refresh().then(function () {
      setInterval(function () { refresh().catch(function () {}); }, 300000);
    }).catch(function () { setTimeout(start, 15000); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
