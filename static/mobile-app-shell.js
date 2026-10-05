/**
 * Toggle #appRoot.nav-drawer-open; closes on backdrop tap, nav link, or resize to desktop.
 */
(function () {
  var root = document.getElementById("appRoot");
  var btn = document.getElementById("btnSidebarToggle");
  var bd = document.getElementById("sidebarBackdrop");
  if (!root || !btn) return;

  function mqMobile() {
    return window.matchMedia("(max-width: 768px)").matches;
  }

  function setOpen(open) {
    root.classList.toggle("nav-drawer-open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    if (bd) bd.setAttribute("aria-hidden", open ? "false" : "true");
    document.body.style.overflow = open && mqMobile() ? "hidden" : "";
  }

  function close() {
    setOpen(false);
  }

  btn.addEventListener("click", function () {
    setOpen(!root.classList.contains("nav-drawer-open"));
  });

  if (bd) {
    bd.addEventListener("click", close);
  }

  root.querySelectorAll(".sidebar a, .sidebar .nav-item").forEach(function (el) {
    el.addEventListener("click", function () {
      if (mqMobile()) close();
    });
  });

  window.addEventListener("resize", function () {
    if (!mqMobile()) close();
  });
})();
