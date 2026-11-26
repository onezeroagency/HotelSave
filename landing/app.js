/* MyRoomWatch v8 interactions. Vanilla, dependency-free, honours
   prefers-reduced-motion (completed end states, no scrubbing or looping).
   All prices and bookings on this page are labelled examples. */
(function () {
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (id) { return document.getElementById(id); };
  var eur = function (n) { return "€" + Math.round(n).toLocaleString("en-IE"); };

  /* 1. Scroll reveal, staggered 70ms per sibling within each section */
  var els = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    els.forEach(function (e) { e.classList.add("in"); });
  } else {
    var counts = new Map();
    els.forEach(function (e) {
      var sec = e.closest("section") || document.body;
      var i = counts.get(sec) || 0;
      counts.set(sec, i + 1);
      e.style.setProperty("--rd", Math.min(i * 70, 420) + "ms");
    });
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (e) { io.observe(e); });
  }

  /* 2. Live price-watch card: price ticks down, then "Rebooked" lands. Example values. */
  var watch = $("watch"), nightly = $("nightly"), stateText = $("state-text"), spark = $("spark-line");
  var steps = [162, 160, 157, 151, 148, 143];
  var paid = 162;
  function sparkPoints(i) {
    var pts = [];
    for (var k = 0; k < steps.length; k++) {
      var v = steps[Math.min(k, i)];
      pts.push((k * 40) + "," + (6 + (paid - v) * 1.4).toFixed(1));
    }
    return pts.join(" ");
  }
  function setStep(i) {
    nightly.textContent = eur(steps[i]);
    spark.setAttribute("points", sparkPoints(i));
    stateText.textContent = i === 0 ? "Watching · check #31" : "Lower rate seen · check #" + (31 + i);
  }
  function finalState() {
    setStep(steps.length - 1);
    watch.classList.add("done");
    stateText.textContent = "Same room confirmed";
  }
  if (watch) {
    if (reduce) { finalState(); }
    else {
      var wi = 0;
      var run = function () {
        watch.classList.remove("done"); wi = 0; setStep(0);
        var t = setInterval(function () {
          wi++;
          if (wi < steps.length) { setStep(wi); nightly.classList.remove("blip"); void nightly.offsetWidth; nightly.classList.add("blip"); }
          else { clearInterval(t); finalState(); setTimeout(run, 4200); }
        }, 1100);
      };
      run();
    }
  }

  /* 3. Countdown ring to the cancellation deadline (example: 72h window) */
  var cd = $("countdown"), ring = $("ring-fg");
  var cdTotal = 72 * 3600, left = 61 * 3600 + 42 * 60 + 10, C = 119.4;
  function drawCd() {
    var hh = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
    cd.textContent = hh + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    ring.style.strokeDashoffset = (C * (1 - left / cdTotal)).toFixed(1);
  }
  if (cd && ring) { drawCd(); if (!reduce) setInterval(function () { left = left > 0 ? left - 1 : cdTotal; drawCd(); }, 1000); }

  /* 4. #how: connected rail + auto-cycling steps with per-step viz motion */
  var stepsList = $("steps"), rail = $("rail-fill");
  if (stepsList) {
    var items = stepsList.querySelectorAll(".step");
    var cur = -1, hTimer = null, hHover = false, hManual = false;
    function setHow(i) {
      if (i === cur) return;
      cur = i;
      items.forEach(function (li, k) {
        li.classList.toggle("on", k === i);
        if (k === i) li.setAttribute("aria-current", "step");
        else li.removeAttribute("aria-current");
      });
      if (rail) rail.style.width = (12.5 + i * 25) + "%";
    }
    function howAuto() {
      if (hManual || reduce || hTimer) return;
      hTimer = setInterval(function () { if (!hHover) setHow((cur + 1) % 4); }, 3000);
    }
    items.forEach(function (li) {
      li.addEventListener("click", function () {
        hManual = true;
        if (hTimer) { clearInterval(hTimer); hTimer = null; }
        setHow(+li.getAttribute("data-step"));
      });
      li.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); li.click(); }
      });
      li.addEventListener("focus", function () { hHover = true; });
      li.addEventListener("blur", function () { hHover = false; });
    });
    stepsList.addEventListener("mouseenter", function () { hHover = true; });
    stepsList.addEventListener("mouseleave", function () { hHover = false; });
    if (reduce) {
      setHow(3); // completed end state, no cycling
      if (rail) rail.style.width = "100%";
    } else {
      setHow(0);
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (e) {
          if (e[0].isIntersecting) howAuto();
          else if (hTimer) { clearInterval(hTimer); hTimer = null; }
        }, { threshold: 0.35 }).observe(stepsList);
      } else { howAuto(); }
    }
  }

  /* 5. Band: booking-confirmation morph (Original <-> Rebooked). Example. */
  var cc = $("confirm-card"), ccToggle = $("cc-toggle"), ccTotal = $("cc-total"),
      ccKeep = $("cc-keep"), ccK = $("cc-k");
  var ccTween = null;
  function ccSet(view) { // 0 original, 1 rebooked
    cc.classList.toggle("re", view === 1);
    ccK.textContent = view === 1 ? "Rebooked · same room" : "Booked 3 weeks ago";
    ccKeep.hidden = view !== 1;
    ccToggle.querySelectorAll(".cc-btn").forEach(function (b) {
      var on = +b.getAttribute("data-v") === view;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
    var from = parseInt(ccTotal.textContent.replace(/\D/g, ""), 10) || 486;
    var to = view === 1 ? 429 : 486;
    if (reduce || from === to) { ccTotal.textContent = eur(to); return; }
    if (ccTween) cancelAnimationFrame(ccTween);
    var t0 = performance.now(), dur = 600;
    (function roll(now) {
      var p = Math.min((now - t0) / dur, 1);
      p = 1 - Math.pow(1 - p, 3); // ease-out cubic
      ccTotal.textContent = eur(from + (to - from) * p);
      if (p < 1) ccTween = requestAnimationFrame(roll); else ccTween = null;
    })(t0);
  }
  if (cc && ccToggle) {
    ccToggle.addEventListener("click", function (e) {
      var b = e.target.closest(".cc-btn");
      if (b) ccSet(+b.getAttribute("data-v"));
    });
    if (reduce) { ccSet(1); }
    else {
      ccSet(0);
      if ("IntersectionObserver" in window) {
        var ccIo = new IntersectionObserver(function (e) {
          if (e[0].isIntersecting) {
            ccIo.disconnect();
            setTimeout(function () { ccSet(1); }, 900);
          }
        }, { threshold: 0.5 });
        ccIo.observe(cc);
      }
    }
  }

  /* 6. Savings calculator: presets, filled tracks, floating tips, rolling total.
     Illustrative 10% drop with the [CONFIRM] floor max(€10, 3%). */
  var cp = $("c-price"), cn = $("c-nights"), oKeep = $("o-keep");
  var presets = $("calc-presets");
  var keepTween = null, keepShown = 45;
  function setFill(input, tip, label) {
    var f = (input.value - input.min) / (input.max - input.min);
    input.style.setProperty("--f", f);
    var wrap = input.closest(".range-wrap");
    if (wrap) wrap.style.setProperty("--f", f);
    if (tip) tip.textContent = label;
  }
  function rollKeep(to) {
    if (reduce) { oKeep.textContent = eur(to); keepShown = to; return; }
    if (keepTween) cancelAnimationFrame(keepTween);
    var from = keepShown, t0 = performance.now(), dur = 450;
    (function roll(now) {
      var p = Math.min((now - t0) / dur, 1);
      p = 1 - Math.pow(1 - p, 3);
      keepShown = from + (to - from) * p;
      oKeep.textContent = eur(keepShown);
      if (p < 1) keepTween = requestAnimationFrame(roll); else { keepTween = null; keepShown = to; }
    })(t0);
  }
  function syncPresets() {
    if (!presets) return;
    presets.querySelectorAll(".cp-btn").forEach(function (b) {
      b.classList.toggle("on", +b.getAttribute("data-p") === +cp.value && +b.getAttribute("data-n") === +cn.value);
    });
  }
  function calc() {
    var p = +cp.value, n = +cn.value, tot = p * n, keep = tot * 0.10, floor = Math.max(10, tot * 0.03);
    $("o-price").textContent = eur(p); $("o-nights").textContent = n;
    setFill(cp, $("tip-price"), eur(p));
    setFill(cn, $("tip-nights"), n + (n === 1 ? " night" : " nights"));
    rollKeep(keep);
    var ok = keep >= floor, f = $("o-flag");
    f.textContent = ok ? "Worth rebooking" : "Too small. We'd stay quiet";
    f.classList.toggle("off", !ok);
    syncPresets();
  }
  if (cp && cn) {
    cp.addEventListener("input", calc);
    cn.addEventListener("input", calc);
    if (presets) presets.addEventListener("click", function (e) {
      var b = e.target.closest(".cp-btn");
      if (!b) return;
      cp.value = b.getAttribute("data-p");
      cn.value = b.getAttribute("data-n");
      calc();
    });
    calc();
  }

  /* 7. Cursor-follow glow in the hero (pointer devices only) */
  var hero = document.querySelector(".hero");
  if (hero && !reduce && window.matchMedia("(pointer: fine)").matches) {
    hero.addEventListener("pointermove", function (e) {
      var r = hero.getBoundingClientRect();
      hero.style.setProperty("--mx", (e.clientX - r.left) + "px");
      hero.style.setProperty("--my", (e.clientY - r.top) + "px");
      hero.classList.add("glow-on");
    });
    hero.addEventListener("pointerleave", function () { hero.classList.remove("glow-on"); });
  }

  /* 8. Mobile sticky CTA after the hero */
  var bar = $("sticky-cta"), top = $("top"), start = $("start");
  if (bar && top && "IntersectionObserver" in window) {
    var heroOut = false, formIn = false;
    var upd = function () {
      var show = heroOut && !formIn;
      bar.classList.toggle("on", show); bar.setAttribute("aria-hidden", show ? "false" : "true");
      bar.querySelector("a").tabIndex = show ? 0 : -1;
    };
    new IntersectionObserver(function (e) { heroOut = !e[0].isIntersecting; upd(); }).observe(top);
    if (start) new IntersectionObserver(function (e) { formIn = e[0].isIntersecting; upd(); }).observe(start);
  }

  /* 9. HorizonX-style helpers shared with the inline signup script */
  var toastBox = null;
  window.mrwPlayCheck = function (el) {
    if (!el) return;
    el.classList.remove("play");
    void el.offsetWidth;
    el.classList.add("play");
  };
  window.mrwToast = function (title, desc) {
    if (!toastBox) {
      toastBox = document.createElement("div");
      toastBox.className = "toasts";
      toastBox.setAttribute("role", "region");
      toastBox.setAttribute("aria-label", "Notifications");
      toastBox.setAttribute("aria-live", "polite");
      document.body.appendChild(toastBox);
    }
    while (toastBox.children.length >= 3) toastBox.firstElementChild.remove();
    var el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("tabindex", "0");
    var tx = document.createElement("div");
    tx.className = "toast-tx";
    var b = document.createElement("b");
    b.textContent = title;
    tx.appendChild(b);
    if (desc) { var s = document.createElement("span"); s.textContent = desc; tx.appendChild(s); }
    var row = document.createElement("div");
    row.className = "toast-row";
    row.innerHTML = '<span class="toast-ic" aria-hidden="true"><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8"/><path d="m6.5 10 2.5 2.5 5-5.5"/></svg></span>';
    row.appendChild(tx);
    var x = document.createElement("button");
    x.className = "toast-x"; x.type = "button";
    x.setAttribute("aria-label", "Dismiss notification");
    x.innerHTML = '<svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="m5 5 10 10m0-10L5 15"/></svg>';
    row.appendChild(x);
    el.appendChild(row);
    var gone = false;
    function dismiss() {
      if (gone) return; gone = true;
      if (reduce) { el.remove(); return; }
      el.classList.remove("in"); el.classList.add("out");
      el.addEventListener("transitionend", function () { el.remove(); }, { once: true });
      setTimeout(function () { el.remove(); }, 240);
    }
    x.addEventListener("click", dismiss);
    var barEl = document.createElement("div");
    barEl.className = "toast-bar";
    var i = document.createElement("i");
    i.style.setProperty("--t", "5000ms");
    barEl.appendChild(i);
    el.appendChild(barEl);
    if (reduce) setTimeout(dismiss, 5000);
    else i.addEventListener("animationend", dismiss);
    toastBox.appendChild(el);
    if (reduce) el.classList.add("in");
    else requestAnimationFrame(function () { requestAnimationFrame(function () { el.classList.add("in"); }); });
    return el;
  };
})();

/* Why-prices-drop: scroll scrubs the example rate timeline; the dot is
   computed from the path geometry every frame so it never leaves the line. */
(function () {
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var sec = document.getElementById("why");
  var chart = document.getElementById("why-chart");
  var line = document.getElementById("wc-line");
  var dot = document.getElementById("wc-dot");
  var pill = document.getElementById("wc-pill");
  var triggers = document.getElementById("why-triggers");
  if (!sec || !chart || !line || !triggers) return;

  var STAGES = [{ x: 104, p: "€185" }, { x: 188, p: "€162" }, { x: 258, p: "€138" }];
  var total = 0, ENDS = [];

  function measure() {
    total = line.getTotalLength();
    ENDS = STAGES.map(function (s) {
      var lo = 0, hi = total;
      for (var i = 0; i < 26; i++) {
        var mid = (lo + hi) / 2;
        if (line.getPointAtLength(mid).x < s.x) lo = mid; else hi = mid;
      }
      return hi;
    });
    line.style.strokeDasharray = total;
  }
  measure();
  window.addEventListener("resize", measure);

  var stage = -1;
  function applyL(L) {
    line.style.strokeDashoffset = Math.max(0, total - L);
    var pt = line.getPointAtLength(Math.max(0.1, Math.min(L, total)));
    dot.setAttribute("cx", pt.x.toFixed(2));
    dot.setAttribute("cy", pt.y.toFixed(2));
    var s = 0;
    if (L >= (ENDS[0] + ENDS[1]) / 2) s = 1;
    if (L >= (ENDS[1] + ENDS[2]) / 2) s = 2;
    if (s !== stage) {
      stage = s;
      pill.textContent = STAGES[s].p;
      if (!reduce) { pill.classList.remove("pop"); void pill.offsetWidth; pill.classList.add("pop"); }
      triggers.querySelectorAll(".wt").forEach(function (b, k) {
        b.classList.toggle("on", k === s);
        b.setAttribute("aria-pressed", k === s ? "true" : "false");
      });
    }
    chart.classList.toggle("done", L >= ENDS[2] - 1);
  }

  if (reduce) {
    // Static completed state; trigger clicks jump without scrubbing.
    applyL(total);
    triggers.addEventListener("click", function (e) {
      var b = e.target.closest(".wt");
      if (b) applyL(ENDS[+b.getAttribute("data-s")]);
    });
    return;
  }

  // Scroll drives a target length; a rAF loop eases the current length
  // toward it. A trigger click overrides the target until the next scroll.
  var curL = 0, targetL = 0, manualTarget = null, manualScrollY = 0;
  var running = false, raf = 0;

  function scrollTarget() {
    var r = sec.getBoundingClientRect();
    var vh = window.innerHeight || 1;
    var p = (vh * 0.9 - r.top) / (vh * 0.65);
    p = Math.max(0, Math.min(1, p));
    return p * total;
  }
  function frame() {
    raf = 0;
    if (manualTarget !== null && Math.abs(window.scrollY - manualScrollY) > 40) manualTarget = null;
    targetL = manualTarget !== null ? manualTarget : scrollTarget();
    curL += (targetL - curL) * 0.16;
    if (Math.abs(targetL - curL) < 0.4) curL = targetL;
    applyL(curL);
    if (running) raf = requestAnimationFrame(frame);
  }
  function setRunning(on) {
    if (on === running) return;
    running = on;
    if (on && !raf) raf = requestAnimationFrame(frame);
    if (!on && raf) { cancelAnimationFrame(raf); raf = 0; }
  }

  triggers.addEventListener("click", function (e) {
    var b = e.target.closest(".wt");
    if (!b) return;
    manualTarget = ENDS[+b.getAttribute("data-s")];
    manualScrollY = window.scrollY;
    setRunning(true);
  });

  applyL(0);
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (e) {
      setRunning(e[0].isIntersecting);
    }, { rootMargin: "20% 0px 20% 0px" }).observe(sec);
  } else {
    setRunning(true);
  }
})();
