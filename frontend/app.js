const state = {
  conversationId: null,
  busy: false,
  messages: [],
  quotes: [],
};

const elements = {
  connection: document.querySelector("#connection-status"),
  form: document.querySelector("#message-form"),
  input: document.querySelector("#message-input"),
  list: document.querySelector("#message-list"),
  quotes: document.querySelector("#quote-list"),
  send: document.querySelector("#send-button"),
  talk: document.querySelector("#talk-button"),
  template: document.querySelector("#message-template"),
};

function qrCodeFromPath() {
  const match = window.location.pathname.match(/^\/q\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || "Bağlantı kurulamadı.");
  }
  return response.json();
}

async function initializeConversation() {
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
    state.quotes = await api(
      `/api/v1/conversations/${encodeURIComponent(state.conversationId)}/quotes`,
    );
    renderMessages();
    renderQuotes();
    setConnection("online", "Hazır");
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
      elements.list.lastElementChild?.scrollIntoView({ block: "end" });
      window.scrollBy({ top: 104, behavior: "smooth" });
    });
  }
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
  if (!state.quotes.length) {
    const empty = document.createElement("div");
    empty.className = "empty-quote";
    empty.innerHTML = '<span class="empty-index">01</span><p>İş türü, konum ve metraj konuşma sırasında burada netleşir.</p>';
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
      ? "Cephe sistemini seçin"
      : quote.total_price
        ? formatMoney(quote.total_price)
        : quote.unit_price
          ? formatMoney(quote.unit_price)
          : "Fiyat netleştirilecek";
    const unit = document.createElement("div");
    unit.className = "quote-unit";
    unit.textContent = quote.quantity
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
  state.messages.push({ role: "USER", content: cleanText, pending: true });
  renderMessages();
  try {
    const response = await api(
      `/api/v1/conversations/${encodeURIComponent(state.conversationId)}/messages`,
      { method: "POST", body: JSON.stringify({ text: cleanText }) },
    );
    state.messages[state.messages.length - 1].pending = false;
    state.messages.push({
      id: response.assistant_message_id,
      role: "ASSISTANT",
      content: response.assistant_text,
    });
    state.quotes = response.quotes || [];
    renderMessages();
    renderQuotes();
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

elements.talk.addEventListener("click", () => elements.input.focus());

document.querySelectorAll("[data-prompt]").forEach((button) => {
  button.addEventListener("click", () => {
    elements.input.value = button.dataset.prompt;
    elements.form.requestSubmit();
  });
});

void initializeConversation();
