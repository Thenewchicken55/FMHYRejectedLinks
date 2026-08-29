const PAGE_SIZE = 40;

const state = {
  all: [],
  filtered: [],
  page: 0,
};

const els = {
  subtitle: document.getElementById("subtitle"),
  search: document.getElementById("search"),
  results: document.getElementById("results"),
  empty: document.getElementById("empty"),
  pager: document.getElementById("pager"),
  pageInfo: document.getElementById("pageInfo"),
  prev: document.getElementById("prev"),
  next: document.getElementById("next"),
};

function escapeHtml(str) {
  return (str || "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function render() {
  const start = state.page * PAGE_SIZE;
  const pageItems = state.filtered.slice(start, start + PAGE_SIZE);

  els.results.innerHTML = pageItems.map((item) => `
    <li>
      <a class="title" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">
        ${escapeHtml(item.title || item.url)}
      </a>
      ${item.summary ? `<p class="summary">${escapeHtml(item.summary)}</p>` : ""}
      <span class="url">${escapeHtml(item.url)}</span>
    </li>
  `).join("");

  els.empty.hidden = state.filtered.length !== 0;

  const totalPages = Math.max(1, Math.ceil(state.filtered.length / PAGE_SIZE));
  els.pager.hidden = state.filtered.length <= PAGE_SIZE;
  els.pageInfo.textContent = `Page ${state.page + 1} of ${totalPages}`;
  els.prev.disabled = state.page === 0;
  els.next.disabled = state.page >= totalPages - 1;
}

function applySearch(query) {
  const q = query.trim().toLowerCase();
  state.page = 0;
  if (!q) {
    state.filtered = state.all;
  } else {
    const terms = q.split(/\s+/);
    state.filtered = state.all.filter((item) => {
      const haystack = item._haystack;
      return terms.every((t) => haystack.includes(t));
    });
  }
  render();
}

let debounceTimer;
els.search.addEventListener("input", (e) => {
  clearTimeout(debounceTimer);
  const value = e.target.value;
  debounceTimer = setTimeout(() => applySearch(value), 120);
});

els.prev.addEventListener("click", () => {
  if (state.page > 0) {
    state.page -= 1;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
});

els.next.addEventListener("click", () => {
  const totalPages = Math.ceil(state.filtered.length / PAGE_SIZE);
  if (state.page < totalPages - 1) {
    state.page += 1;
    render();
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
});

fetch("data.json")
  .then((res) => res.json())
  .then((data) => {
    state.all = data.map((item) => ({
      ...item,
      _haystack: `${item.title || ""} ${item.summary || ""} ${item.url}`.toLowerCase(),
    }));
    state.filtered = state.all;
    els.subtitle.textContent = `${state.all.length.toLocaleString()} links`;
    render();
  })
  .catch((err) => {
    els.subtitle.textContent = "Failed to load data.json";
    console.error(err);
  });
