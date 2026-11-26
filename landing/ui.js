/* MyRoomWatch UI kit — toast manager, success check, inline-banner opener.
   Ported from HorizonX's Animated Toast / Success Check / Inline Banner
   (React originals) to dependency-free vanilla JS. Motion tokens live in
   styles.css (--dur-enter/--dur-exit, --ease-hx-out/--ease-hx-in). */
(function () {
  "use strict";
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

  /* ---------- Success check (self-drawing circle + mark) ---------- */

  // Geometry from the HorizonX component: 48x48 viewBox, r=20 circle,
  // check path m14 24 7 7 13-14; circle draws 100ms, check 120ms after it.
  function checkSVG(size) {
    size = size || 48;
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 48 48" fill="none" ' +
      'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="24" cy="24" r="20"/><path d="m14 24 7 7 13-14"/></svg>';
  }
  function playCheck(el) {
    if (!el) return;
    el.classList.remove("play");
    void el.offsetWidth; // restart the CSS animation
    el.classList.add("play");
  }

  /* ---------- Toast manager ---------- */

  var box = null;
  var LIMIT = 3;
  var ICONS = {
    success: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="8"/><path d="m6.5 10 2.5 2.5 5-5.5"/></svg>',
    info: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="10" cy="10" r="8"/><path d="M10 9v5M10 6v.5"/></svg>',
    warning: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3 2.5 16.5h15z"/><path d="M10 8.5v3.5M10 14.4v.1"/></svg>',
    error: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="10" cy="10" r="8"/><path d="m7.2 7.2 5.6 5.6m0-5.6-5.6 5.6"/></svg>'
  };

  function ensureBox() {
    if (box) return box;
    box = document.createElement("div");
    box.className = "toasts";
    box.setAttribute("role", "region");
    box.setAttribute("aria-label", "Notifications");
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
    // The countdown pauses while the window is unfocused, like the original.
    window.addEventListener("blur", function () { box.setAttribute("data-paused", ""); });
    window.addEventListener("focus", function () { box.removeAttribute("data-paused"); });
    return box;
  }

  function dismiss(el) {
    if (el._gone) return;
    el._gone = true;
    if (reduced.matches) { el.remove(); return; }
    el.classList.remove("in");
    el.classList.add("out");
    var done = function () { el.remove(); };
    el.addEventListener("transitionend", done, { once: true });
    setTimeout(done, 240); // safety if transitionend never fires
  }

  // toast("Email copied", {desc: "...", variant: "success", timeout: 5000})
  function toast(title, opts) {
    opts = opts || {};
    var variant = opts.variant || "success";
    var timeout = opts.timeout == null ? 5000 : opts.timeout;
    var root = ensureBox();
    while (root.children.length >= LIMIT) root.firstElementChild.remove();

    var el = document.createElement("div");
    el.className = "toast " + variant;
    el.setAttribute("tabindex", "0");
    var tx = document.createElement("div");
    tx.className = "toast-tx";
    var b = document.createElement("b");
    b.textContent = title;
    tx.appendChild(b);
    if (opts.desc) {
      var s = document.createElement("span");
      s.textContent = opts.desc;
      tx.appendChild(s);
    }
    var row = document.createElement("div");
    row.className = "toast-row";
    row.innerHTML = '<span class="toast-ic" aria-hidden="true">' + (ICONS[variant] || ICONS.info) + "</span>";
    row.appendChild(tx);
    var x = document.createElement("button");
    x.className = "toast-x";
    x.type = "button";
    x.setAttribute("aria-label", "Dismiss notification");
    x.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m5 5 10 10m0-10L5 15"/></svg>';
    x.addEventListener("click", function () { dismiss(el); });
    row.appendChild(x);
    el.appendChild(row);

    if (timeout > 0) {
      var bar = document.createElement("div");
      bar.className = "toast-bar";
      var i = document.createElement("i");
      i.style.setProperty("--t", timeout + "ms");
      bar.appendChild(i);
      el.appendChild(bar);
      if (reduced.matches) {
        // No CSS countdown under reduced motion — a plain timer stands in.
        setTimeout(function () { dismiss(el); }, timeout);
      } else {
        // The bar drives dismissal, so pausing it (hover/focus/blurred
        // window, via CSS animation-play-state) delays dismissal for free.
        i.addEventListener("animationend", function () { dismiss(el); });
      }
    }

    root.appendChild(el);
    if (!reduced.matches) requestAnimationFrame(function () {
      requestAnimationFrame(function () { el.classList.add("in"); });
    });
    else el.classList.add("in");
    return el;
  }

  /* ---------- Inline banners (slide open after insertion) ---------- */

  function openBanners(root) {
    var wraps = (root || document).querySelectorAll(".banner-wrap:not(.open)");
    wraps.forEach(function (w, i) {
      if (reduced.matches) { w.classList.add("open"); return; }
      setTimeout(function () {
        requestAnimationFrame(function () { w.classList.add("open"); });
      }, 60 + i * 50);
    });
  }

  window.MRW = { toast: toast, checkSVG: checkSVG, playCheck: playCheck, openBanners: openBanners };
})();
