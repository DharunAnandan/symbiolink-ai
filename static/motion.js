/* =====================================================================
   SymbioLink AI — shared motion system
   One file, included from both layout.html (full app shell) and
   auth_layout.html (login/register), so the ambient network canvas, the
   scroll-reveal system, the tilt effect, and the stat counter all behave
   identically everywhere instead of drifting apart as copy-pasted inline
   scripts in 20 templates. Everything here checks prefers-reduced-motion
   and backs off to a static, fully-visible result rather than skipping
   content.
   ===================================================================== */
(function () {
  "use strict";

  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------------------------------------------------------------------
     1. Ambient network canvas — the site-wide signature visual. A field of
        depth-layered nodes drifting slowly, connected by gradient lines
        when close enough, with small light-pulses that travel along active
        connections — a direct visual metaphor for what SymbioLink AI
        actually does (material/value flowing from one unit to another
        across the cluster), styled to evoke the real cluster graph on the
        Matching Engine page (see templates/matches.html). Nodes have a
        simulated depth (z) so nearer ones are bigger, brighter and drift
        faster than farther ones, and gently part around the pointer on
        precise-pointer devices for a subtle interactive, tactile feel.
        Density scales down on small screens; pauses when the tab is
        hidden so it never burns CPU in a background tab; collapses to one
        static, non-animated frame under prefers-reduced-motion (no pulses,
        no drift, no pointer interaction).
     --------------------------------------------------------------------- */
  function initNetworkCanvas() {
    var canvas = document.getElementById("bg-network");
    if (!canvas || !canvas.getContext) return;
    var ctx = canvas.getContext("2d");
    var w, h, dpr = Math.min(window.devicePixelRatio || 1, 2);
    var nodes = [];
    var pulses = [];
    var raf = null;
    var running = true;
    var lastTs = null;
    var pulseCooldown = 0;

    var palette = ["22,163,74", "34,211,238", "167,139,250"];

    var pointerFine = !reduceMotion && window.matchMedia && window.matchMedia("(pointer: fine)").matches;
    var pointer = { x: -9999, y: -9999, active: false };
    var pointerRaf = null, lastPointerEvent = null;

    function onPointerMove(e) {
      lastPointerEvent = e;
      if (pointerRaf) return;
      pointerRaf = window.requestAnimationFrame(function () {
        pointer.x = lastPointerEvent.clientX;
        pointer.y = lastPointerEvent.clientY;
        pointer.active = true;
        pointerRaf = null;
      });
    }

    function resize() {
      w = window.innerWidth;
      h = window.innerHeight;
      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      var count = Math.max(18, Math.min(46, Math.round((w * h) / 42000)));
      nodes = [];
      for (var i = 0; i < count; i++) {
        // Simulated depth: 0 = far (small/slow/dim), 1 = near (big/fast/bright).
        // Everything about a node's look and motion derives from this one
        // value, which is what sells the illusion of a 3D field rather than
        // a flat sprinkle of identical dots.
        var z = 0.3 + Math.random() * 0.7;
        nodes.push({
          x: Math.random() * w,
          y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.11 * (0.55 + z),
          vy: (Math.random() - 0.5) * 0.11 * (0.55 + z),
          r: (0.9 + Math.random() * 1.3) * (0.65 + z * 0.75),
          z: z,
          c: palette[i % palette.length],
          phase: Math.random() * Math.PI * 2,
          phaseSpeed: 0.0007 + Math.random() * 0.0009
        });
      }
      pulses = [];
    }

    function step(ts) {
      if (!running) return;
      if (!lastTs) lastTs = ts;
      var dt = Math.min(ts - lastTs, 48); // clamp so a tab-switch gap doesn't jump the sim
      lastTs = ts;

      ctx.clearRect(0, 0, w, h);
      var linkDist = Math.min(170, w / 6);
      var activeEdges = [];

      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        n.x += n.vx * (dt / 16.6);
        n.y += n.vy * (dt / 16.6);

        // Nodes gently part around the pointer, nearer (bigger-z) ones
        // reacting more — a soft, tactile "the field notices you" cue
        // rather than a hard repulsion.
        if (pointer.active) {
          var pdx = n.x - pointer.x, pdy = n.y - pointer.y;
          var pdist = Math.sqrt(pdx * pdx + pdy * pdy);
          var repelRadius = 130;
          if (pdist < repelRadius && pdist > 0.01) {
            var force = (1 - pdist / repelRadius) * 0.32 * (0.4 + n.z);
            n.x += (pdx / pdist) * force;
            n.y += (pdy / pdist) * force;
          }
        }

        if (n.x < -20) n.x = w + 20; if (n.x > w + 20) n.x = -20;
        if (n.y < -20) n.y = h + 20; if (n.y > h + 20) n.y = -20;
      }

      for (var a = 0; a < nodes.length; a++) {
        for (var b = a + 1; b < nodes.length; b++) {
          var dx = nodes[a].x - nodes[b].x, dy = nodes[a].y - nodes[b].y;
          var dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < linkDist) {
            var proximity = 1 - dist / linkDist;
            var depthFactor = (nodes[a].z + nodes[b].z) / 2;
            var alpha = proximity * 0.22 * (0.5 + depthFactor * 0.5);
            var grad = ctx.createLinearGradient(nodes[a].x, nodes[a].y, nodes[b].x, nodes[b].y);
            grad.addColorStop(0, "rgba(" + nodes[a].c + "," + alpha.toFixed(3) + ")");
            grad.addColorStop(1, "rgba(" + nodes[b].c + "," + alpha.toFixed(3) + ")");
            ctx.strokeStyle = grad;
            ctx.lineWidth = 0.5 + proximity * 0.9 * (0.5 + depthFactor * 0.6);
            ctx.beginPath();
            ctx.moveTo(nodes[a].x, nodes[a].y);
            ctx.lineTo(nodes[b].x, nodes[b].y);
            ctx.stroke();
            activeEdges.push(a, b);
          }
        }
      }

      // Small light-pulses travel along a handful of currently-active
      // connections at a time — the network's visual metaphor for material
      // actually flowing from one unit to another, not just a static graph.
      if (!reduceMotion) {
        pulseCooldown -= dt;
        if (pulseCooldown <= 0 && activeEdges.length && pulses.length < 7) {
          pulseCooldown = 380 + Math.random() * 500;
          var pick = Math.floor(Math.random() * (activeEdges.length / 2)) * 2;
          var flip = Math.random() < 0.5;
          pulses.push({
            a: flip ? activeEdges[pick + 1] : activeEdges[pick],
            b: flip ? activeEdges[pick] : activeEdges[pick + 1],
            t: 0,
            speed: 0.00042 + Math.random() * 0.00033
          });
        }
      }

      for (var p = pulses.length - 1; p >= 0; p--) {
        var pu = pulses[p];
        var na = nodes[pu.a], nb = nodes[pu.b];
        if (!na || !nb) { pulses.splice(p, 1); continue; }
        pu.t += pu.speed * dt;
        var pdx2 = nb.x - na.x, pdy2 = nb.y - na.y;
        var pdist2 = Math.sqrt(pdx2 * pdx2 + pdy2 * pdy2);
        if (pu.t >= 1 || pdist2 > linkDist * 1.5) { pulses.splice(p, 1); continue; }
        var ppx = na.x + pdx2 * pu.t;
        var ppy = na.y + pdy2 * pu.t;
        var fade = Math.sin(Math.PI * pu.t); // eases in, brightest mid-flight, eases out
        var pr = 6.5;
        var glow = ctx.createRadialGradient(ppx, ppy, 0, ppx, ppy, pr);
        glow.addColorStop(0, "rgba(" + na.c + "," + (0.85 * fade).toFixed(3) + ")");
        glow.addColorStop(1, "rgba(" + na.c + ",0)");
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(ppx, ppy, pr, 0, Math.PI * 2);
        ctx.fill();
      }

      for (var j = 0; j < nodes.length; j++) {
        var nn = nodes[j];
        var twinkle = reduceMotion ? 1 : 0.75 + Math.sin(ts * nn.phaseSpeed + nn.phase) * 0.25;
        var baseAlpha = (0.26 + nn.z * 0.4) * twinkle;

        var glowR = nn.r * 3.4;
        var g = ctx.createRadialGradient(nn.x, nn.y, 0, nn.x, nn.y, glowR);
        g.addColorStop(0, "rgba(" + nn.c + "," + baseAlpha.toFixed(3) + ")");
        g.addColorStop(1, "rgba(" + nn.c + ",0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(nn.x, nn.y, glowR, 0, Math.PI * 2);
        ctx.fill();

        ctx.beginPath();
        ctx.fillStyle = "rgba(" + nn.c + "," + Math.min(0.85, baseAlpha + 0.35).toFixed(3) + ")";
        ctx.arc(nn.x, nn.y, nn.r, 0, Math.PI * 2);
        ctx.fill();
      }

      if (!reduceMotion) raf = window.requestAnimationFrame(step);
    }

    resize();
    raf = window.requestAnimationFrame(step);
    if (!reduceMotion) {
      window.addEventListener("resize", function () {
        window.cancelAnimationFrame(raf);
        resize();
        lastTs = null;
        raf = window.requestAnimationFrame(step);
      });
      document.addEventListener("visibilitychange", function () {
        running = !document.hidden;
        if (running) {
          window.cancelAnimationFrame(raf);
          lastTs = null;
          raf = window.requestAnimationFrame(step);
        } else {
          window.cancelAnimationFrame(raf);
        }
      });
      if (pointerFine) {
        window.addEventListener("pointermove", onPointerMove, { passive: true });
      }
    }
  }

  /* ---------------------------------------------------------------------
     2. Scroll reveal — tags a deliberately-scoped set of below-the-fold
        elements (table rows, order/timeline cards, empty states, panels)
        with .reveal, then flips on .in-view the first time each crosses
        the viewport, staggering siblings by a small delay. Deliberately
        excludes .stat-card / .unit-card / .card / section / .search-bar,
        which already have their own load-time fadeInUp entrance in
        style.css — adding .reveal to those too would fight that animation.
     --------------------------------------------------------------------- */
  function initScrollReveal() {
    var selectors = [
      ".order-stat-card", ".order-card", ".timeline-event", ".quick-action-btn",
      ".empty-state", ".panel", ".detail-item", ".help-section", "tbody tr"
    ];
    selectors.forEach(function (sel) {
      var els = document.querySelectorAll(sel);
      els.forEach(function (el) {
        el.classList.add("reveal");
        var siblingIndex = Array.prototype.indexOf.call(
          el.parentElement ? el.parentElement.children : [el], el
        );
        var delay = Math.min(siblingIndex * 45, 480);
        el.style.setProperty("--reveal-delay", (delay / 1000) + "s");
      });
    });

    if (reduceMotion || !("IntersectionObserver" in window)) {
      document.querySelectorAll(".reveal").forEach(function (el) { el.classList.add("in-view"); });
      return;
    }

    // Cap instrumented rows per table to avoid observing hundreds of nodes
    // on very long admin/audit tables — reveal the first N, show the rest
    // immediately so scrolling a huge table doesn't wait on a stagger queue.
    document.querySelectorAll("table tbody").forEach(function (tbody) {
      var rows = tbody.querySelectorAll("tr");
      if (rows.length > 40) {
        rows.forEach(function (row, i) {
          if (i >= 40) { row.classList.remove("reveal"); row.classList.add("in-view"); }
        });
      }
    });

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add("in-view");
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });

    document.querySelectorAll(".reveal").forEach(function (el) { observer.observe(el); });
  }

  /* ---------------------------------------------------------------------
     3. Mouse-follow tilt — applied automatically to stat tiles (the
        elements most likely to be scanned at a glance), only when the
        input device is a precise pointer (skips touch entirely) and
        motion isn't reduced.
     --------------------------------------------------------------------- */
  function initTilt() {
    if (reduceMotion) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    var targets = document.querySelectorAll(".stat-card, .order-stat-card, .auth-visual");
    targets.forEach(function (el) {
      el.setAttribute("data-tilt", "");
      el.addEventListener("pointermove", function (e) {
        var rect = el.getBoundingClientRect();
        var px = (e.clientX - rect.left) / rect.width - 0.5;
        var py = (e.clientY - rect.top) / rect.height - 0.5;
        el.style.setProperty("--tilt-x", (px * 7).toFixed(2) + "deg");
        el.style.setProperty("--tilt-y", (-py * 7).toFixed(2) + "deg");
      });
      el.addEventListener("pointerleave", function () {
        el.style.setProperty("--tilt-x", "0deg");
        el.style.setProperty("--tilt-y", "0deg");
      });
    });
  }

  /* ---------------------------------------------------------------------
     4. Animated count-up for stat values, e.g.
        <div class="val" data-count="42" data-prefix="₹">42</div>
     --------------------------------------------------------------------- */
  function initCounters() {
    document.querySelectorAll(".stat-card .val[data-count], .order-stat-card .stat-number[data-count]").forEach(function (el) {
      var target = parseFloat(el.getAttribute("data-count")) || 0;
      var prefix = el.getAttribute("data-prefix") || "";
      var suffix = el.getAttribute("data-suffix") || "";
      if (reduceMotion) {
        el.textContent = prefix + Math.round(target).toLocaleString("en-IN") + suffix;
        return;
      }
      var duration = 850;
      var start = null;
      function frame(ts) {
        if (!start) start = ts;
        var progress = Math.min((ts - start) / duration, 1);
        var eased = 1 - Math.pow(1 - progress, 3);
        var current = Math.round(target * eased);
        el.textContent = prefix + current.toLocaleString("en-IN") + suffix;
        if (progress < 1) window.requestAnimationFrame(frame);
      }
      window.requestAnimationFrame(frame);
    });
  }

  /* ---------------------------------------------------------------------
     5. Toast-ified flash messages — adds a close button and a self-dismiss
        progress bar to flash messages only (.flash-messages .confirm),
        never to .confirm boxes used as persistent page content elsewhere
        (e.g. an order-created summary), which keep just their entrance pop
        from style.css and stay on screen. Hovering a toast pauses its timer
        so reading a longer message doesn't race the dismiss.
     --------------------------------------------------------------------- */
  function initToasts() {
    // .flash-messages .confirm covers the few routes that use Flask's real
    // flash() mechanism; .confirm-error covers the far more common pattern
    // in this app of routes passing `error` straight into the template and
    // rendering it inline (login, register, place-order, etc.) -- both are
    // transient feedback about the action just taken. Plain non-error
    // .confirm boxes outside .flash-messages (e.g. "Order #123 placed" on
    // order_new.html, a permanent record with details worth re-reading) are
    // deliberately left alone: no close button, no auto-dismiss timer.
    var toasts = document.querySelectorAll(".flash-messages .confirm, .confirm-error");
    toasts.forEach(function (el) {
      var closeBtn = document.createElement("button");
      closeBtn.type = "button";
      closeBtn.className = "confirm-close";
      closeBtn.setAttribute("aria-label", "Dismiss message");
      closeBtn.innerHTML = "&times;";
      el.appendChild(closeBtn);

      function dismiss() {
        if (el.classList.contains("dismissing")) return;
        el.classList.add("dismissing");
        el.addEventListener("animationend", function () { el.remove(); }, { once: true });
      }
      closeBtn.addEventListener("click", dismiss);

      if (reduceMotion) return;

      var progress = document.createElement("div");
      progress.className = "confirm-progress";
      el.appendChild(progress);

      var duration = 7000;
      var remaining = duration;
      var start = Date.now();
      progress.style.setProperty("--toast-duration", duration + "ms");
      var timer = window.setTimeout(dismiss, remaining);

      el.addEventListener("mouseenter", function () {
        window.clearTimeout(timer);
        remaining -= Date.now() - start;
        progress.style.animationPlayState = "paused";
      });
      el.addEventListener("mouseleave", function () {
        start = Date.now();
        timer = window.setTimeout(dismiss, Math.max(remaining, 400));
        progress.style.animationPlayState = "running";
      });
    });
  }

  /* ---------------------------------------------------------------------
     6. Button ripple — a short-lived circle expanding from the pointer's
        position on any .btn / submit button, delegated on the document so
        it works for buttons rendered after load (e.g. inside toggled
        details panels) without re-binding.
     --------------------------------------------------------------------- */
  function initButtonRipple() {
    if (reduceMotion) return;
    document.addEventListener("pointerdown", function (e) {
      if (e.button !== undefined && e.button !== 0) return;
      var btn = e.target.closest(".btn, button.submit, .card button[type=submit]");
      if (!btn || btn.disabled) return;
      var rect = btn.getBoundingClientRect();
      var size = Math.max(rect.width, rect.height) * 1.6;
      var span = document.createElement("span");
      span.className = "btn-ripple";
      span.style.width = span.style.height = size + "px";
      span.style.left = (e.clientX - rect.left - size / 2) + "px";
      span.style.top = (e.clientY - rect.top - size / 2) + "px";
      btn.appendChild(span);
      span.addEventListener("animationend", function () { span.remove(); });
    });
  }

  /* ---------------------------------------------------------------------
     7. Form feedback — shakes the first field the browser flags as invalid
        (native constraint validation, e.g. a required field left blank),
        and puts the submit button into a spinner state once a form does
        pass validation and is actually being posted. There's no client
        router here, so the spinner's only job is to bridge the gap between
        click and the full-page navigation that follows.
     --------------------------------------------------------------------- */
  function initFormFeedback() {
    document.addEventListener("invalid", function (e) {
      var field = e.target;
      if (reduceMotion) { field.style.borderColor = "var(--critical)"; return; }
      field.classList.remove("shake-invalid");
      void field.offsetWidth;
      field.classList.add("shake-invalid");
      field.addEventListener("animationend", function () {
        field.classList.remove("shake-invalid");
      }, { once: true });
    }, true);

    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!(form instanceof HTMLFormElement)) return;
      var submitBtn = form.querySelector("button[type=submit]:not([disabled])");
      if (submitBtn) submitBtn.classList.add("is-loading");
    });
  }

  /* ---------------------------------------------------------------------
     8. Parallax background orbs — writes normalized pointer position
        (-1..1 on each axis) to --px/--py on the root element, rAF-throttled
        so a fast mousemove burst only updates layout once per frame. The
        three .parallax-orbs span elements (injected in layout.html /
        auth_layout.html, right next to #bg-network) each read those two
        variables back through their own --depth multiplier in CSS, which is
        what actually produces the layered "orbs closer to you move more"
        parallax illusion — this function only ever sets two numbers.
     --------------------------------------------------------------------- */
  function initParallaxOrbs() {
    if (reduceMotion) return;
    if (!document.querySelector(".parallax-orbs")) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    var root = document.documentElement;
    var raf = null, lastEvent = null;
    window.addEventListener("pointermove", function (e) {
      lastEvent = e;
      if (raf) return;
      raf = window.requestAnimationFrame(function () {
        var px = (lastEvent.clientX / window.innerWidth - 0.5) * 2;
        var py = (lastEvent.clientY / window.innerHeight - 0.5) * 2;
        root.style.setProperty("--px", px.toFixed(3));
        root.style.setProperty("--py", py.toFixed(3));
        raf = null;
      });
    }, { passive: true });
  }

  /* ---------------------------------------------------------------------
     9. Card cursor-spotlight — a soft highlight that tracks the pointer
        across any glass card surface (.card/.stat-card/.unit-card/
        .order-card/.order-stat-card), painted by each element's own ::after
        in style.css from --spot-x/--spot-y. One delegated pointermove on
        the document instead of per-card listeners, so cards rendered after
        load (e.g. inside a toggled panel) still light up with no re-binding
        — matches the existing delegation pattern used by the ripple and
        toast systems above.
     --------------------------------------------------------------------- */
  function initCardSpotlight() {
    if (reduceMotion) return;
    if (!window.matchMedia || !window.matchMedia("(pointer: fine)").matches) return;
    var raf = null, lastEvent = null;
    document.addEventListener("pointermove", function (e) {
      lastEvent = e;
      if (raf) return;
      raf = window.requestAnimationFrame(function () {
        var card = lastEvent.target.closest(".card, .stat-card, .unit-card, .order-card, .order-stat-card");
        if (card) {
          var rect = card.getBoundingClientRect();
          card.style.setProperty("--spot-x", (lastEvent.clientX - rect.left) + "px");
          card.style.setProperty("--spot-y", (lastEvent.clientY - rect.top) + "px");
        }
        raf = null;
      });
    }, { passive: true });
  }

  /* ---------------------------------------------------------------------
     10. Notification bell — dropdown fed by /api/notifications (JSON), with
         a lightweight poll every 25s to keep the unread badge current
         without a page reload. Only present on authenticated pages
         (layout.html); a no-op on login/register (auth_layout.html), which
         don't render the #notifBell markup at all.
     --------------------------------------------------------------------- */
  function initNotificationBell() {
    var bell = document.getElementById("notifBell");
    var panel = document.getElementById("notifPanel");
    var badge = document.getElementById("notifBadge");
    var list = document.getElementById("notifList");
    var markAllBtn = document.getElementById("notifMarkAll");
    if (!bell || !panel || !badge || !list) return;

    var TYPE_ICON = { match: "&#128279;", order: "&#128230;", dispute: "&#9888;", payment: "&#128179;" };
    var lastUnread = parseInt(badge.getAttribute("data-count"), 10) || 0;
    var loaded = false;
    var closeTimer = null;

    function escapeHtml(str) {
      var div = document.createElement("div");
      div.textContent = str == null ? "" : String(str);
      return div.innerHTML;
    }

    function bindRowRead(el) {
      el.addEventListener("click", function () {
        fetch("/notifications/" + el.dataset.id + "/read", {
          method: "POST",
          headers: { "X-Requested-With": "fetch", "Accept": "application/json" },
        });
      }, { once: true });
    }

    function renderList(items) {
      if (!items.length) {
        list.innerHTML = '<div class="notif-empty">You&rsquo;re all caught up.</div>';
        return;
      }
      list.innerHTML = items.map(function (n) {
        var icon = TYPE_ICON[n.type] || "&#128276;";
        return (
          '<a href="' + (n.link ? escapeHtml(n.link) : "#") + '" class="notif-item' + (n.is_read ? "" : " unread") + '" data-id="' + n.id + '">' +
            '<span class="notif-type-icon notif-type-' + escapeHtml(n.type) + '">' + icon + "</span>" +
            '<span class="notif-body">' +
              '<span class="notif-title">' + escapeHtml(n.title) + "</span>" +
              (n.message ? '<span class="notif-message">' + escapeHtml(n.message) + "</span>" : "") +
              '<span class="notif-time">' + escapeHtml(n.relative_time) + "</span>" +
            "</span>" +
            (n.is_read ? "" : '<span class="notif-dot" aria-hidden="true"></span>') +
          "</a>"
        );
      }).join("");
      list.querySelectorAll(".notif-item.unread").forEach(bindRowRead);
    }

    function updateBadge(count) {
      badge.setAttribute("data-count", count);
      if (count > 0) {
        badge.hidden = false;
        badge.textContent = count > 99 ? "99+" : String(count);
        if (!reduceMotion && loaded && count > lastUnread) {
          bell.classList.remove("ring");
          void bell.offsetWidth; // restart the animation if it's already mid-run
          bell.classList.add("ring");
        }
      } else {
        badge.hidden = true;
        badge.textContent = "0";
      }
      lastUnread = count;
    }

    function poll(withList) {
      fetch("/api/notifications", { headers: { Accept: "application/json" } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data || !data.success) return;
          updateBadge(data.unread_count);
          if (withList) renderList(data.notifications);
          loaded = true;
        })
        .catch(function () {});
    }

    function openPanel() {
      window.clearTimeout(closeTimer);
      panel.hidden = false;
      window.requestAnimationFrame(function () { panel.classList.add("open"); });
      bell.classList.add("is-open");
      bell.setAttribute("aria-expanded", "true");
      poll(true);
    }

    function closePanel() {
      panel.classList.remove("open");
      bell.classList.remove("is-open");
      bell.setAttribute("aria-expanded", "false");
      closeTimer = window.setTimeout(function () { panel.hidden = true; }, 200);
    }

    bell.addEventListener("click", function (e) {
      e.stopPropagation();
      if (panel.classList.contains("open")) closePanel(); else openPanel();
    });
    document.addEventListener("click", function (e) {
      if (panel.classList.contains("open") && !panel.contains(e.target) && e.target !== bell) closePanel();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && panel.classList.contains("open")) closePanel();
    });

    if (markAllBtn) {
      markAllBtn.addEventListener("click", function () {
        fetch("/notifications/read-all", {
          method: "POST",
          headers: { "X-Requested-With": "fetch", "Accept": "application/json" },
        }).then(function () { poll(true); });
      });
    }

    poll(false);
    window.setInterval(function () { poll(panel.classList.contains("open")); }, 25000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    initNetworkCanvas();
    initScrollReveal();
    initTilt();
    initCounters();
    initToasts();
    initButtonRipple();
    initFormFeedback();
    initParallaxOrbs();
    initCardSpotlight();
    initNotificationBell();
  });
})();
