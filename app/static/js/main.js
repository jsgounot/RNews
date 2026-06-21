/* ── main.js — voting + comment replies ────────────────────────────────── */
"use strict";

// ── Item voting ──────────────────────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".vote-btn, .vote-btn-lg");
  if (!btn) return;
  const itemId = btn.dataset.id;
  if (!itemId) return;

  try {
    const res = await fetch(`/api/vote/${itemId}`, { method: "POST" });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await res.json();
    // Update all score elements for this item
    document.querySelectorAll(`#score-${itemId}`).forEach((el) => {
      el.textContent = data.score;
    });
    // Toggle voted class on ALL vote buttons for this item
    document.querySelectorAll(`.vote-btn[data-id="${itemId}"], .vote-btn-lg[data-id="${itemId}"]`).forEach((b) => {
      b.classList.toggle("voted", data.voted);
    });
  } catch (err) {
    console.error("Vote failed:", err);
  }
});

// ── Comment voting ───────────────────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".vote-btn-sm");
  if (!btn) return;
  const commentId = btn.dataset.commentId;
  if (!commentId) return;

  try {
    const res = await fetch(`/api/vote_comment/${commentId}`, { method: "POST" });
    if (res.status === 401) {
      window.location.href = "/login";
      return;
    }
    const data = await res.json();
    const scoreEl = document.getElementById(`cscore-${commentId}`);
    if (scoreEl) scoreEl.textContent = data.score;
    btn.classList.toggle("voted", data.voted);
  } catch (err) {
    console.error("Comment vote failed:", err);
  }
});

// ── Reply toggle ─────────────────────────────────────────────────────────────
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".reply-toggle");
  if (!btn) return;
  const commentId = btn.dataset.comment;
  const form = document.getElementById(`reply-form-${commentId}`);
  if (form) {
    form.classList.toggle("hidden");
    if (!form.classList.contains("hidden")) {
      form.querySelector("textarea").focus();
    }
  }
});

document.addEventListener("click", (e) => {
  const btn = e.target.closest(".reply-cancel");
  if (!btn) return;
  const commentId = btn.dataset.comment;
  const form = document.getElementById(`reply-form-${commentId}`);
  if (form) form.classList.add("hidden");
});

// ── Save tag ──────────────────────────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".save-tag-btn");
  if (!btn) return;
  const slug = btn.dataset.slug;

  try {
    const res = await fetch(`/api/tag/${slug}/save`, { method: "POST" });
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    btn.classList.toggle("saved", data.saved);
    btn.querySelector(".save-label").textContent = data.saved ? "Saved" : "Save tag";
  } catch (err) {
    console.error("Save tag failed:", err);
  }
});

// ── Share to team ─────────────────────────────────────────────────────────────
const shareToggle = document.getElementById("share-toggle");
const sharePanel  = document.getElementById("share-panel");
const shareSubmit = document.getElementById("share-submit-btn");
const shareStatus = document.getElementById("share-status");

if (shareToggle && sharePanel) {
  shareToggle.addEventListener("click", () => {
    sharePanel.classList.toggle("hidden");
  });
}

if (shareSubmit) {
  shareSubmit.addEventListener("click", async () => {
    const itemId  = shareSubmit.dataset.item;
    const select  = document.getElementById("share-team-select");
    const teamSlug = select?.value;
    if (!teamSlug) return;

    shareSubmit.disabled = true;
    if (shareStatus) shareStatus.textContent = "Sharing…";

    try {
      const fd = new FormData();
      fd.append("team_slug", teamSlug);
      const res = await fetch(`/api/item/${itemId}/share`, { method: "POST", body: fd });
      const data = await res.json();
      if (res.ok) {
        shareStatus.textContent = data.status === "already_shared"
          ? `Already in "${data.team}"`
          : `Shared to "${data.team}"`;
        shareStatus.className = "share-status ok";
        sharePanel.classList.add("hidden");
        shareToggle.textContent = `↗ Shared to ${data.team}`;
      } else {
        shareStatus.textContent = data.error || "Error";
        shareStatus.className = "share-status err";
      }
    } catch (err) {
      if (shareStatus) { shareStatus.textContent = "Error"; shareStatus.className = "share-status err"; }
    } finally {
      if (shareSubmit) shareSubmit.disabled = false;
    }
  });
}

// ── Remove team item (admin) ──────────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".remove-team-item-btn");
  if (!btn) return;
  const teamSlug = btn.dataset.team;
  const itemId   = btn.dataset.item;
  if (!confirm("Remove this item from the team?")) return;

  try {
    const res = await fetch(`/api/teams/${teamSlug}/remove-item/${itemId}`, { method: "POST" });
    if (res.ok) {
      const row = document.getElementById(`item-${itemId}`);
      if (row) row.remove();
    }
  } catch (err) {
    console.error("Remove item failed:", err);
  }
});

// ── Favorite (star) item ──────────────────────────────────────────────────────
document.addEventListener("click", async (e) => {
  const btn = e.target.closest(".star-btn");
  if (!btn) return;
  const itemId = btn.dataset.id;
  if (!itemId) return;

  try {
    const res = await fetch(`/api/item/${itemId}/favorite`, { method: "POST" });
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    // Update all star buttons for this item on the page
    document.querySelectorAll(`.star-btn[data-id="${itemId}"]`).forEach((b) => {
      b.classList.toggle("starred", data.favorited);
      b.title = data.favorited ? "Remove from favorites" : "Add to favorites";
    });
    // If auto-vote fired, reflect the new score and mark vote buttons as voted
    if (data.auto_voted) {
      document.querySelectorAll(`.vote-btn[data-id="${itemId}"], .vote-btn-lg[data-id="${itemId}"]`).forEach((b) => {
        b.classList.add("voted");
      });
      document.querySelectorAll(`#score-${itemId}, .score-val[id="score-${itemId}"]`).forEach((el) => {
        el.textContent = data.score;
      });
    }
  } catch (err) {
    console.error("Favorite toggle failed:", err);
  }
});
