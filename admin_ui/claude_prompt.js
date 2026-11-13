/* "Copy for Claude" (§598): one community lead as a prompt to paste into
   Claude with a question underneath. Shared by /admin/groups' cards and
   Egan's section on /admin, so the two copy the same text. Only what a
   card shows; no phone number is ever stored. */
(function () {
  "use strict";
  function famClaudePrompt(k, o) {
    o = o || {};
    var plat = (o.platforms || {})[k.platform] || { label: k.platform, noun: "community" };
    var statuses = o.statuses || {};
    var notes = o.notes != null ? o.notes : (k.notes || "");
    var handle = o.handle != null ? o.handle : (k.handle || "");
    var msg = o.message != null ? o.message : (k.message || "");
    var facts = [plat.label + " " + plat.noun];
    if (k.privacy) facts.push(k.privacy);
    if (k.members) facts.push("about " + Number(k.members).toLocaleString() + " members");
    var lines = [
      "I'm reaching out to an online community on behalf of FAM, an app that turns what people care about " +
      "into short, researched audio stories. We offer community admins free early access so they can share " +
      "episodes with their members. I contact them by hand; nothing is automated.",
      "",
      "Community: " + (k.name || k.ident) + " (" + facts.join(", ") + ")",
      "Link: " + (k.url || ""),
      "Subject we'd pitch it on: " + (k.subject || "")
    ];
    if (k.description) lines.push("What its page says: " + k.description);
    lines.push("How to reach the admins: " + (k.how || "") + (k.contact_url ? " (" + k.contact_url + ")" : ""));
    if (k.public_contact && k.public_contact.length) lines.push("Contact the community publishes: " + k.public_contact.join(", "));
    if (handle) lines.push("Admin's handle, as I found it: " + handle);
    lines.push("Status: " + (statuses[k.status] || k.status) + (notes ? ". My notes: " + notes : ""));
    lines.push("Fit: " + k.fit + "/5" + (k.fit_why ? " (" + k.fit_why + ")" : ""));
    if (k.source === "sent" && k.note) lines.push("What we knew when we picked it: " + k.note);
    if ((k.pitches || []).length) {
      lines.push("", "Episode ideas for its members (ideas, not yet made):");
      (k.pitches || []).forEach(function (p, i) {
        lines.push((i + 1) + ". " + p.title + (p.angle ? ": " + p.angle : "") + (p.why ? " (" + p.why + ")" : ""));
      });
    }
    if (k.episode) lines.push("", "An episode FAM already has that fits: “" + k.episode.title + "” (" +
      k.episode.minutes + " min)" + (k.share_url ? ", " + k.share_url : ""));
    if (msg) lines.push("", (k.status === "new" ? "The message I plan to send:" : "The message I sent:"), "“" + msg + "”");
    lines.push("", "My question:", "");
    return lines.join("\n");
  }

  // The clipboard where the browser allows it, else a selected box the
  // person copies themselves (a prompt() is one line).
  function famCopyLong(text, b) {
    var done = function () { b.textContent = "Copied - paste into Claude"; };
    var fallback = function () {
      var box = document.createElement("textarea");
      box.value = text; box.style.width = "100%"; box.style.minHeight = "220px";
      b.parentNode.insertAdjacentElement("afterend", box);
      box.focus(); box.select();
      b.textContent = "Select all and copy below";
    };
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, fallback);
        return;
      }
    } catch (e) {}
    fallback();
  }

  window.famClaudePrompt = famClaudePrompt;
  window.famCopyLong = famCopyLong;
})();
