/* Seanav Operations — perilaku bersama semua halaman */
(() => {
  const $ = (id) => document.getElementById(id);

  // Sidebar mobile
  const menuBtn = $("menu-btn"), scrim = $("scrim");
  const toggleNav = (open) => document.body.classList.toggle("nav-open", open);
  if (menuBtn) menuBtn.addEventListener("click", () => toggleNav(true));
  if (scrim) scrim.addEventListener("click", () => toggleNav(false));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") toggleNav(false); });

  // Tema terang/gelap
  const themeBtn = $("theme-btn");
  if (themeBtn) themeBtn.addEventListener("click", () => {
    const dark = document.documentElement.classList.toggle("dark");
    try { localStorage.setItem("snv-theme", dark ? "dark" : "light"); } catch (e) { /* abaikan */ }
  });

  // Pencarian global
  const search = $("search");
  if (search) search.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = search.q.value.trim(), target = search.scope ? search.scope.value : "";
    if (target) location.href = target + (q ? "?q=" + encodeURIComponent(q) : "");
  });

  // Konfirmasi: <form data-confirm="Yakin?">
  document.addEventListener("submit", (e) => {
    const form = e.target;
    const msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { e.preventDefault(); return; }
    // Cegah kirim ganda untuk form POST
    if ((form.method || "").toLowerCase() === "post" && !form.hasAttribute("data-no-lock")) {
      if (form.dataset.sent) { e.preventDefault(); return; }
      form.dataset.sent = "1";
      setTimeout(() => { delete form.dataset.sent; }, 8000);
      const btn = e.submitter || form.querySelector('button[type="submit"]');
      if (btn && !btn.dataset.keep) { btn.dataset.label = btn.innerHTML; btn.disabled = true; btn.innerHTML = "Memproses…";
        setTimeout(() => { btn.disabled = false; btn.innerHTML = btn.dataset.label; }, 8000); }
    }
  }, true);

  // Label kolom untuk tampilan kartu di mobile
  document.querySelectorAll("table.data").forEach((table) => {
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    table.querySelectorAll("tbody tr").forEach((tr) => {
      [...tr.children].forEach((td, i) => { if (heads[i] && !td.hasAttribute("data-label")) td.setAttribute("data-label", heads[i]); });
    });
  });

  // Tandai field yang punya error
  document.querySelectorAll(".field .field-error, .field .errorlist").forEach((el) => el.closest(".field").classList.add("has-error"));

  // Format rupiah helper global
  window.snvRupiah = (n) => new Intl.NumberFormat("id-ID", {style: "currency", currency: "IDR", maximumFractionDigits: 0}).format(n || 0);
})();
