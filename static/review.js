/* Minimal review keyboard: A arms approval, Enter confirms; R/M/D submit; N/P nav; Esc disarms. */
(function () {
  var armed = false;
  var approveBtn = document.getElementById("btn-approve");
  var form = document.getElementById("decision-form");
  /* Session/fatigue guard (E4): client-side awareness only. sessionStorage —
     never sent anywhere, never affects queue/scores/decisions. Soft-only. */
  var SKEY = "heimdal-review-session";
  var session = { reviewed: 0, ambiguous: 0, started: Date.now() };
  try {
    var raw = sessionStorage.getItem(SKEY);
    if (raw) session = JSON.parse(raw);
  } catch (e) { /* fresh session */ }
  function save() {
    try { sessionStorage.setItem(SKEY, JSON.stringify(session)); } catch (e) {}
  }
  function renderSession() {
    var el = document.getElementById("session-indicator");
    if (!el) return;
    var mins = Math.round((Date.now() - (session.started || Date.now())) / 60000);
    el.textContent = session.reviewed + " reviewed · " + mins + " min";
    var note = document.getElementById("session-note");
    if (!note) return;
    var heavy = session.ambiguous >= 25, many = session.reviewed >= 40;
    note.textContent = (heavy || many)
      ? "You have reviewed " + (heavy ? session.ambiguous + " ambiguous-heavy cases"
                                       : session.reviewed + " cases")
        + " in this session. Consider starting a fresh review session if decisions are becoming slower."
      : "";
  }
  var resetBtn = document.getElementById("session-reset");
  if (resetBtn) resetBtn.addEventListener("click", function () {
    session = { reviewed: 0, ambiguous: 0, started: Date.now() };
    save(); renderSession();
  });
  renderSession();
  if (!form) return;
  document.addEventListener("keydown", function (ev) {
    if (ev.target && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName)) return;
    var k = ev.key;
    if (k === "Escape") { armed = false; return; }
    if (k === "Enter" && armed) {
      session.reviewed += 1;
      save(); renderSession();
      form.requestSubmit(approveBtn); return;
    }
    if (k === "a" || k === "A") { armed = true; ev.preventDefault(); return; }
    var map = { r: "REJECT", R: "REJECT", m: "AMBIGUOUS", M: "AMBIGUOUS", d: "DEFER", D: "DEFER" };
    if (map[k]) {
      var btn = form.querySelector('button[value="' + map[k] + '"]');
      if (btn) {
        session.reviewed += 1;
        if (map[k] === "AMBIGUOUS" || map[k] === "DEFER") session.ambiguous += 1;
        save(); renderSession();
        form.requestSubmit(btn);
      }
    }
  });
})();
