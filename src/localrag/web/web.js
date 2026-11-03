const $ = (selector) => document.querySelector(selector);
const conversation = $("#conversation");
const message = $("#message");
const CHAT_STORAGE_KEY = "localrag.chats.v1";

function loadChats() {
  try {
    const stored = JSON.parse(localStorage.getItem(CHAT_STORAGE_KEY) || "[]");
    return Array.isArray(stored) ? stored.filter((chat) => chat && typeof chat.id === "string" && Array.isArray(chat.messages)) : [];
  } catch (_) { return []; }
}

let chats = loadChats();
let activeChatId = null;
try { activeChatId = localStorage.getItem(`${CHAT_STORAGE_KEY}.active`); } catch (_) { /* storage is optional */ }

function saveChats() {
  try {
    localStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(chats));
    if (activeChatId) localStorage.setItem(`${CHAT_STORAGE_KEY}.active`, activeChatId);
    else localStorage.removeItem(`${CHAT_STORAGE_KEY}.active`);
  } catch (_) {
    message.textContent = "Could not save chat history in this browser's local storage.";
  }
}

function activeChat() {
  return chats.find((chat) => chat.id === activeChatId) || null;
}

function renderChatList() {
  const list = $("#chat-list");
  list.replaceChildren();
  chats.forEach((chat) => {
    const item = document.createElement("li");
    const select = document.createElement("button");
    select.type = "button";
    select.className = "chat-select";
    select.textContent = chat.title || "New chat";
    select.title = chat.title || "New chat";
    select.setAttribute("aria-current", String(chat.id === activeChatId));
    select.addEventListener("click", () => selectChat(chat.id));
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "chat-action";
    rename.textContent = "Rename";
    rename.setAttribute("aria-label", `Rename ${chat.title || "chat"}`);
    rename.addEventListener("click", () => {
      const title = prompt("Conversation name", chat.title || "New chat");
      if (title?.trim()) {
        chat.title = title.trim().slice(0, 80);
        saveChats();
        renderChatList();
      }
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chat-action";
    remove.textContent = "×";
    remove.setAttribute("aria-label", `Delete ${chat.title || "chat"}`);
    remove.addEventListener("click", () => {
      if (!confirm(`Delete conversation “${chat.title || "New chat"}”?`)) return;
      chats = chats.filter((itemChat) => itemChat.id !== chat.id);
      if (activeChatId === chat.id) activeChatId = chats[0]?.id || null;
      if (!activeChatId) createChat();
      else { saveChats(); renderChatList(); renderConversation(); }
    });
    item.append(select, rename, remove);
    list.append(item);
  });
  $("#export-chat").disabled = !(activeChat()?.messages.length);
}

function renderConversation() {
  conversation.replaceChildren();
  const chat = activeChat();
  if (!chat || !chat.messages.length) {
    const welcome = document.createElement("div");
    welcome.className = "welcome";
    const mark = document.createElement("div");
    mark.className = "welcome-mark";
    mark.textContent = "✦";
    const title = document.createElement("h2");
    title.textContent = "What can I help you find?";
    const help = document.createElement("p");
    help.textContent = "Add some documents, then ask a question about them.";
    welcome.append(mark, title, help);
    conversation.append(welcome);
    return;
  }
  chat.messages.forEach((entry) => {
    const row = addMessage(entry.role, entry.content, entry.sources || []);
    if (entry.role === "assistant") {
      renderCitedAnswer(row.querySelector(".bubble"), row.querySelector(".sources"), entry.content, entry.sources || []);
    }
  });
}

function createChat() {
  const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  chats.unshift({ id, title: "New chat", messages: [] });
  activeChatId = id;
  saveChats();
  renderChatList();
  renderConversation();
}

function selectChat(id) {
  activeChatId = id;
  saveChats();
  renderChatList();
  renderConversation();
}

function recordChatMessage(chatId, role, content, sources = []) {
  const chat = chats.find((itemChat) => itemChat.id === chatId);
  if (!chat) return;
  chat.messages.push({ role, content, sources });
  if (role === "user" && chat.title === "New chat") chat.title = content.slice(0, 48);
  saveChats();
  renderChatList();
}

function setChatControlsDisabled(disabled) {
  $("#new-chat").disabled = disabled;
  $("#chat-list").querySelectorAll("button").forEach((button) => { button.disabled = disabled; });
}

function showError(error) {
  message.textContent = error instanceof Error ? error.message : String(error);
}

async function request(url, options = {}) {
  try {
    const response = await fetch(url, options);
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Request failed.");
    return body;
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("LocalRAG is not connected. Keep the server running and reload this page.");
    }
    throw error;
  }
}

async function streamRequest(url, options, onEvent) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new Error("LocalRAG is not connected. Keep the server running and reload this page.");
    }
    throw error;
  }
  if (!response.ok) {
    let detail = "Request failed.";
    try { detail = (await response.json()).detail || detail; } catch (_) { /* keep fallback */ }
    throw new Error(detail);
  }
  if (!response.body) throw new Error("This browser cannot read streamed responses.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (block) => {
    const data = block.split("\n").find((line) => line.startsWith("data:"));
    if (data) onEvent(JSON.parse(data.slice(5).trim()));
  };
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replaceAll("\r\n", "\n");
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) >= 0) {
      dispatch(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);
    }
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}

async function refreshState() {
  const state = await request("/api/state");
  $("#document-count").textContent = state.document_count;
  const list = $("#document-list");
  list.replaceChildren();
  const documentItems = state.document_items || state.documents.map((name) => ({ id: name, name }));
  documentItems.forEach((documentItem) => {
    const name = documentItem.name;
    const item = document.createElement("li");
    const label = document.createElement("span");
    label.className = "document-name";
    label.title = name;
    label.textContent = name;
    const remove = document.createElement("button");
    remove.className = "remove-document-button";
    remove.type = "button";
    remove.textContent = "×";
    remove.title = `Remove ${name}`;
    remove.setAttribute("aria-label", `Remove ${name}`);
    remove.addEventListener("click", async () => {
      try {
        await request(`/api/documents/${encodeURIComponent(documentItem.id)}`, { method: "DELETE" });
        await refreshState();
        message.textContent = `Removed ${name} from the index.`;
      } catch (error) { showError(error); }
    });
    item.append(label, remove);
    list.append(item);
  });
}

async function refreshModels() {
  try {
    const body = await request("/api/models");
    const select = $("#model-select");
    const current = select.value;
    const detectedModels = body.providers.flatMap((provider) => provider.models || []);
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = detectedModels.length ? "Select a model" : "No local models detected";
    select.append(placeholder);
    detectedModels.forEach((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.textContent = model.size ? `${model.name} - ${model.size}` : model.name;
      select.append(option);
    });
    if (detectedModels.some((model) => model.id === current)) select.value = current;
    const availableProviders = body.providers.filter((provider) => provider.available);
    $("#connection-status").textContent = detectedModels.length
      ? `${detectedModels.length} local model${detectedModels.length === 1 ? "" : "s"} found`
      : availableProviders.length ? "No local models detected" : "No local providers running";
    renderProviders(body.providers);
  } catch (error) {
    $("#connection-status").textContent = "Local provider scan failed";
    $("#connection-status").previousElementSibling.style.background = "#e99595";
  }
}

function renderProviders(providers) {
  const list = $("#provider-list");
  if (!list) return;
  list.replaceChildren();
  providers.forEach((provider) => {
    const card = document.createElement("div");
    card.className = "provider-card";
    const heading = document.createElement("div");
    heading.className = "provider-heading";
    heading.innerHTML = "<strong></strong><span></span>";
    heading.querySelector("strong").textContent = provider.name;
    heading.querySelector("span").textContent = provider.available
      ? `${(provider.models || []).length} detected`
      : "Not running";
    card.append(heading);
    const detail = document.createElement("p");
    detail.textContent = provider.available
      ? "These models are available to select."
      : provider.error || "Start this provider, then scan again.";
    card.append(detail);
    const link = document.createElement("a");
    link.href = provider.library;
    link.target = "_blank";
    link.rel = "noreferrer";
    link.textContent = `Open ${provider.name} model library \u2197`;
    card.append(link);
    if (provider.id === "ollama" && provider.available) {
      addOllamaPullControls(card);
    }
    list.append(card);
  });
}

function addOllamaPullControls(card) {
  const controls = document.createElement("div");
  controls.className = "model-pull-controls";
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "Model name, for example llama3.2";
  input.setAttribute("aria-label", "Ollama model name to download");
  const download = document.createElement("button");
  download.type = "button";
  download.textContent = "Download model";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.textContent = "Cancel";
  cancel.hidden = true;
  const status = document.createElement("span");
  status.className = "pull-progress";
  status.setAttribute("role", "status");
  controls.append(input, download, cancel, status);
  card.append(controls);

  let jobId = null;
  cancel.addEventListener("click", async () => {
    if (!jobId) return;
    cancel.disabled = true;
    try {
      const result = await request(`/api/models/pull/${jobId}`, { method: "DELETE" });
      status.textContent = result.message || "Cancelling download...";
    } catch (error) { status.textContent = error.message; }
  });
  download.addEventListener("click", async () => {
    const name = input.value.trim();
    if (!name) { status.textContent = "Enter an Ollama model name."; return; }
    download.disabled = true;
    input.disabled = true;
    cancel.hidden = false;
    cancel.disabled = false;
    try {
      const started = await request("/api/models/pull", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      jobId = started.job_id;
      let finished = false;
      while (!finished) {
        status.textContent = "Downloading model...";
        await new Promise((resolve) => setTimeout(resolve, 900));
        const result = await request(`/api/models/pull/${jobId}`);
        const progress = result.total > 0
          ? ` (${Math.floor((result.completed / result.total) * 100)}%)`
          : "";
        status.textContent = `${result.message || result.status}${progress}`;
        finished = ["complete", "error", "cancelled"].includes(result.status);
      }
      if ((await request(`/api/models/pull/${jobId}`)).status === "complete") {
        await refreshModels();
      }
    } catch (error) {
      status.textContent = error.message;
    } finally {
      jobId = null;
      download.disabled = false;
      input.disabled = false;
      cancel.hidden = true;
      cancel.disabled = false;
    }
  });
}

async function showProviderPanel() {
  $("#model-panel").hidden = false;
  $("#scan-status").textContent = "Scanning local model providers...";
  $("#provider-list").innerHTML = "<p>Checking local providers...</p>";
  try {
    await refreshModels();
    $("#scan-status").textContent = "Scan complete. Refresh after installing a model.";
  } catch (error) {
    $("#scan-status").textContent = error instanceof Error ? error.message : String(error);
  }
}

function addMessage(role, text, sources = []) {
  conversation.querySelector(".welcome")?.remove();
  const row = document.createElement("div");
  row.className = `message-row ${role}`;
  row.innerHTML = `<div class="message-label">${role === "user" ? "YOU" : "LOCALRAG"}</div><div class="bubble"></div>`;
  const bubble = row.querySelector(".bubble");
  if (text === null) {
    bubble.classList.add("thinking");
    bubble.innerHTML = '<span>.</span><span>.</span><span>.</span>';
  } else {
    bubble.textContent = text;
  }
  addSources(row, sources);
  conversation.append(row);
  conversation.scrollTop = conversation.scrollHeight;
  return row;
}

function addSources(row, sources) {
  if (!sources.length) return null;
  const details = document.createElement("details");
  details.className = "sources";
  const summary = document.createElement("summary");
  summary.textContent = `${sources.length} source${sources.length === 1 ? "" : "s"}`;
  details.append(summary);
  sources.forEach((source, index) => {
    const sourceText = document.createElement("p");
    const matchScore = Math.round((Number(source.score) || 0) * 100);
    sourceText.textContent = `[${index + 1}] ${source.document} - chunk ${source.chunk} - match score ${matchScore}%\n${source.content}`;
    details.append(sourceText);
  });
  row.append(details);
  return details;
}

function renderCitedAnswer(bubble, sourcesDetails, answer, sources) {
  bubble.replaceChildren();
  const citation = /\[(\d+)\]/g;
  let cursor = 0;
  for (const match of answer.matchAll(citation)) {
    const sourceIndex = Number(match[1]) - 1;
    if (sourceIndex < 0 || sourceIndex >= sources.length) continue;
    bubble.append(document.createTextNode(answer.slice(cursor, match.index)));
    const link = document.createElement("button");
    link.type = "button";
    link.className = "citation-link";
    link.textContent = match[0];
    link.setAttribute("aria-label", `Show source ${match[1]}`);
    link.addEventListener("click", () => {
      if (!sourcesDetails) return;
      sourcesDetails.open = true;
      sourcesDetails.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    bubble.append(link);
    cursor = match.index + match[0].length;
  }
  bubble.append(document.createTextNode(answer.slice(cursor)));
}

async function uploadFiles(files, input) {

  if (!files.length) return;
  const form = new FormData();
  [...files].forEach((file) => form.append("uploads", file, file.name));
  message.textContent = "Reading and indexing documents...";
  try {
    const result = await request("/api/documents", { method: "POST", body: form });
    await refreshState();
    message.textContent = `Indexed ${result.documents_added} file${result.documents_added === 1 ? "" : "s"} (${result.chunks_added} chunks).`;
  } catch (error) { showError(error); }
  input.value = "";
}

$("#file-input").addEventListener("change", (event) => uploadFiles(event.target.files, event.target));
$("#folder-input").addEventListener("change", (event) => uploadFiles(event.target.files, event.target));
$("#new-chat").addEventListener("click", createChat);
$("#export-chat").addEventListener("click", () => {
  const chat = activeChat();
  if (!chat) return;
  const blob = new Blob([JSON.stringify(chat, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${(chat.title || "localrag-chat").replace(/[^a-z0-9-_]+/gi, "-")}.json`;
  link.click();
  URL.revokeObjectURL(url);
});

$("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question) return;
  const model = $("#model-select").value.trim();
  if (!model) { showError("Scan for a model and select one first."); return; }
  if (!activeChat()) createChat();
  const chat = activeChat();
  const chatId = chat.id;
  const history = chat.messages.slice(-10).map((entry) => ({
    role: entry.role,
    content: entry.content.slice(-2000),
  }));
  addMessage("user", question);
  recordChatMessage(chatId, "user", question);
  $("#question").value = "";
  $("#question").style.height = "auto";
  $("#send-button").disabled = true;
  setChatControlsDisabled(true);
  message.textContent = "Searching your documents and asking your local model...";
  const answerRow = addMessage("assistant", "");
  const answerBubble = answerRow.querySelector(".bubble");
  let sources = [];
  let answer = "";
  try {
    await streamRequest("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, model, history }),
    }, (event) => {
      if (event.type === "sources") sources = event.sources || [];
      else if (event.type === "token") {
        answer += event.text;
        answerBubble.textContent = answer;
      }
      else if (event.type === "error") throw new Error(event.message);
    });
    const sourcesDetails = addSources(answerRow, sources);
    renderCitedAnswer(answerBubble, sourcesDetails, answer, sources);
    recordChatMessage(chatId, "assistant", answer, sources);
    message.textContent = "";
  } catch (error) {
    answerRow.remove();
    showError(error);
  }
  $("#send-button").disabled = false;
  setChatControlsDisabled(false);
});


$("#clear-button").addEventListener("click", async () => {
  if (!confirm("Remove all indexed documents?")) return;
  try {
    await request("/api/clear", { method: "POST" });
    await refreshState();
    conversation.innerHTML = '<div id="welcome" class="welcome"><div class="welcome-mark">\u2726</div><h2>Index cleared.</h2><p>Upload documents to start a new chat.</p></div>';
  } catch (error) { showError(error); }
});

$("#refresh-models").addEventListener("click", refreshModels);
$("#close-model-panel").addEventListener("click", () => { $("#model-panel").hidden = true; });
$("#diagnostics-button").addEventListener("click", async () => {
  const output = $("#diagnostics-output");
  output.textContent = "Checking local setup...";
  try {
    const health = await request("/api/health");
    const free = health.disk_free_bytes == null
      ? "unavailable"
      : `${(health.disk_free_bytes / (1024 ** 3)).toFixed(1)} GB`;
    const lines = [
      `Search backend: ${health.backend}`,
      `Data folder: ${health.data_directory}`,
      `Indexed documents: ${health.document_count ?? "unavailable"}`,
      `Free disk space: ${free}`,
    ];
    if (health.index_error) lines.push(`Index issue: ${health.index_error}`);
    if (health.embedding_ready !== null) {
      lines.push(`Chroma embedding model ready: ${health.embedding_ready ? "yes" : "no"}`);
    }
    (health.providers || []).forEach((provider) => {
      lines.push(`${provider.name}: ${provider.available ? "available" : provider.error || "offline"}`);
    });
    output.textContent = lines.join("\n");
  } catch (error) {
    output.textContent = error instanceof Error ? error.message : String(error);
  }
});

$("#question").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      $("#chat-form").requestSubmit();
    }
  }
});

$("#question").addEventListener("input", (event) => {
  event.target.style.height = "auto";
  event.target.style.height = `${Math.min(event.target.scrollHeight, 130)}px`;
});

if (!activeChat()) activeChatId = chats[0]?.id || null;
if (!chats.length) createChat();
else {
  saveChats();
  renderChatList();
  renderConversation();
}
Promise.all([refreshState(), refreshModels()]).catch(showError);
