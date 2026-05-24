const form = document.querySelector("#research-form");
const statusEl = document.querySelector("#status");
const answerEl = document.querySelector("#answer");
const timingsEl = document.querySelector("#timings");
const failuresEl = document.querySelector("#failures");
const citationsEl = document.querySelector("#citations");
const submitButton = document.querySelector("#submit-button");

function setStatus(message, busy = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("busy", busy);
  submitButton.disabled = busy;
}

function renderList(target, items, formatter) {
  target.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    formatter(li, item);
    target.appendChild(li);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Researching", true);

  const data = new FormData(form);

  try {
    const response = await fetch("/api/research", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        question: data.get("question"),
        sources: data.getAll("sources"),
        limit: Number(data.get("limit") || 3),
        offline: data.has("offline"),
        no_cache: data.has("no_cache")
      })
    });

    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Request failed");
    }

    answerEl.textContent = payload.answer;

    renderList(timingsEl, Object.entries(payload.timings), (li, [source, seconds]) => {
      li.textContent = `${source}: ${Number(seconds).toFixed(3)}s`;
    });

    renderList(failuresEl, payload.failures, (li, failure) => {
      li.textContent = `${failure.source}: ${failure.error}`;
    });

    renderList(citationsEl, payload.citations, (li, citation) => {
      const link = document.createElement("a");
      link.href = citation.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = `[${citation.index}] ${citation.title}`;

      const meta = document.createElement("p");
      meta.className = "snippet";
      meta.textContent = `${citation.origin} - ${citation.snippet}`;

      li.append(link, meta);
    });

    setStatus(`${payload.elapsed_seconds.toFixed(2)}s`);
  } catch (error) {
    answerEl.textContent = error.message;
    timingsEl.innerHTML = "";
    failuresEl.innerHTML = "";
    citationsEl.innerHTML = "";
    setStatus("Error");
  }
});
