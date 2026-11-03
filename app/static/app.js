// 붙여넣기 즉시 접수. 입력란은 접수 순간 비우고 포커스를 유지한다.
// 응답을 기다리지 않으므로 여러 링크를 연달아 붙여넣어도 각각 따로 접수된다.
(function () {
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
      const res = await fetch("/api/jobs", {
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
  window.addEventListener("focus", () => input.focus());
})();
