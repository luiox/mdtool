// mdtool sitegen —— 页面脚本：代码块复制按钮 + 图片灯箱。
// 复制对应 jacman 时代修好的 ClipboardJS 行为（单击即复制）；
// 灯箱替代 fancybox（点击正文图片放大到视口，点击/Esc 关闭）。
(function () {
  "use strict";

  // 复制按钮：事件委托，文案反馈在此统一，CSS 只管样式
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".code-copy-btn");
    if (!btn) return;
    var figure = btn.closest(".codeblock");
    var code = figure ? figure.querySelector("pre code") : null;
    if (!code) return;
    var text = code.innerText;
    var done = function () {
      btn.textContent = "复制成功";
      btn.classList.add("ok");
      setTimeout(function () {
        btn.textContent = "复制";
        btn.classList.remove("ok");
      }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    } else {
      fallback();
    }
    function fallback() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (err) { /* 静默失败 */ }
      document.body.removeChild(ta);
      done();
    }
  });

  // 图片灯箱：只在 .post-content 内的图片上生效
  document.addEventListener("click", function (e) {
    var img = e.target.closest(".post-content img");
    if (!img) return;
    var overlay = document.createElement("div");
    overlay.className = "img-lightbox";
    var big = document.createElement("img");
    big.src = img.currentSrc || img.src;
    big.alt = img.alt || "";
    overlay.appendChild(big);
    overlay.addEventListener("click", function () { overlay.remove(); });
    document.addEventListener("keydown", function esc(ev) {
      if (ev.key === "Escape") {
        overlay.remove();
        document.removeEventListener("keydown", esc);
      }
    });
    document.body.appendChild(overlay);
  });
})();
