/* ── submit.js — metadata fetch, type toggle, tag autocomplete ────────────── */
"use strict";

// ── Item type toggle ─────────────────────────────────────────────────────────
const paperFields  = document.getElementById("paper-fields");
const fetchBtnWrap = document.getElementById("fetch-btn-wrap");
const radios = document.querySelectorAll('input[name="item_type"]');

function updatePaperFields() {
  const isPaper = document.querySelector('input[name="item_type"]:checked')?.value === "paper";
  paperFields.classList.toggle("hidden", !isPaper);
  if (fetchBtnWrap) fetchBtnWrap.classList.toggle("hidden", !isPaper);
  // Make paper fields required only when paper type is selected
  paperFields.querySelectorAll(".paper-req").forEach((el) => {
    el.closest("label")?.querySelector("input")?.toggleAttribute("required", isPaper);
  });
}

radios.forEach((r) => r.addEventListener("change", updatePaperFields));
updatePaperFields(); // init

// ── Metadata fetch ───────────────────────────────────────────────────────────
const fetchBtn        = document.getElementById("fetch-meta-btn");
const urlInput        = document.getElementById("url-input");
const titleInput      = document.getElementById("title-input");
const journalInput    = document.getElementById("journal-input");
const firstAuthorInput = document.getElementById("first-author-input");
const lastAuthorInput  = document.getElementById("last-author-input");
const pubDateInput    = document.getElementById("pub-date-input");
const metaStatus      = document.getElementById("meta-status");

if (fetchBtn) {
  fetchBtn.addEventListener("click", async () => {
    const url = urlInput.value.trim();
    if (!url) {
      metaStatus.textContent = "Please enter a URL first.";
      return;
    }

    fetchBtn.disabled = true;
    metaStatus.textContent = "Fetching…";

    try {
      const res = await fetch(`/api/metadata?url=${encodeURIComponent(url)}`);
      const data = await res.json();

      if (data.title) titleInput.value = data.title;
      if (data.journal && journalInput)      journalInput.value = data.journal;
      if (data.first_author && firstAuthorInput) firstAuthorInput.value = data.first_author;
      if (data.last_author && lastAuthorInput)   lastAuthorInput.value  = data.last_author;
      if (data.publication_date && pubDateInput) pubDateInput.value     = data.publication_date;

      const filled = [data.title, data.journal, data.first_author].filter(Boolean).length;
      if (filled > 0) {
        metaStatus.textContent = `✓ Filled ${filled} field(s) automatically`;
      } else {
        const hasDoi = /10\.\d{4,}\/\S+/.test(url) || /doi\.org/i.test(url);
        metaStatus.textContent = hasDoi
          ? "No metadata found — please fill manually."
          : "No metadata found — please fill manually. Try a DOI (e.g. 10.1038/…) or a doi.org link instead.";
      }
    } catch (err) {
      metaStatus.textContent = "Error fetching metadata.";
      console.error(err);
    } finally {
      fetchBtn.disabled = false;
    }
  });
}

// ── Tag autocomplete ─────────────────────────────────────────────────────────
const MAX_TAGS = 5;
const tagsInput = document.getElementById("tags-input");
const tagSuggestions = document.getElementById("tag-suggestions");
const tagHint = document.getElementById("tag-hint");
let suggestTimeout = null;

if (tagsInput && tagSuggestions) {
  tagsInput.addEventListener("input", () => {
    updateTagHint();
    clearTimeout(suggestTimeout);
    suggestTimeout = setTimeout(fetchSuggestions, 200);
  });

  tagsInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideSuggestions();
  });

  document.addEventListener("click", (e) => {
    if (!tagsInput.contains(e.target) && !tagSuggestions.contains(e.target)) {
      hideSuggestions();
    }
  });
}

function countTags() {
  if (!tagsInput) return 0;
  return tagsInput.value.split(",").map(t => t.trim()).filter(Boolean).length;
}

function updateTagHint() {
  if (!tagHint) return;
  const n = countTags();
  tagHint.textContent = `${n} / ${MAX_TAGS} tags`;
  tagHint.style.color = n > MAX_TAGS ? "#c0392b" : "#999";
}

function getCurrentTag() {
  const val = tagsInput.value;
  const parts = val.split(",");
  return parts[parts.length - 1].trim();
}

async function fetchSuggestions() {
  if (countTags() >= MAX_TAGS) { hideSuggestions(); return; }
  const query = getCurrentTag();
  if (query.length < 2) { hideSuggestions(); return; }
  try {
    const res = await fetch(`/api/tags/suggest?q=${encodeURIComponent(query)}`);
    const tags = await res.json();
    if (tags.length === 0) { hideSuggestions(); return; }
    tagSuggestions.innerHTML = "";
    tags.forEach((tag) => {
      const item = document.createElement("div");
      item.className = "tag-suggestion-item";
      item.textContent = tag.name;
      item.addEventListener("click", () => selectTag(tag.name));
      tagSuggestions.appendChild(item);
    });
    tagSuggestions.classList.remove("hidden");
  } catch (err) {
    hideSuggestions();
  }
}

function selectTag(name) {
  const parts = tagsInput.value.split(",").map(t => t.trim()).filter(Boolean);
  parts[parts.length - 1] = name;
  if (parts.length > MAX_TAGS) parts.length = MAX_TAGS;
  tagsInput.value = parts.join(", ") + (parts.length < MAX_TAGS ? ", " : "");
  hideSuggestions();
  updateTagHint();
  tagsInput.focus();
}

function hideSuggestions() {
  tagSuggestions.classList.add("hidden");
  tagSuggestions.innerHTML = "";
}

// Init hint
updateTagHint();
