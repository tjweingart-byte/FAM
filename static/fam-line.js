/*
 * FamLine - the episode's illustration, drawn by the audio.
 *
 * One idea, and everything here follows from it:
 *
 *     progress = clamp(currentTime / duration, 0, 1)
 *
 * The reveal is a pure function of playback position. Nothing here runs a
 * timer of its own, counts frames, or remembers how much it has drawn. That is
 * what makes pause, resume, seek, rewind and 1.5x/2x all work without a line
 * of code each: they move the audio position, and the drawing is wherever the
 * audio is. An animation with its own clock would need five special cases and
 * would drift out of step with the episode in all of them.
 *
 * It is also the answer to the hardest case, which is search. The vector may
 * arrive thirty seconds into an episode that is already playing. There is no
 * "catch up" path and no restart: the first frame after it arrives is drawn at
 * whatever the formula says, which is 30/180 = 17%, and it continues from
 * there. The visual joins the episode where the episode is.
 *
 * Mechanically this is one <path> with stroke-dasharray set to its own length
 * and stroke-dashoffset animated from that length to zero. The length is
 * measured once, when the path is set - `getTotalLength()` walks the geometry,
 * and doing it per frame is how a smooth reveal becomes a stuttering one.
 *
 * exploreFAM never calls anything in here. Its player builds its own call to
 * FamAudio and does not go through `speakText`, so the exclusion is structural
 * rather than a flag somebody has to remember to set.
 */
window.FamLine = (function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  /* How often to ask whether the drawing is ready yet. The server never
     generates on this call, so polling cannot cost anything; this is paced for
     the listener's battery, not the bill. */
  var POLL_MS = 2500;
  /* Stop asking after this long. An episode is at most ten minutes and a
     drawing that is not finished by now is not coming. */
  var POLL_CEILING_MS = 240000;
  /* Fade the line in over this long when it arrives mid-episode, so it appears
     rather than pops. The reveal itself is never animated - see above. */
  var FADE_MS = 420;

  var hosts = [];
  var clock = null;
  var episode = null;        // { query, context, surface }
  var current = null;        // the drawing being shown
  var token = 0;
  var pollTimer = null;
  var pollStarted = 0;
  var frame = null;
  /* Paths already fetched this session, by id. A listener who replays an
     episode, switches voice or comes back to it should not re-fetch geometry
     that cannot have changed - the id contains the style version. */
  var seen = {};

  function init(options) {
    options = options || {};
    clock = options.clock || null;
  }

  /* Build the ivory square once per surface that wants one. Returns the host
     record so the caller can keep it; everything after this only writes
     attributes, never HTML. */
  function mount(element) {
    if (!element) return null;
    var host = {
      root: element,
      svg: null,
      path: null,
      badge: null,
      length: 0,
      lastOffset: null,
    };
    element.innerHTML = "";
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 1000 1000");
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "line-svg");
    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("fill", "none");
    /* Placeholders until /api/visual supplies the real values in `apply`.
       `visual_style.INK` and `STROKE_WIDTH` are the source of truth; nothing
       is drawn before the response arrives, so these are never seen. */
    path.setAttribute("stroke", "#171820");
    path.setAttribute("stroke-width", "1.4");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    path.setAttribute("class", "line-path");
    svg.appendChild(path);
    element.appendChild(svg);

    var badge = document.createElement("span");
    badge.className = "line-badge";
    badge.hidden = true;
    badge.textContent = "placeholder art";
    /* Said out loud, on the picture. Synthetic line art is not FAM artwork,
       and this project's most expensive habit has been letting a stand-in look
       like the real thing. */
    badge.title = "Drawn locally without an image model. Not FAM artwork.";
    element.appendChild(badge);

    host.svg = svg;
    host.path = path;
    host.badge = badge;
    hosts.push(host);
    return host;
  }

  function eachHost(fn) {
    for (var i = 0; i < hosts.length; i++) fn(hosts[i]);
  }

  /* Back to ivory. Shared by begin() and clear(), which were doing the same
     six lines each and are the two places a missed one would leave the last
     episode's drawing on the next episode's canvas. */
  function blank(host) {
    host.root.classList.remove("has-line");
    host.root.classList.add("blank");
    host.path.setAttribute("d", "");
    host.path.style.opacity = "0";
    host.badge.hidden = true;
    host.length = 0;
  }

  /* ---- the episode ------------------------------------------------- */

  /* A new episode started. The canvas goes blank and the reveal goes to zero,
     even when the drawing is already in hand: readiness and reveal are
     different things, and a tile whose picture is finished still starts its
     player at 0% and draws it again with the audio. */
  function begin(options) {
    options = options || {};
    token++;
    episode = { query: options.query || "", context: options.context || "",
                surface: options.surface || "" };
    current = null;
    eachHost(function (host) {
      host.lastOffset = null;
      host.path.style.strokeDasharray = "";
      host.path.style.strokeDashoffset = "";
      blank(host);
    });
    stopPolling();
    startLoop();
    pollStarted = Date.now();
    poll(token);
    return true;
  }

  /* No episode. Used when the player closes, and when a surface that must not
     have a drawing takes over - see the note about exploreFAM at the top. */
  function clear() {
    token++;
    episode = null;
    current = null;
    stopPolling();
    stopLoop();
    eachHost(blank);
  }

  /* Show a drawing that is already in hand - a myFAM tile whose visual came
     down with the feed. Saves the first poll, and nothing else differs. */
  function offer(visual) {
    if (!visual || visual.status !== "ready" || !visual.d) return false;
    apply(visual, true);
    return true;
  }

  /* ---- asking the server ------------------------------------------- */

  function poll(myToken) {
    if (myToken !== token || !episode || !episode.query) return;
    var url = "/api/visual?q=" + encodeURIComponent(episode.query)
      + (episode.context ? "&context=" + encodeURIComponent(episode.context) : "");
    fetch(url, { credentials: "same-origin" })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (body) {
        if (myToken !== token) return;
        var visual = body && body.visual;
        if (!visual) { schedule(myToken); return; }
        if (visual.status === "ready") {
          apply(visual, false);
          return;
        }
        if (visual.status === "failed" || visual.status === "unconfigured") {
          /* A designed state, not an error. The episode plays, the square
             stays ivory, and nothing is said to the listener - there is
             nothing they could do about it. The reason is on /api/health. */
          report("visual_asset_failed", visual.id || "", visual.status);
          return;
        }
        schedule(myToken);
      })
      .catch(function () { schedule(myToken); });
  }

  function schedule(myToken) {
    if (myToken !== token || !episode) return;
    if (Date.now() - pollStarted > POLL_CEILING_MS) return;
    stopPolling();
    pollTimer = setTimeout(function () { poll(myToken); }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  /* ---- showing it --------------------------------------------------- */

  function apply(visual, fromFeed) {
    var cached = !!seen[visual.id];
    seen[visual.id] = true;
    current = visual;
    stopPolling();
    var ok = false;
    eachHost(function (host) {
      try {
        host.path.setAttribute("d", visual.d);
        if (visual.view_box) host.svg.setAttribute("viewBox", visual.view_box);
        if (visual.stroke_color) host.path.setAttribute("stroke", visual.stroke_color);
        if (visual.stroke_width) {
          host.path.setAttribute("stroke-width", String(visual.stroke_width));
        }
        if (visual.background_color) {
          host.root.style.background = visual.background_color;
        }
        /* Measured once. The whole reveal is one number written per frame
           after this, and re-measuring per frame is the difference between a
           smooth line and a stuttering one on a long path. */
        host.length = host.path.getTotalLength();
        if (!isFinite(host.length) || host.length <= 0) throw new Error("empty path");
        host.path.style.strokeDasharray = host.length + " " + host.length;
        host.path.style.strokeDashoffset = String(host.length);
        host.path.style.transition = "opacity " + FADE_MS + "ms ease";
        host.path.style.opacity = "1";
        host.root.classList.remove("blank");
        host.root.classList.add("has-line");
        host.badge.hidden = !visual.placeholder;
        host.lastOffset = null;
        ok = true;
      } catch (err) {
        host.path.setAttribute("d", "");
        host.length = 0;
        report("visual_parse_failed", visual.id || "", String(err && err.message));
      }
    });
    if (ok) {
      report(cached ? "visual_asset_cache_hit" : "visual_asset_loaded",
             visual.id || "", fromFeed ? "from the feed" : "polled");
      paint();
    }
  }

  /* ---- the reveal ---------------------------------------------------- */

  function progress() {
    if (!clock) return 0;
    var now = clock();
    if (!now || !now.duration || now.duration <= 0) return 0;
    var share = now.position / now.duration;
    if (!isFinite(share)) return 0;
    return share < 0 ? 0 : (share > 1 ? 1 : share);
  }

  function paint() {
    var share = progress();
    eachHost(function (host) {
      if (!host.length) return;
      var offset = host.length * (1 - share);
      /* Writing an unchanged value still costs a style recalculation on some
         browsers, and this runs sixty times a second underneath streaming
         audio. A tenth of a unit is far below one device pixel. Kept per host
         rather than globally: two surfaces can be mounted at different sizes,
         and one shared "last value" would silence the other one's updates. */
      if (host.lastOffset !== null && Math.abs(offset - host.lastOffset) < 0.1) {
        return;
      }
      host.lastOffset = offset;
      host.path.style.strokeDashoffset = String(offset);
    });
  }

  function tick() {
    frame = null;
    if (!episode) return;
    paint();
    startLoop();
  }

  function startLoop() {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(tick);
  }

  function stopLoop() {
    if (frame !== null) { window.cancelAnimationFrame(frame); frame = null; }
  }

  /* The browser stops firing animation frames on a hidden tab and on a locked
     phone. Nothing here needs catching up - the position is read fresh on the
     next frame - but the first frame back must not be skipped by the
     "unchanged offset" guard, so the cached value is thrown away. */
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) return;
    eachHost(function (host) { host.lastOffset = null; });
    if (episode) { stopLoop(); startLoop(); }
  });

  /* ---- telemetry ----------------------------------------------------- */
  /* What only the client knows. The server can say it produced a drawing; it
     cannot say a browser parsed one, drew one, or fell over on one, and the
     gap between "generated" and "seen" is exactly the sort this project has
     been caught by before. Failures here are silent by design: a telemetry
     post must never be able to interrupt an episode. */
  function report(event, id, detail) {
    try {
      fetch("/api/visual/event", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event: event, visual_id: id || "",
                               detail: (detail || "").slice(0, 200) })
      }).catch(function () {});
    } catch (e) {}
  }

  return {
    init: init,
    mount: mount,
    begin: begin,
    clear: clear,
    offer: offer,
    /* Repaint now rather than on the next frame. Called after a seek, so the
       line lands with the audio instead of a frame later. */
    sync: function () {
      eachHost(function (host) { host.lastOffset = null; });
      paint();
    },
    progress: progress,
    current: function () { return current; },
    isShowing: function () { return !!(current && current.status === "ready"); }
  };
})();
