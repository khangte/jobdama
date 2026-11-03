// 붙여넣기 즉시 접수. 입력란은 접수 순간 비우고 포커스를 유지한다.
// 응답을 기다리지 않으므로 여러 링크를 연달아 붙여넣어도 각각 따로 접수된다.
(function () {
  const input = document.getElementById("url");
  const hint = document.getElementById("hint");
  const URL_RE = /https?:\/\/[^\s<>"']+/g;

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
      if (!res.ok) hint.textContent = "접수 실패: 서버 응답 " + res.status;
    } catch (e) {
      hint.textContent = "접수 실패: 서버에 연결할 수 없음";
    }
    refresh();
  }

  function submitText(text) {
    const trimmed = (text || "").trim();
    if (!trimmed) return;
    const urls = trimmed.match(URL_RE) || [trimmed];
    input.value = "";
    input.focus();
    hint.textContent = urls.length > 1 ? urls.length + "개 링크 접수" : "접수됨";
    urls.forEach(submitOne);
  }

  input.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text");
    e.preventDefault();
    submitText(text);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitText(input.value);
    }
  });

  // 다른 창에서 돌아오면 바로 붙여넣을 수 있도록 포커스 복귀
  window.addEventListener("focus", () => input.focus());
})();
