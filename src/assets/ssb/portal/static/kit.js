/* SSB GUI KIT v16 — dragging, resizing, scroll speed, flashing. Include on every page. */
"use strict";
window.SSBKIT = (() => {
  let scrollSpeed = 1.0;

  function makeDraggable(el, handle) {
    handle = handle || el;
    let sx = 0, sy = 0, ox = 0, oy = 0, drag = false;
    handle.style.cursor = "grab";
    handle.addEventListener("mousedown", e => {
      if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "BUTTON") return;
      drag = true; sx = e.clientX; sy = e.clientY;
      const r = el.getBoundingClientRect();
      if (getComputedStyle(el).position === "static") { el.style.position = "fixed"; el.style.left = r.left + "px"; el.style.top = r.top + "px"; el.style.margin = 0; }
      ox = r.left; oy = r.top; handle.style.cursor = "grabbing";
      e.preventDefault();
    });
    window.addEventListener("mousemove", e => {
      if (!drag) return;
      el.style.left = (ox + e.clientX - sx) + "px";
      el.style.top = (oy + e.clientY - sy) + "px";
    });
    window.addEventListener("mouseup", () => { drag = false; handle.style.cursor = "grab"; });
  }

  function makeResizable(el) {
    const g = document.createElement("div");
    g.style.cssText = "position:absolute;right:0;bottom:0;width:14px;height:14px;cursor:nwse-resize;background:linear-gradient(135deg,transparent 50%,#42f8ff55 50%);";
    el.style.position = getComputedStyle(el).position === "static" ? "relative" : el.style.position;
    el.appendChild(g);
    let drag = false, sx = 0, sy = 0, sw = 0, sh = 0;
    g.addEventListener("mousedown", e => { drag = true; sx = e.clientX; sy = e.clientY; sw = el.offsetWidth; sh = el.offsetHeight; e.preventDefault(); e.stopPropagation(); });
    window.addEventListener("mousemove", e => {
      if (!drag) return;
      el.style.width = Math.max(180, sw + e.clientX - sx) + "px";
      el.style.height = Math.max(90, sh + e.clientY - sy) + "px";
    });
    window.addEventListener("mouseup", () => { drag = false; });
  }

  function attachScrollSpeed() {
    const bar = document.createElement("div");
    bar.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:9999;background:#04070ccc;border:1px solid #234;border-radius:8px;padding:6px 10px;font:11px ui-monospace;color:#d5f7ff;";
    bar.innerHTML = `scroll <input type="range" min="1" max="80" value="10" style="width:80px;vertical-align:middle"> <span id="ssb-spd">1.0×</span> <button id="ssb-drag-tgl" style="background:none;border:1px solid #42f8ff;color:#42f8ff;border-radius:5px;font:inherit;cursor:pointer;padding:1px 6px">drag</button>`;
    document.body.appendChild(bar);
    bar.querySelector("input").addEventListener("input", e => {
      scrollSpeed = e.target.value / 10;
      bar.querySelector("#ssb-spd").textContent = scrollSpeed.toFixed(1) + "×";
    });
    document.addEventListener("wheel", e => {
      if (!e.ctrlKey) return;
      e.preventDefault();
      let el = e.target;
      while (el && el !== document.body && el.scrollHeight <= el.clientHeight) el = el.parentElement;
      if (el && el.scrollHeight > el.clientHeight) el.scrollTop += e.deltaY * scrollSpeed * 3;
    }, { passive: false });
    let dragOn = false;
    bar.querySelector("#ssb-drag-tgl").addEventListener("click", () => {
      dragOn = !dragOn;
      document.querySelectorAll("section, .panel, #log, #out, .card").forEach(el => {
        if (dragOn) { makeDraggable(el); makeResizable(el); el.style.outline = "1px dashed #42f8ff44"; }
        else el.style.outline = "";
      });
    });
  }

  function flash(el, cls) {
    cls = cls || "ssb-flash";
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
  }

  const style = document.createElement("style");
  style.textContent = `
    @keyframes ssbflash { 0% { background:#59ffa366; } 100% { background:transparent; } }
    .ssb-flash { animation: ssbflash 0.8s ease-out; }
    @keyframes ssbflashred { 0% { background:#ff6a6a66; } 100% { background:transparent; } }
    .ssb-flash-red { animation: ssbflashred 0.8s ease-out; }`;
  document.head.appendChild(style);

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", attachScrollSpeed);
  else attachScrollSpeed();

  return { makeDraggable, makeResizable, flash };
})();
