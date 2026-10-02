// Loom: small, dependency-free interactions for the conversation UI.
(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  // ---- toggles: navigation drawer and engagement panel
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-toggle]");
    if (!t) return;
    const which = t.dataset.toggle;
    if (which === "nav") document.body.classList.toggle("nav-open");
    if (which === "panel") {
      const narrow = window.matchMedia("(max-width: 1100px)").matches;
      document.body.classList.toggle(narrow ? "panel-open" : "panel-closed");
      try { localStorage.setItem("loom-panel-closed", document.body.classList.contains("panel-closed") ? "1" : ""); } catch (_) {}
    }
  });
  try { if (localStorage.getItem("loom-panel-closed")) document.body.classList.add("panel-closed"); } catch (_) {}

  // ---- overlay preview over the panel
  const overlay = $("#overlay");
  async function openOverlay(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    $("#overlay-body").innerHTML = await r.text();
    overlay.hidden = false;
  }
  document.addEventListener("click", (e) => {
    const eid = ($("#thread-wrap") || {}).dataset?.eid;
    const open = e.target.closest("[data-open]");
    if (open && eid) { e.preventDefault(); openOverlay(`/e/${eid}/preview?path=${encodeURIComponent(open.dataset.open)}`); }
    if (e.target.closest("[data-brief]") && eid) { e.preventDefault(); openOverlay(`/e/${eid}/brief`); }
    if (e.target.closest("[data-close-overlay]") || e.target === overlay) overlay.hidden = true;
    const jump = e.target.closest("[data-jump]");
    if (jump) {
      const target = document.getElementById(jump.dataset.jump);
      if (target) { e.preventDefault(); target.scrollIntoView({ behavior: "smooth", block: "start" }); document.body.classList.remove("panel-open"); }
    }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && overlay) overlay.hidden = true; });

  // ---- chips (single choice) bound to a hidden input
  document.addEventListener("click", (e) => {
    const chip = e.target.closest(".chips[data-single] .chip");
    if (!chip) return;
    const group = chip.parentElement;
    const was = chip.classList.contains("on");
    $$(".chip", group).forEach((c) => c.classList.remove("on"));
    const input = group.parentElement.querySelector(`input[name="${group.dataset.single}"]`) ||
                  document.querySelector(`input[name="${group.dataset.single}"]`);
    if (!was || group.dataset.single === "as_kind") chip.classList.add("on");
    if (input) input.value = chip.classList.contains("on") ? chip.dataset.value : "";
  });

  // ---- decision cards: buttons carry the composer text as the note
  const composerText = () => $("#composer-text");
  document.addEventListener("mouseover", (e) => {
    const b = e.target.closest("[data-decision-card] [data-decision]");
    if (b) { const line = b.closest("form").querySelector("[data-next-line]"); if (line) line.textContent = b.dataset.next; }
  });
  document.addEventListener("focusin", (e) => {
    const b = e.target.closest("[data-decision-card] [data-decision]");
    if (b) { const line = b.closest("form").querySelector("[data-next-line]"); if (line) line.textContent = b.dataset.next; }
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-decision-card] [data-decision]");
    if (!b) return;
    const form = b.closest("form");
    const note = (composerText()?.value || "").trim();
    if (b.dataset.needsNote && !note) {
      e.preventDefault();
      form.querySelector(".need-note").hidden = false;
      composerText()?.focus();
      return;
    }
    if (b.dataset.confirm && !window.confirm(b.dataset.confirm)) { e.preventDefault(); return; }
    form.elements.decision.value = b.dataset.decision;
    form.elements.note.value = note;
    form.elements.target_stage.value = b.dataset.target || "";
    form.elements.note_as.value = b.dataset.noteAs || "notes";
    if (composerText()) composerText().value = "";
    try { sessionStorage.removeItem("loom-draft-" + location.pathname); } catch (_) {}
  });

  // ---- composer: autosize, Enter to send, file chips
  const ta = composerText();
  if (ta) {
    const size = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 200) + "px"; };
    ta.addEventListener("input", size);
    ta.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (ta.value.trim() || $("#composer-input").files.length) ta.form.requestSubmit(); }
    });
    try { const d = sessionStorage.getItem("loom-draft-" + location.pathname); if (d) { ta.value = d; size(); } } catch (_) {}
    ta.addEventListener("input", () => { try { sessionStorage.setItem("loom-draft-" + location.pathname, ta.value); } catch (_) {} });
    ta.form.addEventListener("submit", () => { try { sessionStorage.removeItem("loom-draft-" + location.pathname); } catch (_) {} });
    $("#composer-input").addEventListener("change", (e) => {
      $("#composer-files").innerHTML = Array.from(e.target.files).map((f) => `<span class="chip">${f.name.replace(/</g, "&lt;")}</span>`).join("");
    });
  }

  // ---- countdowns on undoable decisions
  setInterval(() => $$("[data-countdown]").forEach((el) => {
    const n = Math.max(0, parseInt(el.textContent, 10) - 1); el.textContent = n;
  }), 1000);

  // ---- live updates: poll, swap fragments only when something changed
  const wrap = $("#thread-wrap");
  if (wrap) {
    const eid = wrap.dataset.eid;
    let version = "";
    const atBottom = () => wrap.scrollHeight - wrap.scrollTop - wrap.clientHeight < 120;
    const firstNew = $(".divider", wrap);
    if (firstNew) firstNew.scrollIntoView({ block: "center" }); else wrap.scrollTop = wrap.scrollHeight;
    if (location.hash === "#live") { const live = $("#live"); if (live) live.scrollIntoView({ block: "end" }); }
    async function poll() {
      try {
        const r = await fetch(`/e/${eid}/live?v=${version}`, { credentials: "same-origin" });
        const data = await r.json();
        if (data.mode && data.mode !== wrap.dataset.mode) { location.reload(); return; }
        if (version && data.version !== version && data.thread) {
          const stick = atBottom();
          $("#thread").innerHTML = data.thread;
          $("#bar").innerHTML = data.bar;
          const panel = $("#panel"); if (panel) panel.innerHTML = data.panel;
          $("#nav-groups").innerHTML = data.nav;
          if (stick) wrap.scrollTop = wrap.scrollHeight;
        }
        version = data.version;
      } catch (_) { /* offline for a moment; try again */ }
      setTimeout(poll, document.hidden ? 10000 : 2500);
    }
    poll();
  }

  // ---- kickoff: three questions, one at a time
  const ko = $("#kickoff");
  if (ko) {
    const people = [];
    const dt = new DataTransfer();
    const answers = {
      1: () => ko.elements.challenge.value.trim() + (ko.elements.challenge_type.value ? ` · ${ko.elements.challenge_type.value}` : ""),
      2: () => (people.length ? people.map((p) => `${p.name} (${p.role})`).join(", ") : "Nobody yet") + (ko.elements.client.value ? ` · ${ko.elements.client.value}` : ""),
      3: () => dt.files.length ? Array.from(dt.files).map((f) => f.name).join(", ") : "No files yet",
    };
    const show = (n) => {
      $$(".kq", ko).forEach((s) => {
        const q = parseInt(s.dataset.q, 10);
        s.hidden = q > n;
        const sum = s.querySelector(".kq-summary"), ans = s.querySelector(".kq-answer");
        if (sum && ans) { const done = q < n; sum.hidden = !done; ans.hidden = done; if (done) sum.querySelector("p").textContent = answers[q](); }
      });
      if (n === 4) summarise();
      const sec = $(`.kq[data-q="${n}"]`, ko);
      sec.scrollIntoView({ behavior: "smooth", block: "end" });
      const f = sec.querySelector("textarea, input:not([type=hidden]):not([type=file])"); if (f) f.focus();
    };
    ko.addEventListener("click", (e) => {
      const b = e.target.closest("[data-next]"); if (!b) return;
      const n = parseInt(b.dataset.next, 10);
      if (n === 2 && !ko.elements.challenge.value.trim()) { ko.elements.challenge.focus(); return; }
      show(n);
    });
    const renderPeople = () => {
      $("#people").innerHTML = people.map((p, i) => `<span class="chip on">${esc(p.name)} · ${esc(p.role)}<button type="button" class="link x" data-rm="${i}" aria-label="Remove">✕</button></span>`).join("")
        + people.map((p) => `<input type="hidden" name="person" value='${esc(JSON.stringify(p))}'>`).join("");
    };
    const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/'/g, "&#39;").replace(/"/g, "&quot;");
    const add = () => {
      const name = $("#person-name").value.trim(); if (!name) return;
      people.push({ name, role: $("#person-role").value }); $("#person-name").value = ""; $("#person-role").value = "Stakeholder"; renderPeople(); $("#person-name").focus();
    };
    $("#add-person").addEventListener("click", add);
    $("#person-name").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    $("#people").addEventListener("click", (e) => { const x = e.target.closest("[data-rm]"); if (x) { people.splice(+x.dataset.rm, 1); renderPeople(); } });

    const input = $("#files"), drop = $("#drop");
    const kindOf = (n) => /\.(vtt|srt)$/i.test(n) || /transcript/i.test(n) ? "transcripts" : "documents";
    const renderFiles = () => {
      input.files = dt.files;
      $("#filelist").innerHTML = Array.from(dt.files).map((f, i) => `<li><span>${esc(f.name)}</span>
        <select name="kind" aria-label="Kind of file"><option value="transcripts" ${kindOf(f.name) === "transcripts" ? "selected" : ""}>Meeting transcript</option>
        <option value="documents" ${kindOf(f.name) === "documents" ? "selected" : ""}>Document or diagram</option></select>
        <button type="button" class="link" data-rmf="${i}">Remove</button></li>`).join("");
    };
    input.addEventListener("change", () => { Array.from(input.files).forEach((f) => { if (!Array.from(dt.files).some((g) => g.name === f.name)) dt.items.add(f); }); renderFiles(); });
    ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => { Array.from(e.dataTransfer.files).forEach((f) => dt.items.add(f)); renderFiles(); });
    $("#filelist").addEventListener("click", (e) => { const x = e.target.closest("[data-rmf]"); if (x) { dt.items.remove(+x.dataset.rmf); renderFiles(); } });

    function summarise() {
      const c = ko.elements.challenge.value.trim();
      $("#u-challenge").textContent = c + (ko.elements.challenge_type.value ? ` (${ko.elements.challenge_type.value})` : "");
      $("#u-people").textContent = people.length ? people.map((p) => `${p.name} (${p.role})`).join(", ") : "None yet: you can add them in the brief later.";
      $("#u-files").textContent = dt.files.length ? Array.from(dt.files).map((f) => f.name).join(", ") : "No files yet: you can drop them into the conversation.";
      if (!$("#u-title").value) $("#u-title").value = c.split(/\s+/).slice(0, 6).join(" ").replace(/[.,;:]$/, "");
    }
  }
})();
