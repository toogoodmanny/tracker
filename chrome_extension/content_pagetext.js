/**
 * content_pagetext.js
 *
 * Injected into all non-Chrome pages.
 * Sends a sample of visible page text once per page load.
 * Used to understand what article/content the user is reading.
 * 500 char limit — enough for topic detection, not enough to be a copyright issue.
 */

(function () {
  "use strict";

  const MAX_CHARS = 500;

  function extractPageText() {
    // Prefer article body content over nav/footer noise
    const selectors = [
      "article",
      "main",
      "[role='main']",
      ".post-content",
      ".article-body",
      ".entry-content",
    ];

    for (const sel of selectors) {
      const el = document.querySelector(sel);
      if (el) {
        const text = el.innerText || el.textContent || "";
        const cleaned = text.replace(/\s+/g, " ").trim();
        if (cleaned.length > 100) {
          return cleaned.substring(0, MAX_CHARS);
        }
      }
    }

    // Fallback: body text, stripping script/style content
    const body = document.body;
    if (!body) return "";

    // Clone to avoid mutating the page
    const clone = body.cloneNode(true);
    clone.querySelectorAll("script, style, nav, footer, header").forEach(el => el.remove());
    const text = (clone.innerText || clone.textContent || "").replace(/\s+/g, " ").trim();
    return text.substring(0, MAX_CHARS);
  }

  function sendPageText() {
    const text = extractPageText();
    if (!text || text.length < 50) return; // Too short to be useful

    chrome.runtime.sendMessage({
      type: "page_text_sample",
      text: text,
    }).catch(() => {
      // Extension context invalidated — ignore
    });
  }

  // Send once after page settles
  if (document.readyState === "complete") {
    setTimeout(sendPageText, 1500);
  } else {
    window.addEventListener("load", () => setTimeout(sendPageText, 1500));
  }
})();
