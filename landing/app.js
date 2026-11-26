/* MyRoomWatch v8 interactions. Vanilla, ~3 KB, honours prefers-reduced-motion. All numbers are examples. */
(function () {
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var $ = function (id) { return document.getElementById(id); };
  var eur = function (n) { return "€" + Math.round(n).toLocaleString("en-IE"); };

  /* 1. Scroll reveal */
  var els = document.querySelectorAll(".reveal");
  if (reduce || !("IntersectionObserver" in window)) {
    els.forEach(function (e) { e.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); } });
    }, { rootMargin: "0px 0px -8% 0px" });
    els.forEach(function (e) { io.observe(e); });
  }

  /* 2. Live price-watch card: price ticks down, then "Rebooked" lands. Example values. */
  var watch = $("watch"), nightly = $("nightly"), stateText = $("state-text"), spark = $("spark-line");
  var steps = [162, 160, 157, 151, 148, 143];      // example nightly prices
  var nights = 3, paid = 162;
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
      var i = 0;
      var run = function () {
        watch.classList.remove("done"); i = 0; setStep(0);
        var t = setInterval(function () {
          i++;
          if (i < steps.length) { setStep(i); nightly.classList.remove("blip"); void nightly.offsetWidth; nightly.classList.add("blip"); }
          else { clearInterval(t); finalState(); setTimeout(run, 4200); }
        }, 1100);
      };
      run();
    }
  }

  /* 3. Countdown ring to the cancellation deadline (example: 72h window) */
  var cd = $("countdown"), ring = $("ring-fg");
  var total = 72 * 3600, left = 61 * 3600 + 42 * 60 + 10, C = 119.4;
  function drawCd() {
    var h = Math.floor(left / 3600), m = Math.floor(left % 3600 / 60), s = left % 60;
    cd.textContent = h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    ring.style.strokeDashoffset = (C * (1 - left / total)).toFixed(1);
  }
  if (cd && ring) { drawCd(); if (!reduce) setInterval(function () { left = left > 0 ? left - 1 : total; drawCd(); }, 1000); }

  /* 4. Before/after swap: drag, or it gently auto-plays until touched */
  var swap = $("swap"), range = $("swap-range");
  if (swap && range) {
    var touched = false;
    var setPos = function (v) { swap.style.setProperty("--pos", v + "%"); };
    range.addEventListener("input", function () { touched = true; setPos(range.value); });
    if (!reduce && "IntersectionObserver" in window) {
      var tt = 0, timer = null;
      new IntersectionObserver(function (en) {
        if (en[0].isIntersecting && !timer && !touched) {
          timer = setInterval(function () {
            if (touched) { clearInterval(timer); return; }
            tt += 0.035; var v = 50 + Math.sin(tt) * 38; range.value = v; setPos(v);
          }, 30);
        } else if (!en[0].isIntersecting && timer) { clearInterval(timer); timer = null; }
      }).observe(swap);
    }
  }

  /* 5. Savings calculator: illustrative 10% drop, with the [CONFIRM] floor max(€10, 3%) */
  var cp = $("c-price"), cn = $("c-nights");
  function calc() {
    var p = +cp.value, n = +cn.value, tot = p * n, keep = tot * 0.10, floor = Math.max(10, tot * 0.03);
    $("o-price").textContent = eur(p); $("o-nights").textContent = n;
    $("o-keep").textContent = eur(keep);
    var ok = keep >= floor, f = $("o-flag");
    f.textContent = ok ? "Worth rebooking" : "Too small. We'd stay quiet";
    f.classList.toggle("off", !ok);
  }
  if (cp && cn) { cp.addEventListener("input", calc); cn.addEventListener("input", calc); calc(); }

  /* 6. Cursor-follow glow in the hero (pointer devices only) */
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

  /* 7. Mobile sticky CTA after the hero */
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
})();
