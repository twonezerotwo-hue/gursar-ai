const state = {
  conversationId: null,
  busy: false,
  messages: [],
  quotes: [],
  ledger: null,
  projectId: null,
  uploadedDocuments: [],
  quickReplies: [],
  uploading: false,
  documentCapabilities: {
    accepted_extensions: [".pdf", ".png", ".jpg", ".jpeg", ".dxf", ".ifc"],
    accepted_media_types: ["application/pdf", "image/png", "image/jpeg"],
    max_upload_bytes: 50 * 1024 * 1024,
    max_pdf_pages: 500,
    encrypted_pdf_supported: false,
  },
};

const elements = {
  connection: document.querySelector("#connection-status"),
  form: document.querySelector("#message-form"),
  input: document.querySelector("#message-input"),
  list: document.querySelector("#message-list"),
  conversationQuickReplies: document.querySelector("#conversation-quick-replies"),
  quotes: document.querySelector("#quote-list"),
  summaryQuantity: document.querySelector("#summary-quantity"),
  summaryTotal: document.querySelector("#summary-total"),
  ledgerBreakdown: document.querySelector("#ledger-breakdown"),
  ledgerGrandTotal: document.querySelector("#ledger-grand-total"),
  assistantCard: document.querySelector("#assistant-card"),
  send: document.querySelector("#send-button"),
  reset: document.querySelector("#reset-button"),
  report: document.querySelector("#report-button"),
  fileInput: document.querySelector("#project-file-input"),
  fileTrigger: document.querySelector("#file-trigger"),
  fileSupport: document.querySelector("#file-support"),
  uploadedFiles: document.querySelector("#uploaded-files"),
  template: document.querySelector("#message-template"),
  portfolioLaunch: document.querySelector("#portfolio-launch"),
  portfolioModal: document.querySelector("#portfolio-modal"),
  portfolioClose: document.querySelector("#portfolio-close"),
};

let portfolioCloseTimer = null;
let portfolioLastFocus = null;

function qrCodeFromPath() {
  const match = window.location.pathname.match(/^\/q\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData) && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    ...options,
    headers,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : detail?.message;
    throw new Error(message || "Bağlantı kurulamadı.");
  }
  return response.json();
}

async function initializeConversation() {
  state.conversationId = null;
  state.messages = [];
  state.quotes = [];
  state.ledger = null;
  state.projectId = null;
  state.uploadedDocuments = [];
  state.quickReplies = [];
  state.uploading = false;
  elements.list.innerHTML = '<div class="loading-message"><span></span> Görüşme hazırlanıyor…</div>';
  elements.assistantCard.classList.remove("has-conversation");
  elements.send.disabled = true;
  elements.fileTrigger.disabled = true;
  renderUploadedFiles();
  renderFileSupport();
  setConnection("loading", "Bağlanıyor");
  try {
    const code = qrCodeFromPath();
    const payload = code
      ? await api(`/api/v1/qr/${encodeURIComponent(code)}/conversation`, { method: "POST" })
      : await api("/api/v1/conversations", {
          method: "POST",
          body: JSON.stringify({ locale: "tr" }),
        });
    state.conversationId = payload.conversation_id;
    state.messages = payload.messages || [];
    state.ledger = await api(
      `/api/v1/conversations/${encodeURIComponent(state.conversationId)}/ledger`,
    );
    state.quotes = state.ledger.items;
    state.documentCapabilities = await api("/api/v1/documents/capabilities").catch(
      () => state.documentCapabilities,
    );
    const documentTools = await api("/api/v1/document-tools").catch(() => null);
    if (documentTools) {
      state.documentCapabilities.accepted_extensions = documentTools.accepted_extensions;
      state.documentCapabilities.max_upload_bytes = documentTools.max_upload_bytes;
    }
    renderMessages();
    renderConversationQuickReplies();
    renderQuotes();
    renderFileSupport();
    setConnection("online", "Sistem Hazır");
    elements.send.disabled = false;
    elements.fileTrigger.disabled = false;
    elements.input.focus({ preventScroll: true });
  } catch (error) {
    setConnection("error", "Bağlantı sorunu");
    renderSystemError(error.message);
  }
}

function setConnection(mode, text) {
  elements.connection.className = `connection is-${mode}`;
  elements.connection.querySelector("span:last-child").textContent = text;
}

function appendRichText(container, text) {
  const segments = text.split(/(\*\*[^*]+\*\*)/g);
  for (const segment of segments) {
    if (!segment) continue;
    if (segment.startsWith("**") && segment.endsWith("**")) {
      const strong = document.createElement("strong");
      strong.textContent = segment.slice(2, -2);
      container.append(strong);
    } else {
      container.append(document.createTextNode(segment));
    }
  }
}

function renderMessages() {
  elements.assistantCard.classList.toggle("has-conversation", state.messages.length > 1);
  elements.list.replaceChildren();
  for (const message of state.messages) {
    const fragment = elements.template.content.cloneNode(true);
    const article = fragment.querySelector(".message");
    const meta = fragment.querySelector(".message-meta");
    const body = fragment.querySelector(".message-body");
    const isUser = message.role === "USER";
    article.classList.toggle("is-user", isUser);
    article.classList.toggle("is-pending", Boolean(message.pending));
    meta.textContent = isUser ? "Siz" : "GÜRSAR AI";
    appendRichText(body, message.content);
    elements.list.append(fragment);
  }
  if (state.messages.length > 1) {
    requestAnimationFrame(() => {
      elements.list.lastElementChild?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  }
}

function renderConversationQuickReplies() {
  elements.conversationQuickReplies.replaceChildren();
  for (const reply of state.quickReplies) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = reply.label;
    button.addEventListener("click", () => void sendMessage(reply.value));
    elements.conversationQuickReplies.append(button);
  }
  elements.conversationQuickReplies.hidden = !state.quickReplies.length;
}

function renderSystemError(message) {
  state.messages.push({ role: "ASSISTANT", content: message });
  renderMessages();
}

function formatMoney(value) {
  return new Intl.NumberFormat("tr-TR", {
    style: "currency",
    currency: "TRY",
    maximumFractionDigits: Number(value) % 1 === 0 ? 0 : 2,
  }).format(Number(value));
}

function formatQuantity(value, unit) {
  const label = unit === "m2" ? "m²" : unit === "m3" ? "m³" : unit;
  return `${new Intl.NumberFormat("tr-TR").format(Number(value))} ${label}`;
}

function renderQuotes() {
  elements.quotes.replaceChildren();
  const quantityTotals = state.ledger?.quantity_totals || [];
  const grandTotal = state.ledger?.grand_total || 0;

  elements.summaryQuantity.textContent = quantityTotals.length
    ? quantityTotals.map((total) => formatQuantity(total.quantity, total.unit)).join(" · ")
    : "—";
  elements.summaryTotal.textContent = Number(grandTotal) ? formatMoney(grandTotal) : "—";
  elements.ledgerGrandTotal.textContent = Number(grandTotal) ? formatMoney(grandTotal) : "—";
  elements.report.disabled = !state.ledger?.items?.length;
  elements.ledgerBreakdown.replaceChildren();
  const ledgerItems = state.ledger?.items || [];
  if (!ledgerItems.length) {
    const empty = document.createElement("p");
    empty.textContent = "Henüz hesaplanmış kalem yok.";
    elements.ledgerBreakdown.append(empty);
  } else {
    for (const quote of ledgerItems) {
      const row = document.createElement("div");
      const label = document.createElement("span");
      const amount = document.createElement("strong");
      label.textContent = quote.service_label;
      amount.textContent = quote.total_price
        ? formatMoney(quote.total_price)
        : `${formatMoney(quote.unit_price)} / ${quote.unit === "m2" ? "m²" : quote.unit}`;
      row.append(label, amount);
      elements.ledgerBreakdown.append(row);
    }
  }

  if (!state.quotes.length) {
    const empty = document.createElement("div");
    empty.className = "empty-quote";
    empty.innerHTML = '<span aria-hidden="true">＋</span><p><strong>Henüz kalem eklenmedi.</strong>Sohbet üzerinden projenizi tarif edin, kalemler otomatik olarak burada görünsün.</p>';
    elements.quotes.append(empty);
    return;
  }

  state.quotes.forEach((quote, index) => {
    const card = document.createElement("article");
    const needsClarification = quote.status === "SERVICE_CLARIFICATION_REQUIRED";
    card.className = `quote-card${
      quote.status === "PRICE_NOT_CONFIGURED"
        ? " is-missing"
        : needsClarification
          ? " is-clarification"
          : ""
    }`;

    const kicker = document.createElement("div");
    kicker.className = "quote-card-kicker";
    kicker.textContent = `İş ${String(index + 1).padStart(2, "0")}`;

    const title = document.createElement("h3");
    title.textContent = quote.service_label;
    const location = document.createElement("p");
    location.className = "quote-location";
    location.textContent = quote.location_label;
    const price = document.createElement("div");
    price.className = "quote-price";
    price.textContent = needsClarification
      ? quote.service_code === "rough_construction.turnkey"
        ? "Kapsamı seçin"
        : "Sistemi seçin"
      : quote.total_price
        ? formatMoney(quote.total_price)
        : quote.unit_price
          ? formatMoney(quote.unit_price)
          : "Fiyat netleştirilecek";
    const unit = document.createElement("div");
    unit.className = "quote-unit";
    unit.textContent = needsClarification
      ? quote.service_code === "rough_construction.turnkey"
        ? "Malzeme kapsamı bekleniyor"
        : "Talep kaydedildi"
      : quote.quantity
      ? `${formatQuantity(quote.quantity, quote.unit)} · KDV hariç`
      : quote.unit_price
        ? `Birim fiyat / ${quote.unit === "m2" ? "m²" : quote.unit} · KDV hariç`
        : "Talep kaydedildi";
    const scope = document.createElement("div");
    scope.className = "quote-scope";
    scope.textContent =
      quote.clarification_question ||
      quote.scope_summary ||
      "Kapsam görüşme sırasında netleşir.";

    card.append(kicker, title, location, price, unit, scope);
    elements.quotes.append(card);
  });
}

async function sendMessage(text) {
  const cleanText = text.trim();
  if (!cleanText || state.busy || !state.conversationId) return;
  state.busy = true;
  elements.send.disabled = true;
  elements.input.disabled = true;
  setConnection("loading", "AI düşünüyor");
  state.messages.push({ role: "USER", content: cleanText, pending: true });
  const requestId = crypto.randomUUID();
  renderMessages();
  try {
    const response = await api(
      `/api/v1/conversations/${encodeURIComponent(state.conversationId)}/messages`,
      {
        method: "POST",
        headers: { "Idempotency-Key": requestId },
        body: JSON.stringify({ text: cleanText }),
      },
    );
    state.messages[state.messages.length - 1].pending = false;
    state.messages.push({
      id: response.assistant_message_id,
      role: "ASSISTANT",
      content: response.assistant_text,
    });
    state.quickReplies = response.quick_replies || [];
    state.ledger = await api(
      `/api/v1/conversations/${encodeURIComponent(state.conversationId)}/ledger`,
    );
    const savedQuotes = state.ledger.items;
    const transientQuotes = (response.quotes || []).filter(
      (quote) => !quote.quote_id && !savedQuotes.some((saved) => saved.job_id === quote.job_id),
    );
    state.quotes = [...savedQuotes, ...transientQuotes];
    renderMessages();
    renderConversationQuickReplies();
    renderQuotes();
    setConnection("online", "Sistem Hazır");
  } catch (error) {
    state.messages[state.messages.length - 1].pending = false;
    renderSystemError("Şu anda yanıtı hazırlayamadım. Lütfen tekrar deneyin.");
    setConnection("error", error.message);
  } finally {
    state.busy = false;
    elements.send.disabled = false;
    elements.input.disabled = false;
    elements.input.focus();
  }
}

function formatFileSize(bytes) {
  const megabytes = Number(bytes) / (1024 * 1024);
  return `${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(megabytes)} MB`;
}

function renderFileSupport(message = "", mode = "") {
  elements.fileSupport.className = `file-support${mode ? ` is-${mode}` : ""}`;
  if (message) {
    elements.fileSupport.textContent = message;
    return;
  }
  const capabilities = state.documentCapabilities;
  const formats = capabilities.accepted_extensions
    .map((extension) => extension.slice(1).toUpperCase())
    .join(", ");
  const encryption = capabilities.encrypted_pdf_supported ? "" : " · Şifresiz";
  elements.fileSupport.textContent = `${formats}${encryption} · En fazla ${formatFileSize(capabilities.max_upload_bytes)} · ${capabilities.max_pdf_pages} sayfa`;
  elements.fileInput.accept = [
    ...capabilities.accepted_extensions,
    ...capabilities.accepted_media_types,
  ].join(",");
}

function renderUploadedFiles() {
  elements.uploadedFiles.replaceChildren();
  for (const uploadedDocument of state.uploadedDocuments) {
    const item = document.createElement("span");
    item.className = "uploaded-file";
    const name = document.createElement("span");
    const pages = document.createElement("small");
    name.textContent = uploadedDocument.original_name;
    pages.textContent = uploadedDocument.analysis
      ? `${uploadedDocument.analysis.summary} · ${uploadedDocument.analysis.tools_used.join(" + ")}`
      : `${uploadedDocument.latest_version.page_count} sayfa`;
    item.append(name, pages);
    elements.uploadedFiles.append(item);
  }
}

async function ensureUploadProject() {
  if (state.projectId) return state.projectId;
  const project = await api("/api/v1/projects", {
    method: "POST",
    body: JSON.stringify({
      name: `GÜRSAR AI Görüşmesi ${state.conversationId.slice(0, 8)}`,
      locale: "tr",
    }),
  });
  state.projectId = project.id;
  return project.id;
}

function validateProjectFile(file) {
  const capabilities = state.documentCapabilities;
  const lowerName = file.name.toLocaleLowerCase("tr-TR");
  if (!capabilities.accepted_extensions.some((extension) => lowerName.endsWith(extension))) {
    throw new Error(`Bu dosya türü desteklenmiyor: ${capabilities.accepted_extensions.join(", ")}`);
  }
  if (!file.size) throw new Error("Boş dosya yüklenemez.");
  if (file.size > capabilities.max_upload_bytes) {
    throw new Error(`Dosya ${formatFileSize(capabilities.max_upload_bytes)} sınırını aşıyor.`);
  }
}

async function uploadProjectFiles(files) {
  if (!files.length || state.uploading || !state.conversationId) return;
  state.uploading = true;
  elements.fileTrigger.disabled = true;
  setConnection("loading", "Dosya yükleniyor");
  try {
    for (const file of files) {
      validateProjectFile(file);
      renderFileSupport(`${file.name} yükleniyor…`);
      const form = new FormData();
      form.append("file", file, file.name);
      form.append("conversation_id", state.conversationId);
      const analysis = await api("/api/v1/document-tools/analyze", {
        method: "POST",
        body: form,
      });
      let uploaded = {
        original_name: file.name,
        latest_version: { page_count: analysis.metrics.page_count || 1 },
      };
      if (file.name.toLocaleLowerCase("tr-TR").endsWith(".pdf")) {
        const projectId = await ensureUploadProject();
        const storageForm = new FormData();
        storageForm.append("file", file, file.name);
        uploaded = await api(
          `/api/v1/projects/${encodeURIComponent(projectId)}/documents`,
          { method: "POST", body: storageForm },
        );
      }
      uploaded.analysis = analysis;
      state.uploadedDocuments.push(uploaded);
      renderUploadedFiles();
      renderFileSupport(
        `${uploaded.original_name} analiz edildi · ${analysis.summary}`,
        "success",
      );
    }
    setConnection("online", "Sistem Hazır");
  } catch (error) {
    renderFileSupport(error.message, "error");
    setConnection("error", "Dosya yüklenemedi");
  } finally {
    state.uploading = false;
    elements.fileTrigger.disabled = false;
    elements.fileInput.value = "";
  }
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function downloadReport() {
  const items = state.ledger?.items || [];
  if (!items.length) return;

  const rows = [
    ["İş Kalemi", "Konum", "Metraj", "Birim", "Birim Fiyat", "Toplam", "KDV"],
    ...items.map((item) => [
      item.service_label,
      item.location_label,
      item.quantity ?? "",
      item.unit,
      item.unit_price ?? "",
      item.total_price ?? "",
      "Hariç",
    ]),
    [],
    ["Genel Toplam", "", "", "", "", state.ledger.grand_total || 0, "KDV hariç"],
  ];
  const csv = `\uFEFF${rows.map((row) => row.map(csvCell).join(";")).join("\n")}`;
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = `GURSAR_AI_Proje_Ozeti_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function openPortfolio({ automatic = false } = {}) {
  if (!elements.portfolioModal.hidden) return;
  if (portfolioCloseTimer) window.clearTimeout(portfolioCloseTimer);
  portfolioLastFocus = automatic ? null : document.activeElement;
  elements.portfolioModal.hidden = false;
  elements.portfolioLaunch.setAttribute("aria-expanded", "true");
  document.body.classList.add("portal-open");
  requestAnimationFrame(() => {
    elements.portfolioModal.classList.add("is-open");
    window.setTimeout(() => elements.portfolioClose.focus({ preventScroll: true }), 420);
  });
  try {
    sessionStorage.setItem("gursar-portfolio-seen", "true");
  } catch {
    // Portal remains fully usable when browser storage is unavailable.
  }
}

function closePortfolio() {
  if (elements.portfolioModal.hidden) return;
  elements.portfolioModal.classList.remove("is-open");
  elements.portfolioLaunch.setAttribute("aria-expanded", "false");
  document.body.classList.remove("portal-open");
  portfolioCloseTimer = window.setTimeout(() => {
    elements.portfolioModal.hidden = true;
    if (portfolioLastFocus instanceof HTMLElement) {
      portfolioLastFocus.focus({ preventScroll: true });
    }
  }, 480);
}

function schedulePortfolioReveal() {
  let alreadySeen = false;
  try {
    alreadySeen = sessionStorage.getItem("gursar-portfolio-seen") === "true";
  } catch {
    alreadySeen = false;
  }
  if (!alreadySeen) {
    window.setTimeout(() => openPortfolio({ automatic: true }), 5600);
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const text = elements.input.value;
  elements.input.value = "";
  elements.input.style.height = "auto";
  void sendMessage(text);
});

elements.input.addEventListener("input", () => {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 132)}px`;
});

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

elements.reset.addEventListener("click", () => {
  if (state.busy || state.uploading) return;
  elements.input.value = "";
  void initializeConversation();
});

elements.report.addEventListener("click", downloadReport);
elements.fileTrigger.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", () => {
  void uploadProjectFiles([...elements.fileInput.files]);
});
elements.portfolioLaunch.addEventListener("click", () => openPortfolio());
elements.portfolioClose.addEventListener("click", closePortfolio);
elements.portfolioModal.querySelectorAll("[data-portfolio-close]").forEach((button) => {
  button.addEventListener("click", closePortfolio);
});
elements.portfolioModal.querySelectorAll("[data-portfolio-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.input.value = button.dataset.portfolioPrompt;
    closePortfolio();
    window.setTimeout(() => {
      elements.assistantCard.scrollIntoView({ behavior: "smooth", block: "center" });
      elements.input.focus({ preventScroll: true });
    }, 520);
  });
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !elements.portfolioModal.hidden) closePortfolio();
});

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.input.value = button.dataset.prompt;
    elements.form.requestSubmit();
  });
});

void initializeConversation();
schedulePortfolioReveal();
