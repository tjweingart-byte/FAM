/*
 * FamLine - the continuous-line episode visual.
 *
 * One uninterrupted stroke, revealed as the episode plays, complete when it
 * ends. The completed frame is the episode's thumbnail. "A single line
 * connecting us all."
 *
 * Three rules shape everything here.
 *
 * 1. **The reveal is a function of playback position, never of a clock.**
 *    There is no animation timer in this file. Whoever is already painting a
 *    progress bar calls `progress(fraction)`, and that fraction is
 *    `position / duration` straight off FamAudio. Pause freezes it because
 *    position stops moving; seek jumps it because position jumped; 1.5x stays
 *    in step because a normalised position does not care about rate; rewinding
 *    un-draws the line because the fraction went down. None of those are
 *    special cases in the code, which is the point of not having a clock.
 *
 * 2. **Audio comes first, always.** Nothing in this file is awaited by the
 *    audio path. The asset is fetched after playback has been asked for, every
 *    failure resolves to "draw nothing", and a visual that arrives late simply
 *    arrives at the position the episode has already reached.
 *
 * 3. **One path or nothing.** The visual language is that the pen never
 *    leaves the page. An SVG with two drawable elements would reveal as two
 *    strokes appearing in sequence, which reads as a different product. The
 *    server rejects those before they get here; this file refuses them again
 *    rather than trusting that it did, because a silent multi-stroke reveal is
 *    exactly the kind of quietly-wrong output this project keeps paying for.
 *
 * Geometry is parsed once per asset and cached: a tick sets one CSS property
 * on one element and does no measuring, no parsing and no layout of its own.
 *
 * Public surface:
 *   FamLine.attach(element)      where to draw
 *   FamLine.show(record)         a record from GET /api/visual
 *   FamLine.progress(fraction)   0..1, from playback position
 *   FamLine.hide()               leave no square behind
 *   FamLine.status()             "" | "processing" | "ready" | "failed"
 *   FamLine.telemetry()          the events below, newest last
 */
window.FamLine = (function () {
  "use strict";

  var SVG_NS = "http://www.w3.org/2000/svg";
  // Assets are tens of kilobytes of text. A week is long enough that a daily
  // listener never refetches one, and short enough that a corrupted row is
  // never permanent.
  var CACHE_TTL_MS = 7 * 24 * 3600 * 1000;
  var DB_NAME = "fam-visuals";
  var STORE = "assets";
  // Below this the reveal is invisible and the write is wasted. At 1/2000 a
  // ten-minute episode still gets a smooth draw.
  var PROGRESS_EPSILON = 0.0005;

  var mount = null;          // the square element we own
  var frame = null;          // the <svg> inside it
  var line = null;           // the one <path>
  var length = 0;            // its total length, measured once
  var hairline = 1.4;        // the asked-for stroke width, in CSS pixels
  var watcher = null;        // ResizeObserver, to keep the hairline a hairline
  var state = "";            // "", processing, ready, failed
  var wanted = 0;            // last progress asked for, applied when ready
  var painted = -1;          // last progress actually written
  var token = 0;             // which show() owns the mount
  var events = [];
  var counts = {};

  /* ---------------------------------------------------------- telemetry */

  /* FAM has no client analytics pipeline - /api/event is the taste log and
     putting render failures in it would teach the recommender about SVGs. So
     these land in a ring buffer and the console, and the server counts its own
     half on /api/health. When there is a pipeline, this is the one function
     that has to change. */
  function record(name, detail) {
    counts[name] = (counts[name] || 0) + 1;
    events.push({ at: Date.now(), event: name, detail: detail === undefined ? null : detail });
    if (events.length > 60) events.shift();
    if (window.console && console.debug) {
      console.debug("[fam.visual] " + name, detail === undefined ? "" : detail);
    }
  }

  /* -------------------------------------------------------------- cache */

  /* Its own database rather than a second store inside `fam-offline`. That one
     holds downloaded episodes - the audio a listener explicitly asked to keep -
     and the rule for this feature is that it cannot interfere with playback.
     Sharing a connection and a version number with the audio store is exactly
     how it would. */
  function db() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) { reject(new Error("no indexedDB")); return; }
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        if (!req.result.objectStoreNames.contains(STORE)) {
          req.result.createObjectStore(STORE, { keyPath: "url" });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }

  function cacheGet(url) {
    return db().then(function (d) {
      return new Promise(function (resolve) {
        var req = d.transaction(STORE, "readonly").objectStore(STORE).get(url);
        req.onsuccess = function () {
          var row = req.result;
          if (!row || !row.svg) { resolve(null); return; }
          if (Date.now() - (row.at || 0) > CACHE_TTL_MS) { resolve(null); return; }
          resolve(row.svg);
        };
        req.onerror = function () { resolve(null); };
      });
    }).catch(function () { return null; });
  }

  function cachePut(url, svg) {
    return db().then(function (d) {
      var tx = d.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put({ url: url, svg: svg, at: Date.now() });
      return new Promise(function (resolve) { tx.oncomplete = resolve; tx.onerror = resolve; });
    }).catch(function () { /* a cache that will not write is not an error */ });
  }

  function cacheDrop(url) {
    return db().then(function (d) {
      var tx = d.transaction(STORE, "readwrite");
      tx.objectStore(STORE).delete(url);
      return new Promise(function (resolve) { tx.oncomplete = resolve; tx.onerror = resolve; });
    }).catch(function () {});
  }

  /* ------------------------------------------------------------ parsing */

  /* Pull the one drawable path out of an SVG document, or say why there isn't
     one. Returns { d, viewBox } or throws with a sentence. */
  function parseOnePath(text) {
    var doc = new DOMParser().parseFromString(text, "image/svg+xml");
    if (doc.getElementsByTagName("parsererror").length) {
      throw new Error("that file is not parseable SVG");
    }
    var root = doc.documentElement;
    if (!root || String(root.nodeName).toLowerCase() !== "svg") {
      throw new Error("that file is not an SVG");
    }
    var drawable = root.querySelectorAll(
      "path, circle, ellipse, rect, line, polyline, polygon");
    var paths = [];
    for (var i = 0; i < drawable.length; i++) {
      var el = drawable[i];
      var name = String(el.nodeName).toLowerCase();
      if (name === "path" && !String(el.getAttribute("d") || "").trim()) continue;
      paths.push(el);
    }
    if (!paths.length) throw new Error("that file contains no drawable path");
    if (paths.length > 1) {
      throw new Error("that file has " + paths.length + " drawable elements; a "
                      + "FAM visual is exactly one continuous path");
    }
    if (String(paths[0].nodeName).toLowerCase() !== "path") {
      throw new Error("that file's one drawable element is not a <path>");
    }
    var fill = String(paths[0].getAttribute("fill") || "").trim().toLowerCase();
    if (fill && fill !== "none" && fill !== "transparent") {
      throw new Error("that path is filled; a FAM visual is stroke only");
    }
    return {
      d: paths[0].getAttribute("d"),
      viewBox: (root.getAttribute("viewBox") || "").trim()
    };
  }

  /* ------------------------------------------------------------ drawing */

  function clearMount() {
    unwatch();
    if (!mount) return;
    mount.innerHTML = "";
    frame = null; line = null; length = 0; painted = -1;
  }

  function showSquare(record) {
    if (!mount) return;
    mount.style.background = (record && record.background_color) || "#F8F4EA";
    mount.hidden = false;
    mount.classList.add("on");
  }

  function build(record, geometry) {
    clearMount();
    var svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", geometry.viewBox || record.view_box || "0 0 1000 1000");
    // The square is the frame; the drawing keeps its own proportions inside it.
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");

    var path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", geometry.d);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", record.stroke_color || "#171820");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    mount.appendChild(svg);

    // Measured once, after insertion: a detached path measures zero in some
    // engines, and a zero-length path never draws.
    var total = 0;
    try { total = path.getTotalLength(); } catch (e) { total = 0; }
    if (!total || !isFinite(total)) throw new Error("that path has no length");

    frame = svg; line = path; length = total;
    hairline = Number(record.stroke_width) || 1.4;
    // The dash pattern is in the path's own user units, which is the only
    // coordinate system `getTotalLength` speaks. This is why the stroke width
    // is converted below rather than pinned with `vector-effect:
    // non-scaling-stroke`: that attribute moves dash lengths into *screen*
    // units while the measured length stays in user units, so on a 1000-unit
    // asset drawn at 240px the dash was fifteen times longer than the path and
    // the whole drawing appeared at once. It looked like a working reveal in
    // every frame except the first, which is exactly the kind of quietly-wrong
    // output this project keeps paying for.
    path.style.strokeDasharray = total + " " + total;
    path.style.strokeDashoffset = String(total);
    applyStrokeWidth();
    watch();
    painted = -1;
    paint(wanted);
  }

  /* `stroke_width` is read as CSS pixels at the rendered size, not as user
     units: a 1.4 hairline is meant to look like a 1.4 hairline whether the
     square is 240px on a phone or 600px on a desktop, and an asset drawn on a
     1000-unit canvas has no way to know which. Converted here, and again
     whenever the square changes size. */
  function applyStrokeWidth() {
    if (!line || !frame || !mount) return;
    var box = (frame.getAttribute("viewBox") || "").split(/[\s,]+/);
    var units = Math.max(Number(box[2]) || 0, Number(box[3]) || 0) || 1000;
    var pixels = Math.max(mount.clientWidth || 0, mount.clientHeight || 0);
    if (!pixels) return;
    line.setAttribute("stroke-width", String(hairline * units / pixels));
  }

  function watch() {
    if (watcher || !mount || !window.ResizeObserver) return;
    watcher = new ResizeObserver(function () { applyStrokeWidth(); });
    try { watcher.observe(mount); } catch (e) { watcher = null; }
  }

  function unwatch() {
    if (!watcher) return;
    try { watcher.disconnect(); } catch (e) {}
    watcher = null;
  }

  /* One property, one element, no measuring. This is the whole per-tick cost. */
  function paint(fraction) {
    if (!line || !length) return;
    var p = fraction;
    if (!isFinite(p)) p = 0;
    if (p < 0) p = 0;
    if (p > 1) p = 1;
    if (painted >= 0 && Math.abs(p - painted) < PROGRESS_EPSILON) return;
    painted = p;
    line.style.strokeDashoffset = String(length * (1 - p));
  }

  /* ---------------------------------------------------------- lifecycle */

  function attach(element) {
    mount = element || null;
    return !!mount;
  }

  function hide() {
    token++;
    state = "";
    wanted = 0;
    if (!mount) return;
    clearMount();
    mount.hidden = true;
    mount.classList.remove("on");
  }

  function fetchAsset(url) {
    record("visual_asset_request", url);
    return cacheGet(url).then(function (cached) {
      if (cached) { record("visual_asset_cache_hit", url); return cached; }
      return fetch(url, { credentials: "same-origin" }).then(function (res) {
        if (!res.ok) throw new Error("the artwork could not be fetched (" + res.status + ")");
        return res.text();
      }).then(function (text) {
        cachePut(url, text);
        return text;
      });
    });
  }

  /* Draw this episode's visual, or decide not to. Never rejects: the caller is
     a player, and a player must not have to handle an artwork failure. */
  function show(record_) {
    var mine = ++token;
    wanted = 0;
    var rec = record_ || {};
    state = rec.status || "none";

    if (!mount || state === "none" || state === "failed") {
      // No square at all. The player has never had artwork, so an ivory box
      // that stays empty forever would read as something broken rather than
      // something absent - and an episode with no drawing is the ordinary
      // case, not a fault. `failed` is told apart in telemetry, not on screen.
      if (state === "failed") record("visual_asset_failed", rec.reason || "");
      hide();
      state = rec.status || "none";
      return Promise.resolve(false);
    }

    clearMount();
    showSquare(rec);

    if (state !== "ready" || !rec.vector_url) {
      // processing: the square is a promise being kept, and the episode plays
      // over it exactly as it would have done.
      return Promise.resolve(false);
    }

    // The version is the cache key, because an asset is immutable for a given
    // id: new artwork for the same subject arrives as a new version. Skipped
    // for a data: URL, where a query string would become part of the document
    // rather than a parameter to it.
    var url = rec.vector_url;
    if (rec.version && url.indexOf("data:") !== 0) {
      url += (url.indexOf("?") >= 0 ? "&" : "?") + "v=" + encodeURIComponent(rec.version);
    }
    var started = (window.performance && performance.now) ? performance.now() : Date.now();
    return fetchAsset(url).then(function (text) {
      if (mine !== token) return false;
      var geometry;
      try {
        geometry = parseOnePath(text);
      } catch (err) {
        // A cached row that no longer parses is corruption; drop it so the
        // next play refetches instead of failing forever.
        cacheDrop(url);
        record("visual_parse_failed", err.message);
        hide();
        state = "failed";
        return false;
      }
      try {
        build(rec, geometry);
      } catch (err) {
        record("visual_render_error", err.message);
        hide();
        state = "failed";
        return false;
      }
      var now = (window.performance && performance.now) ? performance.now() : Date.now();
      record("visual_asset_loaded", url);
      record("visual_ready_latency_ms", Math.round(now - started));
      record("visual_first_draw_ms", Math.round(now - started));
      return true;
    }).catch(function (err) {
      if (mine !== token) return false;
      record("visual_asset_failed", (err && err.message) || "unknown");
      hide();
      state = "failed";
      return false;
    });
  }

  /* The only entry point a player calls on every tick. Cheap by construction. */
  function progress(fraction) {
    if (!isFinite(fraction)) return;
    wanted = fraction < 0 ? 0 : (fraction > 1 ? 1 : fraction);
    paint(wanted);
  }

  return {
    attach: attach,
    show: show,
    progress: progress,
    hide: hide,
    status: function () { return state; },
    // What is on screen right now, for tests and for the console.
    drawn: function () { return painted < 0 ? 0 : painted; },
    pathLength: function () { return length; },
    telemetry: function () { return events.slice(); },
    counts: function () { var out = {}; for (var k in counts) out[k] = counts[k]; return out; },
  };
})();
