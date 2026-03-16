const els = {
  apiBase: document.getElementById("apiBase"),
  videoUrl: document.getElementById("videoUrl"),
  language: document.getElementById("language"),
  fallbackTranscribe: document.getElementById("fallbackTranscribe"),
  runBtn: document.getElementById("runBtn"),
  status: document.getElementById("status"),
  resultText: document.getElementById("resultText"),
  downloadTxt: document.getElementById("downloadTxt"),
  downloadSrt: document.getElementById("downloadSrt"),
};

let lastResult = null;
const DEFAULT_API_BASE = "http://127.0.0.1:8001";
const API_BASE_CANDIDATES = [
  DEFAULT_API_BASE,
  "http://localhost:8001",
  "http://127.0.0.1:8000",
  "http://localhost:8000",
];

function setStatus(msg, isError = false) {
  els.status.textContent = msg;
  els.status.classList.toggle("error", isError);
}

function normalizeApiBase(raw) {
  return (raw || "").trim().replace(/\/$/, "");
}

function suggestFilename(rawTitle) {
  return (rawTitle || "transcript")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 100);
}

function downloadFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

async function loadCurrentTabUrl() {
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    const tab = tabs[0];
    if (!tab || !tab.url) return;
    if (tab.url.startsWith("http://") || tab.url.startsWith("https://")) {
      els.videoUrl.value = tab.url;
    }
  } catch {
    // Ignore.
  }
}

async function loadSettings() {
  try {
    const stored = await chrome.storage.local.get(["apiBase", "language", "fallbackTranscribe"]);
    if (stored.apiBase) {
      els.apiBase.value = normalizeApiBase(stored.apiBase);
    }
    if (stored.language) els.language.value = stored.language;
    if (typeof stored.fallbackTranscribe === "boolean") {
      els.fallbackTranscribe.checked = stored.fallbackTranscribe;
    } else {
      // Default on: many videos have no public subtitles.
      els.fallbackTranscribe.checked = true;
    }
  } catch {
    // Ignore.
  }
}

async function saveSettings() {
  try {
    await chrome.storage.local.set({
      apiBase: normalizeApiBase(els.apiBase.value) || DEFAULT_API_BASE,
      language: els.language.value.trim(),
      fallbackTranscribe: els.fallbackTranscribe.checked,
    });
  } catch {
    // Ignore.
  }
}

async function probeBackend(apiBase) {
  try {
    const res = await fetch(`${apiBase}/health`, { method: "GET" });
    if (!res.ok) return false;
    const data = await res.json().catch(() => null);
    return !!(data && typeof data === "object" && data.status === "ok");
  } catch {
    return false;
  }
}

async function resolveApiBase(inputBase) {
  const normalizedInput = normalizeApiBase(inputBase);
  const candidates = [...new Set([normalizedInput, ...API_BASE_CANDIDATES].filter(Boolean))];
  for (const base of candidates) {
    if (await probeBackend(base)) {
      return base;
    }
  }
  return null;
}

async function callTranscriptApi(apiBase, payload) {
  const res = await fetch(`${apiBase}/api/transcript`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }

  return { res, data };
}

async function runTranscript() {
  const inputApiBase = normalizeApiBase(els.apiBase.value) || DEFAULT_API_BASE;
  const videoUrl = els.videoUrl.value.trim();
  const language = els.language.value.trim();

  if (!videoUrl) {
    setStatus("Please fill Video URL", true);
    return;
  }

  els.runBtn.disabled = true;
  els.downloadTxt.disabled = true;
  els.downloadSrt.disabled = true;
  setStatus("Generating transcript...");

  try {
    const resolvedApiBase = await resolveApiBase(inputApiBase);
    if (!resolvedApiBase) {
      throw new Error("Backend not reachable. Start API at http://127.0.0.1:8001.");
    }
    if (resolvedApiBase !== inputApiBase) {
      els.apiBase.value = resolvedApiBase;
    }

    let payload = {
      url: videoUrl,
      language: language || null,
      fallback_transcribe: els.fallbackTranscribe.checked,
    };

    await saveSettings();

    let { res, data } = await callTranscriptApi(resolvedApiBase, payload);
    const detail = data && data.detail ? String(data.detail) : "";

    if (!res.ok && detail.includes("No subtitles available for this video") && !payload.fallback_transcribe) {
      // Auto-retry once via Whisper so users don't have to guess this toggle.
      setStatus("No public subtitles. Retrying with Whisper fallback...");
      payload = { ...payload, fallback_transcribe: true };
      els.fallbackTranscribe.checked = true;
      await saveSettings();
      ({ res, data } = await callTranscriptApi(resolvedApiBase, payload));
    }

    if (!res.ok) {
      const finalDetail = data && data.detail ? String(data.detail) : "";
      if (res.status === 404 && finalDetail === "Not Found") {
        throw new Error(`Wrong backend address: ${resolvedApiBase}`);
      }
      const msg = finalDetail || `HTTP ${res.status}`;
      throw new Error(msg);
    }

    lastResult = data;
    els.resultText.value = data.text || "";
    els.downloadTxt.disabled = false;
    els.downloadSrt.disabled = false;

    const len = (data.text || "").length;
    setStatus(`Done: ${data.title} | source=${data.source} | chars=${len}`);
  } catch (err) {
    lastResult = null;
    const message = err && err.message ? err.message : String(err);
    setStatus(`Failed: ${message}`, true);
  } finally {
    els.runBtn.disabled = false;
  }
}

function setupEvents() {
  els.runBtn.addEventListener("click", runTranscript);

  els.downloadTxt.addEventListener("click", () => {
    if (!lastResult) return;
    const base = suggestFilename(lastResult.title);
    downloadFile(`${base}.txt`, lastResult.text || "", "text/plain;charset=utf-8");
  });

  els.downloadSrt.addEventListener("click", () => {
    if (!lastResult) return;
    const base = suggestFilename(lastResult.title);
    downloadFile(`${base}.srt`, lastResult.srt || "", "application/x-subrip;charset=utf-8");
  });
}

(async function bootstrap() {
  setupEvents();
  await loadSettings();
  if (!normalizeApiBase(els.apiBase.value)) {
    els.apiBase.value = DEFAULT_API_BASE;
  }
  await loadCurrentTabUrl();
})();
