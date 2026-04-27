/**
 * content_youtube.js
 *
 * Injected into YouTube pages only.
 * Watches for video title changes (including autoplay to next video)
 * and sends them to the background service worker.
 *
 * Uses MutationObserver on document.title — more reliable than URL change
 * since YouTube is a SPA and titles update before navigation settles.
 */

(function () {
  "use strict";

  let lastTitle = "";
  let lastChannel = "";

  function extractVideoTitle() {
    // Primary: h1 inside ytd-video-primary-info-renderer
    const h1 = document.querySelector("ytd-video-primary-info-renderer h1 yt-formatted-string");
    if (h1 && h1.textContent.trim()) {
      return h1.textContent.trim();
    }
    // Fallback: document title (strips " - YouTube" suffix)
    const docTitle = document.title || "";
    return docTitle.replace(/ - YouTube$/, "").trim();
  }

  function extractChannelName() {
    const channel = document.querySelector("ytd-channel-name yt-formatted-string#text a");
    if (channel && channel.textContent.trim()) {
      return channel.textContent.trim();
    }
    return "";
  }

  function checkAndSendIfChanged() {
    const title = extractVideoTitle();
    const channel = extractChannelName();

    if (!title || title === lastTitle) return;
    if (title === "YouTube") return; // homepage, not a video

    lastTitle = title;
    lastChannel = channel;

    chrome.runtime.sendMessage({
      type: "youtube_title_changed",
      videoTitle: title,
      channel: channel,
    }).catch(() => {
      // Extension context may have been invalidated — ignore
    });
  }

  // Watch document.title for SPA navigation
  const observer = new MutationObserver(() => {
    checkAndSendIfChanged();
  });

  observer.observe(document.querySelector("title") || document.head, {
    subtree: true,
    characterData: true,
    childList: true,
  });

  // Also check on initial load
  if (document.readyState === "complete") {
    checkAndSendIfChanged();
  } else {
    window.addEventListener("load", checkAndSendIfChanged);
  }

  // Poll every 3 seconds as a fallback for missed mutations
  setInterval(checkAndSendIfChanged, 3000);
})();
