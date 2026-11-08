(function () {
  let saveSettings = null;  // 조건 폼이 있는 화면에서 아래 설정 블록이 채운다
  let settingsDirty = () => false;
  const status = document.getElementById("status-button");
  const popover = document.getElementById("connection-popover");
  const connectDialog = document.getElementById("connect-dialog");
  const disconnectDialog = document.getElementById("disconnect-dialog");
  const feedback = document.getElementById("notion-feedback");
  function closePopover() {
    if (popover) popover.hidden = true;
    status.setAttribute("aria-expanded", "false");
  }
  function openConnect() {
    document.getElementById("connect-error").hidden = true;
    connectDialog.showModal();
    document.getElementById("token").focus();
  }
  document.getElementById("connect-open")?.addEventListener("click", openConnect);
  status.addEventListener("click", () => {
    if (connectDialog) return openConnect();
    if (!popover) { location.href = "/"; return; }  // 수집 화면에서 미연결이면 메인에서 연결
    popover.hidden = !popover.hidden;
    status.setAttribute("aria-expanded", String(!popover.hidden));
    if (!popover.hidden) document.getElementById("disconnect-open")?.focus();
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".status-wrap")) closePopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && popover && !popover.hidden) {
      closePopover();
      status.focus();
    }
  });
  document.addEventListener("focusin", (e) => {
    if (popover && !popover.hidden && !e.target.closest(".status-wrap")) closePopover();
  });
  document.getElementById("disconnect-open")?.addEventListener("click", () => {
    closePopover();
    document.getElementById("disconnect-error").hidden = true;
    disconnectDialog.showModal();
  });
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => document.getElementById(button.dataset.close).close());
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("cancel", (e) => {
      if (dialog.querySelector('[aria-busy="true"]')) e.preventDefault();
    });
    dialog.addEventListener("close", () => {
      if (dialog === connectDialog) {
        dialog.querySelector("form").reset();
        document.getElementById("token").type = "password";
        const toggle = document.getElementById("token-toggle");
        toggle.textContent = "표시";
        toggle.setAttribute("aria-pressed", "false");
      }
      status.focus();
    });
  });
  document.getElementById("token-toggle")?.addEventListener("click", (e) => {
    const token = document.getElementById("token");
    const show = token.type === "password";
    token.type = show ? "text" : "password";
    e.currentTarget.textContent = show ? "숨기기" : "표시";
    e.currentTarget.setAttribute("aria-pressed", String(show));
  });
  document.querySelectorAll("form[data-notion-action]").forEach((notionForm) => {
    notionForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (notionForm.getAttribute("aria-busy") === "true") return;
      const connecting = notionForm.dataset.notionAction === "connect";
      const errorBox = document.getElementById(connecting ? "connect-error" : "disconnect-error");
      const values = Object.fromEntries(Array.from(new FormData(notionForm), ([key, value]) => [key, value.trim()]));
      errorBox.hidden = true;
      if (connecting && Object.values(values).some((value) => !value || /\s/.test(value))) {
        errorBox.textContent = "토큰과 DB 링크 또는 ID를 공백 없이 입력해 주세요.";
        errorBox.hidden = false;
        errorBox.focus();
        return;
      }
      const controls = notionForm.querySelectorAll("input, button");
      const submit = notionForm.querySelector('[type="submit"]');
      const label = submit.textContent;
      notionForm.setAttribute("aria-busy", "true");
      controls.forEach((control) => { control.disabled = true; });
      submit.textContent = connecting ? "연결 확인 중…" : "해제 중…";
      try {
        const res = await fetch(notionForm.action, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: connecting ? JSON.stringify(values) : undefined,
        });
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data.problems?.join(" · ") || (res.status === 422
            ? "입력값을 확인해 주세요. 토큰과 DB 링크 또는 ID는 공백 없이 입력해 주세요."
            : "요청 실패: 서버 응답 " + res.status));
        }
        notionForm.reset();
        window.location.reload();
      } catch (error) {
        errorBox.textContent = error instanceof TypeError ? "서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요." : error.message;
        errorBox.hidden = false;
        errorBox.focus();
      } finally {
        notionForm.removeAttribute("aria-busy");
        controls.forEach((control) => { control.disabled = false; });
        submit.textContent = label;
      }
    });
  });
  document.getElementById("recheck")?.addEventListener("click", async (e) => {
    e.currentTarget.disabled = true;
    try {
      const res = await fetch("/api/v1/recheck", { method: "POST" });
      if (!res.ok) throw new Error();
      window.location.reload();
    } catch {
      e.target.disabled = false;
      const error = document.getElementById("connect-error");
      error.textContent = "연결을 점검하지 못했어요. 잠시 후 다시 시도해 주세요.";
      error.hidden = false;
      error.focus();
    }
  });
  // #collect는 htmx가 통째로 교체하므로, 폼에 직접 달지 않고 문서에서 받아 이벤트의 현재 폼을 쓴다
  const collectDesktop = window.matchMedia("(min-width: 1100px)");
  let collectPage = 1;
  let collectScope = null;
  let collectPageFocus = null;

  function focusCollectPage(host, key) {
    // 조각 교체나 번호 행 재생성 뒤에도 키보드 위치를 유지한다.
    const target = host.querySelector(`[data-collect-focus="${key}"]`);
    const button = target && !target.disabled ? target : host.querySelector('[aria-current="page"]');
    button?.focus({ preventScroll: true });
  }

  function collectPageButton(label, page, key, disabled = false, current = false) {
    // 번호와 이전·다음에 공통으로 쓰는 접근 가능한 버튼을 만든다.
    const button = document.createElement("button");
    button.type = "button";
    button.className = "page-button";
    button.textContent = label;
    button.dataset.collectPage = String(page);
    button.dataset.collectFocus = key;
    button.disabled = disabled;
    button.setAttribute("aria-label", key === "prev" ? "이전 페이지" : key === "next" ? "다음 페이지" : `${page}페이지`);
    if (current) button.setAttribute("aria-current", "page");
    return button;
  }

  function renderCollectPagination() {
    // 폴링 조각 밖에 둔 페이지 상태를 현재 목록에 다시 적용한다.
    const root = document.getElementById("collect");
    if (!root) return;
    const scope = JSON.stringify([root.dataset.collectStatus, root.dataset.collectRun]);
    if (collectScope !== null && collectScope !== scope) collectPage = 1;
    collectScope = scope;
    const items = [...root.querySelectorAll("[data-collect-item]")];
    const host = root.querySelector("[data-collect-pagination]");
    if (!host) return;
    const focused = host.contains(document.activeElement) ? document.activeElement.dataset.collectFocus : null;
    if (!collectDesktop.matches) {
      items.forEach((item) => { item.hidden = false; });
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    const pages = Math.max(1, Math.ceil(items.length / 10));
    collectPage = Math.max(1, Math.min(collectPage, pages));
    const first = (collectPage - 1) * 10;
    items.forEach((item, index) => { item.hidden = index < first || index >= first + 10; });
    if (pages === 1) {
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    const info = document.createElement("p");
    info.className = "pagination-info";
    info.textContent = `${first + 1}–${Math.min(first + 10, items.length)} / 총 ${items.length}개 · 페이지당 10개 · ${collectPage} / ${pages} 페이지`;
    const nav = document.createElement("nav");
    nav.className = "pagination-controls";
    nav.setAttribute("aria-label", "공고별 결과 페이지 이동");
    const numbers = document.createElement("div");
    numbers.className = "pagination-numbers";
    for (let page = Math.max(1, collectPage - 4); page <= Math.min(pages, collectPage + 4); page++) {
      const button = collectPageButton(String(page), page, `page-${page}`, false, page === collectPage);
      button.style.gridColumn = String(page - collectPage + 5);
      numbers.append(button);
    }
    nav.append(collectPageButton("이전", collectPage - 1, "prev", collectPage === 1), numbers,
      collectPageButton("다음", collectPage + 1, "next", collectPage === pages));
    host.replaceChildren(info, nav);
    host.hidden = false;
    if (focused) focusCollectPage(host, focused);
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-collect-page]");
    if (!button || button.disabled || !collectDesktop.matches) return;
    const next = Number(button.dataset.collectPage);
    if (next === collectPage) return;
    collectPage = next;
    renderCollectPagination();
  });
  document.addEventListener("htmx:beforeSwap", (event) => {
    if (event.detail.target?.id !== "collect") return;
    collectPageFocus = document.activeElement.closest?.(".collect-pagination")
      ? document.activeElement.dataset.collectFocus : null;
  });
  document.addEventListener("htmx:afterSwap", (event) => {
    if (event.target.id !== "collect" && event.detail.target?.id !== "collect") return;
    renderCollectPagination();
    if (collectPageFocus && document.activeElement === document.body) {
      const host = document.querySelector("[data-collect-pagination]");
      if (host && !host.hidden) focusCollectPage(host, collectPageFocus);
    }
    collectPageFocus = null;
  });
  collectDesktop.addEventListener("change", renderCollectPagination);
  renderCollectPagination();

  let collectPending = false;
  document.addEventListener("submit", async (e) => {
    const collectForm = e.target.closest("#collect-form");
    if (!collectForm) return;
    e.preventDefault();
    const runButton = document.getElementById("collect-run");
    if (collectPending || runButton.disabled) return;
    collectPending = true;
    const dryRun = collectForm.elements.dry_run.checked;
    let started = false;
    let errorMessage = "";
    document.getElementById("collect-error").hidden = true;
    runButton.disabled = true;
    try {
      if (settingsDirty() && !(await saveSettings())) {
        errorMessage = "조건을 저장하지 못해 수집을 시작하지 않았어요.";
      } else {
        const res = await fetch("/api/v1/collect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ dry_run: dryRun, trigger: "screen" }),
        });
        if (res.status === 202) {
          collectPage = 1;
          started = true;  // 조각 교체가 버튼 상태를 정한다
          return window.htmx && htmx.trigger(document.body, "collect-started");
        }
        errorMessage = { 409: "이미 수집 중이에요.", 503: "Notion이 연결되지 않았어요." }[res.status] || "요청 실패: 서버 응답 " + res.status;
      }
    } catch {
      errorMessage = "서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.";
    } finally {
      collectPending = false;
      // 새 조각의 실행 중·미연결 상태는 유지하고, 직접 잠근 원래 버튼만 복구한다.
      if (!started && document.getElementById("collect-run") === runButton) runButton.disabled = false;
    }
    // 저장·실행을 기다리는 동안 필터나 폴링으로 교체된 현재 조각에 표시한다.
    const error = document.getElementById("collect-error");
    if (error) {
      error.textContent = errorMessage;
      error.hidden = false;
    }
  });
  const settingsForm = document.getElementById("settings-form");
  if (settingsForm) {
    const SELECTS = ["job", "region", "career", "education", "employment", "salary", "sort"];
    const WORDS = ["title_exclude_words", "body_signal_words"];
    const summary = document.getElementById("settings-error");
    const success = document.getElementById("settings-success");
    const note = document.getElementById("settings-status");
    const field = (name) => settingsForm.elements[name];
    const words = (text) => text.split(/[\n,]/).map((w) => w.trim()).filter(Boolean);

    function clearErrors() {
      // 이전 저장의 필드 오류와 결과 안내를 비운다.
      summary.hidden = success.hidden = true;
      note.textContent = "";
      settingsForm.querySelectorAll(".field-error").forEach((el) => { el.hidden = true; el.textContent = ""; });
      settingsForm.querySelectorAll("[aria-invalid]").forEach((el) => { el.removeAttribute("aria-invalid"); el.removeAttribute("aria-describedby"); });
    }

    function fill(cfg) {
      // 저장된 조건이나 기본값을 입력 요소에 채운다.
      settingsForm.querySelectorAll('input[name="sites"]').forEach((box) => { box.checked = cfg.sites.includes(box.value); });
      field("keyword").value = cfg.keyword;
      field("max_per_site").value = cfg.max_per_site;
      SELECTS.forEach((name) => { field(name).value = cfg[name]; });
      WORDS.forEach((name) => { field(name).value = cfg[name].join("\n"); });
    }

    function payload() {
      // 화면의 입력값을 설정 API 본문으로 만든다.
      const body = {
        sites: [...settingsForm.querySelectorAll('input[name="sites"]:checked')].map((box) => box.value),
        keyword: field("keyword").value,
        max_per_site: field("max_per_site").value,  // 문자열 그대로 보내 2.5·2e1도 서버가 거른다
      };
      SELECTS.forEach((name) => { body[name] = field(name).value; });
      WORDS.forEach((name) => { body[name] = words(field(name).value); });
      return body;
    }

    function showFieldError(name, message) {
      // 필드 오류 문구와 접근성 속성을 함께 표시한다.
      const box = document.getElementById("err-" + name);
      if (!box) return false;
      box.textContent = message;
      box.hidden = false;
      const target = name === "sites" ? document.getElementById("sites-field") : field(name);
      target.setAttribute("aria-invalid", "true");
      target.setAttribute("aria-describedby", box.id);
      return true;
    }

    function showSummary(message) {
      // 저장 실패 요약을 알림 영역에 표시한다.
      summary.textContent = message;
      summary.hidden = false;
    }

    let baseline = JSON.stringify(payload());  // 마지막으로 불러오거나 저장한 값

    function refreshWords() {
      // 접힌 영역에서도 현재 단어 개수를 확인하게 한다.
      const title = words(field("title_exclude_words").value).length;
      const body = words(field("body_signal_words").value).length;
      document.getElementById("words-counts").textContent =
        `공고명 제외 ${title}개 · ${body ? `본문 신호 ${body}개` : "본문 신호 검사 안 함"}`;
    }

    let saving = null;
    async function persistSettings() {
      // 저장 중 입력을 잠가 응답이 새 입력을 덮지 않게 하고 성공 여부를 반환한다.
      clearErrors();
      const controls = settingsForm.querySelectorAll("input, select, textarea, button");
      const body = JSON.stringify(payload());
      settingsForm.setAttribute("aria-busy", "true");
      controls.forEach((control) => { control.disabled = true; });
      try {
        const res = await fetch("/api/v1/collect/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body,
        });
        const data = (res.ok || res.status === 422) ? await res.json() : null;
        controls.forEach((control) => { control.disabled = false; });
        if (res.ok) {
          fill(data.settings);
          baseline = JSON.stringify(payload());
          refreshWords();
          document.getElementById("settings-problem")?.remove();
          success.hidden = false;
          note.textContent = "저장했어요. 다음 수집부터 적용돼요.";
          return true;
        }
        if (res.status === 422) {
          const detail = data.detail || [];
          detail.forEach((d) => { showFieldError(d.type, d.msg) || showFieldError(d.loc[d.loc.length - 1], d.msg); });
          showSummary("입력값을 확인하세요.");
          const first = settingsForm.querySelector('[aria-invalid="true"]');
          if (first) {
            // 첫 오류가 접힌 칸 밖에 있어도 안쪽 오류가 가려지지 않게 연다
            if (settingsForm.querySelector('#words-details [aria-invalid="true"]')) document.getElementById("words-details").open = true;
            (first.matches("fieldset") ? first.querySelector("input") : first).focus();
          }
        } else {
          showSummary({ 403: "다른 사이트에서 온 요청이라 저장하지 않았어요.", 500: "설정을 저장하지 못했어요." }[res.status] || "요청 실패: 서버 응답 " + res.status);
        }
      } catch {
        showSummary("서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.");
      } finally {
        controls.forEach((control) => { control.disabled = false; });
        settingsForm.removeAttribute("aria-busy");
      }
      return false;
    }
    saveSettings = () => {
      // 저장과 실행이 겹쳐도 진행 중인 저장 하나의 결과를 함께 기다린다.
      if (!saving) saving = persistSettings().finally(() => { saving = null; });
      return saving;
    };
    settingsDirty = () => JSON.stringify(payload()) !== baseline;

    settingsForm.addEventListener("submit", (e) => {
      e.preventDefault();
      saveSettings();
    });
    const onEdit = () => {
      refreshWords();
      success.hidden = true;
      note.textContent = settingsDirty() ? "저장하지 않은 변경이 있어요. 수집 실행 시 함께 저장돼요." : "";
    };
    settingsForm.addEventListener("input", onEdit);
    settingsForm.addEventListener("change", onEdit);

    document.getElementById("defaults-button").addEventListener("click", () => {
      clearErrors();
      fill(JSON.parse(document.getElementById("settings-defaults").textContent));
      refreshWords();
      note.textContent = "기본값을 불러왔어요. 저장해야 적용돼요.";
    });
  }
  if (!document.getElementById("job-form")) return;  // 아래는 공고 붙여넣기 화면 전용
  const input = document.getElementById("url");
  const hint = document.getElementById("hint");
  const form = document.getElementById("job-form");
  const URL_RE = /https?:\/\/[^\s<>"']+/g;

  function showHint(message, error = false) {
    hint.textContent = message;
    hint.dataset.error = String(error);
  }

  function refresh() {
    if (window.htmx) htmx.trigger(document.body, "job-submitted");
  }

  async function submitOne(url) {
    try {
      const res = await fetch("/api/v1/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!res.ok) showHint("접수 실패: 서버 응답 " + res.status, true);
    } catch (e) {
      showHint("접수 실패: 서버에 연결할 수 없음", true);
    }
    refresh();
  }

  function submitText(text) {
    if (input.disabled) return;
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    const urls = trimmed.match(URL_RE) || [trimmed];
    input.value = "";
    input.focus();
    showHint(urls.length > 1 ? urls.length + "개 링크 접수" : "접수됨");
    urls.forEach(submitOne);
  }

  input.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    e.preventDefault();
    submitText(text);
  });

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submitText(input.value);
  });

  // 한글 조합 확정 Enter를 공고 접수로 처리하지 않는다.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.isComposing) e.preventDefault();
  });

  // 같은 결과로 목록을 다시 그려 키보드 포커스를 잃지 않도록 한다.
  let lastJobsHTML = null;
  document.addEventListener("htmx:beforeSwap", (e) => {
    if (e.detail.target.id !== "jobs" || e.detail.xhr.status !== 200) return;
    const html = e.detail.xhr.responseText;
    if (html === lastJobsHTML) e.detail.shouldSwap = false;
    else lastJobsHTML = html;
  });

  // 다른 창에서 돌아오면 바로 붙여넣을 수 있도록 포커스 복귀
  window.addEventListener("focus", () => {
    if (!input.disabled && document.activeElement === document.body) input.focus();
  });
})();
