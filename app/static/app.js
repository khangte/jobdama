(function () {
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
    popover.hidden = !popover.hidden;
    status.setAttribute("aria-expanded", String(!popover.hidden));
    if (!popover.hidden) document.getElementById("disconnect-open").focus();
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
