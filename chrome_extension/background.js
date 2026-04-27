/**
 * background.js
 *
 * Service worker for the tracker Chrome extension.
 * Connects to the daemon's WebSocket server on localhost:27182.
 * Sends tab events: URL changes, page titles, YouTube video titles.
 *
 * Never collects passwords, form data, or any content from banking/private pages.
 */

const DAEMON_WS_URL = "ws://localhost:27182";
const RECONNECT_DELAY_MS = 5000;
const MAX_PAGE_TEXT_CHARS = 500;

let ws = null;
let reconnectTimer = null;
let isConnected = false;

// ---------------------------------------------------------------------------
// WebSocket connection management
// ---------------------------------------------------------------------------

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    ws = new WebSocket(DAEMON_WS_URL);
  } catch (err) {
    console.debug("[tracker] WebSocket creation failed:", err.message);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.debug("[tracker] Connected to daemon");
    isConnected = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  ws.onclose = (event) => {
    console.debug("[tracker] Disconnected from daemon (code:", event.code, ")");
    isConnected = false;
    ws = null;
    scheduleReconnect();
  };

  ws.onerror = (err) => {
    // Errors are followed by onclose — reconnect handles it there
    console.debug("[tracker] WebSocket error");
  };

  ws.onmessage = (event) => {
    // Daemon can send ack messages — we ignore them
    console.debug("[tracker] Daemon message:", event.data);
  };
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWebSocket();
  }, RECONNECT_DELAY_MS);
}

function sendEvent(payload) {
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    return; // Daemon not running — silently drop
  }
  try {
    ws.send(JSON.stringify(payload));
  } catch (err) {
    console.debug("[tracker] Send failed:", err.message);
  }
}

// ---------------------------------------------------------------------------
// Tab event listeners
// ---------------------------------------------------------------------------

chrome.tabs.onActivated.addListener(async (activeInfo) => {
  try {
    const tab = await chrome.tabs.get(activeInfo.tabId);
    if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
      return;
    }
    sendEvent({
      type: "tab_activated",
      url: tab.url,
      title: tab.title || "",
      timestamp: Date.now(),
    });
  } catch (err) {
    console.debug("[tracker] onActivated error:", err.message);
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  if (changeInfo.status !== "complete") return;
  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
    return;
  }
  try {
    sendEvent({
      type: "tab_updated",
      url: tab.url,
      title: tab.title || "",
      timestamp: Date.now(),
    });
  } catch (err) {
    console.debug("[tracker] onUpdated error:", err.message);
  }
});

// ---------------------------------------------------------------------------
// Messages from content scripts
// ---------------------------------------------------------------------------

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || !message.type) return;

  switch (message.type) {
    case "youtube_title_changed":
      sendEvent({
        type: "youtube_video",
        url: sender.tab?.url || "",
        video_title: message.videoTitle || "",
        channel: message.channel || "",
        timestamp: Date.now(),
      });
      break;

    case "page_text_sample":
      sendEvent({
        type: "page_text",
        url: sender.tab?.url || "",
        page_title: sender.tab?.title || "",
        text_sample: (message.text || "").substring(0, MAX_PAGE_TEXT_CHARS),
        timestamp: Date.now(),
      });
      break;

    default:
      console.debug("[tracker] Unknown message type:", message.type);
  }
});

// ---------------------------------------------------------------------------
// Startup
// ---------------------------------------------------------------------------

connectWebSocket();

// Keep service worker alive by checking connection periodically
setInterval(() => {
  if (!isConnected) {
    connectWebSocket();
  }
}, 10000);
