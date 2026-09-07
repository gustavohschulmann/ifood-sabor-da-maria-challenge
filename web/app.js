(() => {
  "use strict";

  const chatLog = document.getElementById("chat-log");
  const welcome = document.getElementById("welcome");
  const composer = document.getElementById("composer");
  const messageInput = document.getElementById("message");
  const sendBtn = document.getElementById("send-btn");
  const resetBtn = document.getElementById("reset-btn");
  const budgetValue = document.getElementById("budget-value");
  const menuBody = document.getElementById("menu-body");
  const candidates = document.getElementById("candidates");
  const banner = document.getElementById("banner");
  const turnStatus = document.getElementById("turn-status");

  const SESSION_KEY = "sabor-da-maria.hermes-session";

  const state = {
    sessionId: localStorage.getItem(SESSION_KEY) || null,
    phase: "idle",
    controller: null,
    generation: 0,
    workspaceRequest: 0,
  };

  function money(value) {
    const raw = String(value ?? "0");
    const num = Number(raw.replace(",", "."));

    if (Number.isNaN(num)) {
      return `R$ ${raw}`;
    }

    return num.toLocaleString("pt-BR", {
      style: "currency",
      currency: "BRL",
    });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function normalizedText(value) {
    return String(value ?? "")
      .replace(/\r\n/g, "\n")
      .trim();
  }

  function showBanner(text, isError = false) {
    if (!text) {
      banner.classList.add("hidden");
      banner.textContent = "";
      return;
    }

    banner.textContent = text;
    banner.classList.toggle("error", Boolean(isError));
    banner.classList.remove("hidden");
  }

  function setSessionId(sessionId) {
    state.sessionId = sessionId || null;

    if (state.sessionId) {
      localStorage.setItem(SESSION_KEY, state.sessionId);
    } else {
      localStorage.removeItem(SESSION_KEY);
    }
  }

  function setComposerStatus(phase, label) {
    if (!turnStatus) return;

    if (!label) {
      turnStatus.hidden = true;
      turnStatus.textContent = "";
      turnStatus.dataset.phase = "";
      return;
    }

    turnStatus.hidden = false;
    turnStatus.dataset.phase = phase || "";
    turnStatus.textContent = label;
  }

  function setPhase(next, label = "") {
    state.phase = next;

    const busy =
      next === "streaming" ||
      next === "resetting";

    sendBtn.disabled = busy;
    resetBtn.disabled = busy;
    messageInput.disabled = busy;

    if (busy) {
      setComposerStatus(
        "thinking",
        label || "Pensando…"
      );
      return;
    }

    if (next === "failed") {
      setComposerStatus(
        "failed",
        label || "Algo falhou"
      );
      return;
    }

    setComposerStatus(
      "waiting",
      "Pode responder"
    );
  }

  function hideWelcome() {
    if (welcome && welcome.isConnected) {
      welcome.remove();
    }
  }

  function scrollChat() {
    requestAnimationFrame(() => {
      chatLog.scrollTop = chatLog.scrollHeight;
    });
  }

  function addPlainTurn(role, text) {
    if (!text) return null;

    hideWelcome();

    const turn = document.createElement("article");
    turn.className = `turn ${role}`;

    const who = document.createElement("div");
    who.className = "who";
    who.textContent =
      role === "user"
        ? "Dona Maria"
        : "Consultora";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text;

    turn.append(who, bubble);
    chatLog.append(turn);

    scrollChat();

    return turn;
  }

  function createAgentTurn() {
    hideWelcome();

    const thread =
      document.createElement("div");

    thread.className = "agent-thread";

    const who =
      document.createElement("div");

    who.className = "who";
    who.textContent = "Consultora";

    const timeline =
      document.createElement("div");

    timeline.className =
      "agent-timeline";

    const statusEl =
      document.createElement("p");

    statusEl.className =
      "agent-status";

    statusEl.dataset.phase =
      "thinking";

    statusEl.setAttribute(
      "role",
      "status"
    );

    statusEl.setAttribute(
      "aria-live",
      "polite"
    );

    statusEl.textContent =
      "Pensando…";

    thread.append(
      who,
      timeline,
      statusEl
    );

    chatLog.append(thread);

    const bubbles = new Map();
    const tools = new Map();
    let sawTool = false;
    let assistantCountAtLastTool = 0;
    let responseAfterLastTool = false;

    function ensureBubble(segment = 0) {
      const key =
        Number.isInteger(Number(segment))
          ? Number(segment)
          : 0;

      if (bubbles.has(key)) {
        return bubbles.get(key);
      }

      const turn =
        document.createElement("article");

      turn.className =
        "turn agent";

      turn.dataset.segment =
        String(key);

      const bubble =
        document.createElement("div");

      bubble.className =
        "bubble";

      turn.append(bubble);

      bubbles.set(
        key,
        bubble
      );

      timeline.append(turn);

      return bubble;
    }

    function setTool(event) {
      const id = String(
        event.id ||
          `${event.name || "tool"}:${tools.size}`
      );

      let item =
        tools.get(id);
      const isNew = !item;

      if (!item) {
        item =
          document.createElement("div");

        item.className =
          "step";

        item.dataset.id =
          id;

        item.innerHTML =
          '<span class="step-status"></span>' +
          '<div>' +
          '<strong></strong>' +
          '<span class="summary"></span>' +
          "</div>";

        tools.set(
          id,
          item
        );

        timeline.append(item);
      }

      const status =
        event.status === "started"
          ? "running"
          : event.status || "running";

      item.classList.remove(
        "running",
        "completed",
        "failed",
        "started"
      );

      item.classList.add(
        status
      );

      item.querySelector(
        "strong"
      ).textContent =
        event.label ||
        event.name ||
        "Ação";

      const summary =
        item.querySelector(
          ".summary"
        );

      if (event.summary) {
        summary.textContent =
          event.summary;
      }

      summary.hidden =
        !summary.textContent;

      if (
        isNew &&
        status === "running"
      ) {
        sawTool = true;
        assistantCountAtLastTool =
          bubbles.size;
        responseAfterLastTool =
          false;
      }
    }

    return {
      setStatus(phase, label) {
        if (phase === "waiting") {
          statusEl.hidden = true;
          return;
        }

        statusEl.hidden = false;

        statusEl.dataset.phase =
          phase || "thinking";

        statusEl.textContent =
          label || "Pensando…";

        setComposerStatus(
          phase || "thinking",
          label || "Pensando…"
        );

        scrollChat();
      },

      applyDelta(payload) {
        const text = String(
          payload.text ||
          payload.delta ||
          ""
        );

        if (!text) return;

        const bubble =
          ensureBubble(
            payload.segment ?? 0
          );

        /*
         * IMPORTANTE:
         *
         * O backend já normaliza os eventos
         * cumulativos do Hermes.
         *
         * Se replace=true, não tentamos
         * descobrir se o texto é delta,
         * snapshot, prefixo etc.
         *
         * Simplesmente substituímos.
         */
        if (payload.replace) {
          bubble.textContent =
            text;
        } else {
          bubble.textContent +=
            text;
        }

        const segment = Number(
          payload.segment ?? 0
        );

        if (
          sawTool &&
          segment >=
            assistantCountAtLastTool
        ) {
          responseAfterLastTool =
            true;
        }

        scrollChat();
      },

      applyTool(event) {
        setTool(event);
        scrollChat();
      },

      replaceAssistantMessages(messages) {
        const clean =
          (messages || [])
            .map((text) =>
              normalizedText(text)
            )
            .filter(Boolean);

        if (!clean.length) {
          return;
        }

        clean.forEach(
          (text, index) => {
            const bubble =
              ensureBubble(index);

            bubble.textContent =
              text;
          }
        );

        if (
          sawTool &&
          clean.length >
            assistantCountAtLastTool
        ) {
          responseAfterLastTool =
            true;
        }

        scrollChat();
      },

      hasText() {
        return [...bubbles.values()]
          .some((bubble) =>
            normalizedText(
              bubble.textContent
            )
          );
      },

      hasTerminalText() {
        return (
          this.hasText() &&
          (
            !sawTool ||
            responseAfterLastTool
          )
        );
      },

      finish(ok) {
        if (ok) {
          statusEl.hidden = true;
          statusEl.textContent = "";
        } else {
          statusEl.hidden = false;
          statusEl.dataset.phase =
            "failed";

          statusEl.textContent =
            "Algo falhou";
        }

        scrollChat();
      },
    };
  }

  function candidateLabel(item) {
    if (item.interested === false) {
      return "recusada";
    }

    if (item.accepted) {
      return "aceita";
    }

    if (
      item.feasibility_status ===
      "blocked"
    ) {
      return "bloqueada";
    }

    if (item.interested === true) {
      return "em análise";
    }

    return "apresentada";
  }

  function renderWorkspace(data) {
    budgetValue.textContent =
      money(
        data.remaining_budget
      );

    const menu =
      data.accepted_menu || [];

    menuBody.replaceChildren();

    if (!menu.length) {
      const row =
        document.createElement("tr");

      row.className =
        "empty-row";

      row.innerHTML =
        '<td colspan="5">' +
        "Nenhum prato aceito ainda." +
        "</td>";

      menuBody.append(row);
    } else {
      for (const item of menu) {
        const row =
          document.createElement("tr");

        row.innerHTML = `
          <td>
            ${escapeHtml(
              item.recipe_name
            )}
          </td>
          <td>
            ${escapeHtml(
              money(
                item.cmv_per_serving
              )
            )}
          </td>
          <td>
            ${escapeHtml(
              money(item.price)
            )}
          </td>
          <td>
            ${escapeHtml(
              money(item.profit)
            )}
          </td>
          <td>
            ${escapeHtml(
              item.label || "—"
            )}
          </td>
        `;

        menuBody.append(row);
      }
    }

    const pending =
      (data.candidates || [])
        .filter(
          (item) =>
            !item.accepted
        );

    candidates.replaceChildren();

    if (!pending.length) {
      const li =
        document.createElement("li");

      li.className =
        "muted";

      li.textContent =
        "Nenhuma candidata ainda.";

      candidates.append(li);

      return;
    }

    for (const item of pending) {
      const li =
        document.createElement("li");

      li.innerHTML = `
        <span class="name">
          ${escapeHtml(
            item.recipe_name
          )}
        </span>

        <span class="meta">
          ${escapeHtml(
            candidateLabel(item)
          )}
        </span>
      `;

      candidates.append(li);
    }
  }

  async function refreshWorkspace() {
    const requestId =
      ++state.workspaceRequest;

    const response =
      await fetch(
        "/api/workspace",
        {
          cache: "no-store",
        }
      );

    if (!response.ok) {
      throw new Error(
        "Não foi possível ler o cardápio."
      );
    }

    const data =
      await response.json();

    /*
     * Se outra atualização começou
     * depois desta, esta resposta já
     * está velha.
     */
    if (
      requestId !==
      state.workspaceRequest
    ) {
      return;
    }

    renderWorkspace(data);
  }

  async function fetchTranscript() {
    if (!state.sessionId) {
      return null;
    }

    const response =
      await fetch(
        `/api/transcript?session_id=${encodeURIComponent(
          state.sessionId
        )}`,
        {
          cache: "no-store",
        }
      );

    if (!response.ok) {
      return null;
    }

    const data =
      await response.json();

    if (
      !Array.isArray(
        data.messages
      )
    ) {
      return null;
    }

    return data.messages
      .filter(
        (item) =>
          item &&
          (
            item.role === "user" ||
            item.role === "assistant"
          )
      )
      .map((item) => ({
        role: item.role,
        content: String(
          item.content ||
          item.text ||
          ""
        ),
      }))
      .filter((item) =>
        normalizedText(
          item.content
        )
      );
  }

  async function hydrateTranscript() {
    const messages =
      await fetchTranscript();

    if (!messages?.length) {
      return false;
    }

    chatLog.replaceChildren();

    for (const message of messages) {
      addPlainTurn(
        message.role,
        message.content
      );
    }

    return true;
  }

  async function reconcileTurn(
    agent,
    userText
  ) {
    const messages =
      await fetchTranscript();

    if (!messages?.length) {
      return false;
    }

    const wanted =
      normalizedText(userText);

    let userIndex = -1;

    /*
     * Procuramos DE TRÁS PRA FRENTE
     * a mensagem exata enviada neste
     * turno.
     */
    for (
      let i =
        messages.length - 1;
      i >= 0;
      i -= 1
    ) {
      if (
        messages[i].role ===
          "user" &&
        normalizedText(
          messages[i].content
        ) === wanted
      ) {
        userIndex = i;
        break;
      }
    }

    if (userIndex < 0) {
      return false;
    }

    const assistantMessages =
      messages
        .slice(userIndex + 1)
        .filter(
          (item) =>
            item.role ===
            "assistant"
        )
        .map(
          (item) =>
            item.content
        )
        .filter((text) =>
          normalizedText(text)
        );

    if (
      !assistantMessages.length
    ) {
      return false;
    }

    /*
     * Agora o transcript persistido
     * pelo Hermes vence o DOM.
     */
    agent.replaceAssistantMessages(
      assistantMessages
    );

    return true;
  }

  async function readSse(
    response,
    onEvent,
    signal
  ) {
    if (!response.body) {
      throw new Error(
        "O navegador não recebeu o fluxo da conversa."
      );
    }

    const reader =
      response.body.getReader();

    const decoder =
      new TextDecoder();

    let buffer = "";
    let eventName =
      "message";

    let dataLines = [];
    let terminal = false;

    function flush() {
      if (!dataLines.length) {
        eventName =
          "message";

        return;
      }

      const raw =
        dataLines.join("\n");

      dataLines = [];

      const name =
        eventName;

      eventName =
        "message";

      let payload;

      try {
        payload =
          JSON.parse(raw);
      } catch {
        payload = {
          text: raw,
        };
      }

      onEvent(
        name,
        payload
      );

      if (name === "done") {
        terminal = true;
      }
    }

    try {
      while (!terminal) {
        if (signal.aborted) {
          throw new DOMException(
            "Aborted",
            "AbortError"
          );
        }

        const {
          value,
          done,
        } = await reader.read();

        buffer +=
          decoder.decode(
            value ||
              new Uint8Array(),
            {
              stream: !done,
            }
          );

        const lines =
          buffer.split(
            /\r?\n/
          );

        buffer =
          lines.pop() ?? "";

        for (
          const line of lines
        ) {
          if (line === "") {
            flush();

            if (terminal) {
              break;
            }

            continue;
          }

          /*
           * Comentário SSE / keepalive
           */
          if (
            line.startsWith(":")
          ) {
            continue;
          }

          if (
            line.startsWith(
              "event:"
            )
          ) {
            eventName =
              line
                .slice(6)
                .trim();

            continue;
          }

          if (
            line.startsWith(
              "data:"
            )
          ) {
            dataLines.push(
              line
                .slice(5)
                .trimStart()
            );
          }
        }

        if (done) {
          flush();
          break;
        }
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        // ignore
      }
    }
  }

  function isAbortError(error) {
    return (
      error?.name ===
        "AbortError" ||
      error?.code ===
        "ABORT_ERR" ||
      /abort/i.test(
        String(
          error?.message || ""
        )
      )
    );
  }

  async function abortCurrentTurn() {
    if (!state.controller) {
      return;
    }

    const controller =
      state.controller;

    state.controller =
      null;

    /*
     * Invalida todos os callbacks
     * daquele stream.
     */
    state.generation += 1;

    controller.abort();
  }

  async function sendMessage(
    text,
    {
      displayUser = true,
    } = {}
  ) {
    const trimmed =
      normalizedText(text);

    if (
      !trimmed ||
      state.phase !== "idle"
    ) {
      return;
    }

    const generation =
      ++state.generation;

    const controller =
      new AbortController();

    state.controller =
      controller;

    setPhase(
      "streaming",
      "Pensando…"
    );

    showBanner("");

    if (displayUser) {
      addPlainTurn(
        "user",
        trimmed
      );
    }

    const agent =
      createAgentTurn();

    let streamError = "";
    let complete = false;

    try {
      const response =
        await fetch(
          "/api/chat",
          {
            method: "POST",

            headers: {
              "Content-Type":
                "application/json",
            },

            body:
              JSON.stringify({
                message:
                  trimmed,

                session_id:
                  state.sessionId,
              }),

            signal:
              controller.signal,
          }
        );

      if (!response.ok) {
        const detail =
          await response
            .json()
            .catch(
              () => ({})
            );

        throw new Error(
          detail.error ||
            `Falha no chat (${response.status})`
        );
      }

      await readSse(
        response,

        (event, payload) => {
          /*
           * Se este callback pertence
           * a um stream cancelado,
           * ignoramos completamente.
           */
          if (
            generation !==
            state.generation
          ) {
            return;
          }

          if (
            event === "session" &&
            payload.session_id
          ) {
            setSessionId(
              payload.session_id
            );

            return;
          }

          if (
            event === "status"
          ) {
            agent.setStatus(
              payload.phase,
              payload.label
            );

            return;
          }

          if (
            event === "delta"
          ) {
            agent.applyDelta(
              payload
            );

            return;
          }

          if (
            event === "tool"
          ) {
            agent.applyTool(
              payload
            );

            return;
          }

          if (
            event === "error"
          ) {
            streamError =
              payload.message ||
              "O Hermes encontrou um erro.";

            return;
          }

          if (
            event === "done"
          ) {
            complete =
              payload.complete !==
              false;
          }
        },

        controller.signal
      );

      if (
        generation !==
          state.generation ||
        controller.signal
          .aborted
      ) {
        return;
      }

      if (streamError) {
        throw new Error(
          streamError
        );
      }

      if (!complete) {
        throw new Error(
          "O turno encerrou antes do Hermes concluir."
        );
      }

      if (!agent.hasTerminalText()) {
        throw new Error(
          "O Hermes concluiu sem entregar a resposta final depois das ferramentas."
        );
      }
      
      /*
       * Boundary determinístico do turno:
       * primeiro Hermes termina,
       * depois sincronizamos o domínio.
       */
      await refreshWorkspace();
      
      agent.finish(true);
      
      setPhase("idle");
      
      messageInput.focus();
    } catch (error) {
      if (
        generation !==
          state.generation ||
        isAbortError(error)
      ) {
        return;
      }

      /*
       * Pode acontecer:
       *
       * Hermes concluiu e persistiu
       * a resposta, mas a conexão SSE
       * morreu antes do browser ver
       * `done`.
       *
       * Então tentamos recuperar pelo
       * transcript UMA ÚLTIMA VEZ.
       */
      const recovered =
        await reconcileTurn(
          agent,
          trimmed
        ).catch(
          () => false
        );

      if (
        recovered &&
        agent.hasTerminalText()
      ) {
        await refreshWorkspace()
          .catch(() => {});
      
        agent.finish(true);
      
        setPhase("idle");
      
        showBanner("");
      
        messageInput.focus();
      
        return;
      }

      agent.finish(false);

      setPhase(
        "failed",
        "Algo falhou"
      );

      showBanner(
        error?.message ||
          "Erro na conversa.",
        true
      );

      /*
       * Liberamos o composer para
       * retry.
       */
      state.phase = "idle";

      sendBtn.disabled =
        false;

      resetBtn.disabled =
        false;

      messageInput.disabled =
        false;
    } finally {
      if (
        state.controller ===
        controller
      ) {
        state.controller =
          null;
      }
    }
  }

  async function resetDemo() {
    const ok =
      window.confirm(
        "Isso apaga o cardápio, a conversa e recarrega a despensa. Continuar?"
      );

    if (!ok) return;

    await abortCurrentTurn();

    setPhase(
      "resetting",
      "Resetando…"
    );

    showBanner("");

    try {
      const response =
        await fetch(
          "/api/reset",
          {
            method: "POST",
          }
        );

      const data =
        await response
          .json()
          .catch(
            () => ({})
          );

      if (!response.ok) {
        throw new Error(
          data.error ||
            "Não foi possível resetar a demo."
        );
      }

      setSessionId(
        data.session_id ||
          null
      );

      chatLog.replaceChildren();

      renderWorkspace(
        data.workspace || {}
      );

      const prompt =
        String(
          data.demo_prompt ||
          ""
        );

      setPhase("idle");

      if (prompt) {
        await sendMessage(prompt);
      }
    } catch (error) {
      setPhase("idle");

      showBanner(
        error?.message ||
          "Falha ao resetar.",
        true
      );
    }
  }

  composer.addEventListener(
    "submit",
    (event) => {
      event.preventDefault();

      if (
        state.phase !==
        "idle"
      ) {
        return;
      }

      const text =
        messageInput.value;

      messageInput.value =
        "";

      sendMessage(text);
    }
  );

  messageInput.addEventListener(
    "keydown",
    (event) => {
      if (
        event.key ===
          "Enter" &&
        !event.shiftKey
      ) {
        event.preventDefault();

        composer.requestSubmit();
      }
    }
  );

  resetBtn.addEventListener(
    "click",
    resetDemo
  );

  async function boot() {
    setPhase("idle");
    showBanner("");
  
    try {
      const health =
        await fetch(
          "/api/health",
          {
            cache: "no-store",
          }
        ).then(
          (response) =>
            response.json()
        );
  
      if (!health.hermes) {
        showBanner(
          health.error ||
            "Hermes API não está disponível. Inicie o gateway antes da UI.",
          true
        );
      }
  
      /*
       * Sempre restaurar o estado
       * determinístico do domínio.
       */
      await refreshWorkspace();
  
      /*
       * Depois restaurar a conversa.
       */
      if (state.sessionId) {
        const hydrated =
          await hydrateTranscript()
            .catch(
              () => false
            );
  
        if (!hydrated) {
          console.info(
            "Transcript hydration unavailable."
          );
        }
      }
  
      messageInput.focus();
    } catch (error) {
      showBanner(
        error?.message ||
          "Não foi possível carregar a demo.",
        true
      );
    }
  }

  boot();
})();
