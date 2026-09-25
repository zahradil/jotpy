(() => {
  const app = document.getElementById("app");
  const sheetId = document.body.dataset.sheetId || "";
  const shareId = document.body.dataset.shareId || "";
  const shareAccess = document.body.dataset.shareAccess || "";
  const tooLargeAtStart = document.body.dataset.tooLarge === "1";
  if (!app || document.body.dataset.page !== "sheet" || !sheetId) return;

  const isPublic = Boolean(shareId);
  const canEdit = !isPublic || shareAccess === "edit";
  const themeIcon = window.__themeIcon || ((theme) => (theme === "dark" ? "☀" : "☾"));
  const state = {
    title: document.title || "untitled",
    version: 0,
    columns: [],
    rows: [],
    shareAccess: shareAccess || "none",
    shareId: isPublic ? (shareId || null) : (document.body.dataset.sheetShareId || null),
    clientId: "",
    peers: new Map(),
  };

  let ws = null;
  let socketGen = 0;
  let sawHello = false;
  let stopped = false;
  let rendering = false;
  let waiter = null;
  let tail = Promise.resolve();
  let readyResolve = () => {};
  let ready = new Promise((resolve) => { readyResolve = resolve; });
  let titleInput = null;

  renderShell();
  if (tooLargeAtStart) {
    showTooLarge();
    return;
  }
  connect();

  function renderShell() {
    const editButtons = canEdit
      ? `<jot-button variant="ghost" size="sm" id="addColumn">Add column</jot-button><jot-button variant="ghost" size="sm" id="addRow">Add row</jot-button>`
      : "";
    const back = isPublic ? "" : `<jot-icon-button icon="back" label="Back" id="notesButton"></jot-icon-button>`;
    const title = canEdit && !isPublic
      ? `<input id="titleInput" class="title-input" type="text" spellcheck="false" value="${escapeHtml(state.title)}" />`
      : `<div class="topbar-title" id="titleText">${escapeHtml(state.title)}</div>`;
    const share = isPublic
      ? ""
      : `<div class="share-popover-wrap" id="sharePopoverWrap"><jot-icon-button icon="share" label="Share" id="shareButton"></jot-icon-button><div class="share-popover hidden" id="sharePopover"></div></div>`;
    app.innerHTML = `
      <div class="app-root sheet-root">
        <header class="topbar">
          <div class="topbar-left">
            ${back}
            ${title}
            <span class="status-text sheet-version" id="sheetVersion"></span>
            <span class="status-text" id="saveStatus"></span>
          </div>
          <div class="topbar-right">
            ${editButtons}
            <jot-icon-button icon="robot" label="Agent setup" id="agentButton"></jot-icon-button>
            ${share}
            <button type="button" class="jot-btn-icon jot-btn-icon--md theme-toggle" aria-label="Toggle theme">${themeIcon(document.documentElement.getAttribute("data-theme") || "dark")}</button>
          </div>
        </header>
        <div class="sheet-scroll" id="gridHost"></div>
        <div class="modal-backdrop hidden" id="modalBackdrop"></div>
      </div>
    `;
    titleInput = document.getElementById("titleInput");
    const notesButton = document.getElementById("notesButton");
    if (notesButton) notesButton.addEventListener("click", () => { window.location.href = "/"; });
    const addColumn = document.getElementById("addColumn");
    const addRow = document.getElementById("addRow");
    if (addColumn) {
      addColumn.addEventListener("click", () => {
        const name = window.prompt("Column name");
        if (!name) return;
        sendOps([{ op: "insert_column", name }]);
      });
    }
    if (addRow) addRow.addEventListener("click", () => sendOps([{ op: "insert_row" }]));
    if (titleInput) {
      titleInput.addEventListener("change", () => saveTitle());
    }
    const shareButton = document.getElementById("shareButton");
    if (shareButton) shareButton.addEventListener("click", (event) => { event.stopPropagation(); toggleShare(); });
    const agentButton = document.getElementById("agentButton");
    if (agentButton) {
      agentButton.addEventListener("click", () => {
        if (!isPublic && (state.shareAccess === "none" || !state.shareId)) return;
        openAgent();
      });
    }
    syncAgentButton();
    document.getElementById("gridHost").addEventListener("click", onGridClick);
    document.getElementById("gridHost").addEventListener("dblclick", onGridDblClick);
  }

  function onGridClick(event) {
    const deleteColumn = event.target.closest("[data-delete-column]");
    if (deleteColumn) {
      const column = state.columns.find((item) => item.id === deleteColumn.dataset.deleteColumn);
      if (!column || !window.confirm(`Delete column ${column.name}?`)) return;
      sendOps([{ op: "delete_column", name: column.name }]);
      return;
    }
    const deleteRow = event.target.closest("[data-delete-row]");
    if (deleteRow) {
      if (!window.confirm("Delete this row?")) return;
      sendOps([{ op: "delete_row", row: deleteRow.dataset.deleteRow }]);
      return;
    }
    const cell = event.target.closest("td[data-row-id]");
    if (!cell || event.target.closest("input")) return;
    const rowId = cell.dataset.rowId;
    const columnId = cell.dataset.columnId;
    sendPresence(rowId, columnId);
    if (!canEdit) return;
    beginEdit(cell, rowId, columnId);
  }

  function onGridDblClick(event) {
    if (!canEdit) return;
    const name = event.target.closest("[data-column-name]");
    if (!name) return;
    const column = state.columns.find((item) => item.id === name.dataset.columnName);
    if (!column) return;
    const input = document.createElement("input");
    input.className = "sheet-input";
    input.value = column.name;
    input.dataset.original = column.name;
    name.replaceWith(input);
    input.focus();
    input.select();
    input.addEventListener("keydown", (keyEvent) => {
      if (keyEvent.key === "Enter") { keyEvent.preventDefault(); input.blur(); }
      if (keyEvent.key === "Escape") { input.dataset.skip = "1"; input.blur(); }
    });
    input.addEventListener("blur", () => {
      if (rendering) return;
      const next = input.value;
      if (input.dataset.skip === "1" || next === column.name || !next) {
        renderGrid();
        return;
      }
      sendOps([{ op: "rename_column", name: column.name, newName: next }]);
    });
  }

  function beginEdit(cell, rowId, columnId) {
    const column = state.columns.find((item) => item.id === columnId);
    if (!column) return;
    const row = state.rows.find((item) => item.id === rowId);
    const current = row && row.cells ? row.cells[columnId] || "" : "";
    const input = document.createElement("input");
    input.className = "sheet-input";
    input.value = current;
    input.dataset.original = current;
    input.spellcheck = false;
    input.autocomplete = "off";
    cell.textContent = "";
    cell.appendChild(input);
    input.focus();
    input.addEventListener("keydown", (keyEvent) => {
      if (keyEvent.key === "Enter") { keyEvent.preventDefault(); input.blur(); }
      if (keyEvent.key === "Escape") { input.dataset.skip = "1"; input.blur(); }
    });
    input.addEventListener("blur", () => {
      if (rendering) return;
      if (input.dataset.skip === "1" || input.value === current) {
        renderGrid();
        return;
      }
      sendOps([{ op: "set", row: rowId, column: column.name, value: input.value }]);
    });
  }

  function renderGrid() {
    const host = document.getElementById("gridHost");
    if (!host || host.querySelector(".sheet-too-large")) return;
    rendering = true;
    const head = state.columns.map((column) => `
      <th data-column-id="${escapeHtml(column.id)}">
        <div class="sheet-col">
          <span class="sheet-col-name" data-column-name="${escapeHtml(column.id)}">${escapeHtml(column.name)}</span>
          ${canEdit ? `<button type="button" class="sheet-x" data-delete-column="${escapeHtml(column.id)}" aria-label="Delete column">×</button>` : ""}
        </div>
      </th>
    `).join("");
    const body = state.rows.map((row, index) => {
      const cells = state.columns.map((column) => {
        const value = row.cells && row.cells[column.id] != null ? row.cells[column.id] : "";
        return `<td class="sheet-cell" data-row-id="${escapeHtml(row.id)}" data-column-id="${escapeHtml(column.id)}">${escapeHtml(value)}</td>`;
      }).join("");
      return `<tr>
        <th class="sheet-row-head">${canEdit ? `<button type="button" class="sheet-x" data-delete-row="${escapeHtml(row.id)}" aria-label="Delete row">×</button>` : ""}${index + 1}</th>
        ${cells}
      </tr>`;
    }).join("");
    host.innerHTML = `
      <table class="sheet-grid">
        <thead><tr><th class="sheet-corner"></th>${head}</tr></thead>
        <tbody>${body || ""}</tbody>
      </table>
    `;
    paintCursors();
    rendering = false;
    const version = document.getElementById("sheetVersion");
    if (version) version.textContent = `v${state.version}`;
  }

  function paintCursors() {
    const grouped = new Map();
    for (const peer of state.peers.values()) {
      if (!peer.selection || !peer.selection.rowId) continue;
      const key = `${peer.selection.rowId}:${peer.selection.columnId}`;
      const list = grouped.get(key) || [];
      list.push(peer);
      grouped.set(key, list);
    }
    for (const [key, peers] of grouped) {
      const [rowId, columnId] = key.split(":");
      const cell = document.querySelector(`td[data-row-id="${rowId}"][data-column-id="${columnId}"]`);
      if (!cell) continue;
      cell.style.outline = `2px solid ${peers[0].color || "var(--blue)"}`;
      cell.style.outlineOffset = "-2px";
      cell.title = peers.map((peer) => peer.name || "Someone").join(", ");
    }
  }

  function showTooLarge() {
    stopped = true;
    if (ws) {
      const current = ws;
      ws = null;
      current.close();
    }
    const host = document.getElementById("gridHost");
    if (host) {
      host.innerHTML = `<p class="sheet-too-large">This table has more than 2000 rows, so the grid stays closed. Read it with a query that uses WHERE or LIMIT.</p>`;
    }
    document.getElementById("addColumn")?.remove();
    document.getElementById("addRow")?.remove();
  }

  function applyMessage(message) {
    if (message.tooLarge) {
      showTooLarge();
      return;
    }
    state.version = message.version;
    if (message.title) state.title = message.title;
    if ("shareAccess" in message && message.shareAccess) state.shareAccess = message.shareAccess;
    if ("shareId" in message) state.shareId = message.shareId || null;
    syncAgentButton();
    state.columns = message.columns || [];
    state.rows = message.rows || [];
    if (titleInput && document.activeElement !== titleInput) titleInput.value = state.title;
    const titleText = document.getElementById("titleText");
    if (titleText) titleText.textContent = state.title;
    document.title = state.title;
    renderGrid();
  }

  function connect() {
    if (stopped) return;
    const gen = ++socketGen;
    sawHello = false;
    ready = new Promise((resolve) => { readyResolve = resolve; });
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const query = isPublic ? `shareId=${encodeURIComponent(shareId)}` : `sheetId=${encodeURIComponent(sheetId)}`;
    const socket = new WebSocket(`${protocol}//${location.host}/?${query}`);
    ws = socket;
    socket.addEventListener("message", (event) => {
      if (gen !== socketGen) return;
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === "sheet") {
        sawHello = true;
        if (message.clientId) state.clientId = message.clientId;
        applyMessage(message);
        readyResolve();
        return;
      }
      if (message.type === "ops-result") {
        if (waiter) {
          const resolve = waiter;
          waiter = null;
          resolve(message);
        }
        return;
      }
      if (message.type === "presence" && message.clientId && message.clientId !== state.clientId) {
        state.peers.set(message.clientId, message);
        renderGrid();
        return;
      }
      if (message.type === "presence-leave") {
        state.peers.delete(message.clientId);
        renderGrid();
      }
    });
    socket.addEventListener("close", () => {
      if (gen === socketGen && waiter) {
        const resolve = waiter;
        waiter = null;
        resolve({ ok: false, error: "Disconnected" });
      }
      if (gen !== socketGen || stopped) return;
      setStatus("Disconnected");
      if (!sawHello) return;
      window.setTimeout(() => { if (gen === socketGen) connect(); }, 1000);
    });
  }

  function sendOps(ops) {
    const job = tail.then(() => ready).then(() => deliver(ops));
    tail = job.then(() => {}, () => {});
    return job;
  }

  function deliver(ops) {
    return new Promise((resolve, reject) => {
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        reject(new Error("Disconnected"));
        return;
      }
      waiter = resolve;
      ws.send(JSON.stringify({
        type: "ops",
        clientId: state.clientId,
        baseVersion: state.version,
        ops,
      }));
    }).then((message) => {
      if (!message.ok) {
        setStatus(message.error === "conflict" ? "Conflict" : (message.error || "Rejected"));
        if (message.error === "conflict") connect();
        else renderGrid();
        return message;
      }
      applyMessage(message);
      setStatus("Saved");
      return message;
    });
  }

  function sendPresence(rowId, columnId) {
    if (!ws || ws.readyState !== WebSocket.OPEN || !state.clientId) return;
    ws.send(JSON.stringify({
      type: "presence",
      clientId: state.clientId,
      selection: { rowId, columnId },
    }));
  }

  async function saveTitle() {
    if (!titleInput) return;
    const saved = await api(`/api/sheets/${sheetId}`, { method: "PUT", body: { title: titleInput.value } });
    state.title = saved.title || titleInput.value;
    state.shareAccess = saved.shareAccess;
    state.shareId = saved.shareId || null;
    titleInput.value = state.title;
    document.title = state.title;
    setStatus("Saved");
  }

  function toggleShare() {
    const popover = document.getElementById("sharePopover");
    if (!popover) return;
    if (!popover.classList.contains("hidden")) { popover.classList.add("hidden"); return; }
    const access = state.shareAccess || "none";
    const link = state.shareId ? `${location.origin}/s/${state.shareId}` : "";
    popover.innerHTML = `
      <div class="share-popover-row">
        <select id="shareAccessSelect">
          <option value="none" ${access === "none" ? "selected" : ""}>Not shared</option>
          <option value="view" ${access === "view" ? "selected" : ""}>View only</option>
          <option value="edit" ${access === "edit" ? "selected" : ""}>Can edit</option>
        </select>
        <button type="button" id="shareCopyBtn" ${link ? "" : "disabled"}>copy link</button>
      </div>
      <div class="share-popover-row">
        <button type="button" id="shareRotateBtn" ${access === "none" ? "disabled" : ""}>new link</button>
      </div>
    `;
    popover.classList.remove("hidden");
    const select = popover.querySelector("#shareAccessSelect");
    const copyBtn = popover.querySelector("#shareCopyBtn");
    const rotateBtn = popover.querySelector("#shareRotateBtn");
    const apply = (saved) => {
      state.shareAccess = saved.shareAccess;
      state.shareId = saved.shareId || null;
      const next = saved.shareUrl || (state.shareId ? `${location.origin}/s/${state.shareId}` : "");
      copyBtn.disabled = !next;
      rotateBtn.disabled = saved.shareAccess === "none";
      syncAgentButton();
      copyBtn.onclick = async () => {
        if (!next) return;
        try { await navigator.clipboard.writeText(next); copyBtn.textContent = "copied!"; } catch {}
      };
    };
    select.addEventListener("change", async () => {
      apply(await api(`/api/sheets/${sheetId}`, { method: "PUT", body: { shareAccess: select.value } }));
    });
    rotateBtn.addEventListener("click", async () => {
      if (select.value === "none") return;
      apply(await api(`/api/sheets/${sheetId}`, { method: "PUT", body: { shareAccess: select.value, rotateShare: true } }));
    });
    if (link) {
      copyBtn.onclick = async () => {
        try { await navigator.clipboard.writeText(link); copyBtn.textContent = "copied!"; } catch {}
      };
    }
    const closeHandler = (event) => {
      if (!popover.contains(event.target) && !event.target.closest("#shareButton")) {
        popover.classList.add("hidden");
        document.removeEventListener("click", closeHandler);
      }
    };
    window.setTimeout(() => document.addEventListener("click", closeHandler), 0);
  }

  function openAgent() {
    const origin = location.origin;
    let readUrl;
    let importUrl;
    let opsUrl;
    const lines = [
      "Tohle je jedna tabulka pro člověka a agenta. Člověk ji může mezitím měnit v prohlížeči.",
      "Před zápisem si přečti version. Pošli ji jako X-Jot-Base-Version nebo baseVersion.",
      "Když odpověď je 409, tabulka se mezitím změnila. Přečti ji znovu a zapiš jen to, co pořád platí. Stejný požadavek neopakuj.",
      "Buňka je text. 001 zůstane 001. Verze je v odpovědi a v hlavičce X-Jot-Version u CSV.",
      "",
    ];
    if (isPublic) {
      lines.push(`Odkaz: ${origin}/s/${shareId}`);
      readUrl = `${origin}/api/share/${shareId}/data`;
      importUrl = `${origin}/api/share/${shareId}/import-csv`;
      opsUrl = `${origin}/api/share/${shareId}/ops`;
    } else if (!state.shareId) {
      lines.push("Sdílení je vypnuté. Klíč vlastníka vidí tabulku.");
      readUrl = `${origin}/api/sheets/${sheetId}/data`;
      importUrl = `${origin}/api/sheets/${sheetId}/import-csv`;
      opsUrl = `${origin}/api/sheets/${sheetId}/ops`;
    } else {
      lines.push(`Odkaz: ${origin}/s/${state.shareId}`);
      readUrl = `${origin}/api/share/${state.shareId}/data`;
      importUrl = `${origin}/api/share/${state.shareId}/import-csv`;
      opsUrl = `${origin}/api/share/${state.shareId}/ops`;
    }
    lines.push(
      "",
      "Soubor CSV se nahrává importem. To není editace a jde jen do prázdné tabulky. Když už má sloupec nebo řádek, server to odmítne.",
      `Nejdřív GET ${readUrl} a vezmi version.`,
      `POST ${importUrl}`,
      "Hlavička X-Jot-Base-Version: ta verze. Content-Type: text/csv; charset=utf-8.",
      "Tělo je celý soubor. První řádek jsou jména sloupců. Sloupec _id se při importu zahodí.",
      "curl -sS -X POST <import-csv> -H 'Content-Type: text/csv; charset=utf-8' -H 'X-Jot-Base-Version: <verze>' --data-binary @soubor.csv",
      "",
      "Úpravy hotové tabulky jsou dávka, ne další import.",
      `POST ${opsUrl}`,
      '{"baseVersion": <verze>, "ops": [',
      '  {"op": "insert_column", "name": "jméno"},',
      '  {"op": "insert_row", "values": {"jméno": "text"}},',
      '  {"op": "set", "row": "<id řádku>", "column": "jméno", "value": "text"}',
      "]}",
      "id řádku je pole id v JSON a první sloupec _id v CSV.",
      'Podmínka {"column","value"} musí trefit právě jeden řádek.',
      "view import ani dávku nepustí.",
    );
    const instructions = lines.join("\n");
    const backdrop = document.getElementById("modalBackdrop");
    if (!backdrop) return;
    backdrop.classList.remove("hidden");
    backdrop.innerHTML = `
      <div class="modal agent-modal" role="dialog" aria-modal="true">
        <div class="settings-header">
          <h2 class="settings-title">Agent setup</h2>
          <jot-icon-button icon="close" label="Close" id="agentModalClose"></jot-icon-button>
        </div>
        <p class="agent-hint">Předej tenhle text agentovi.</p>
        <pre class="agent-instructions"><code>${escapeHtml(instructions)}</code></pre>
        <button type="button" id="agentCopyBtn">copy to clipboard</button>
      </div>
    `;
    const close = () => { backdrop.classList.add("hidden"); backdrop.innerHTML = ""; };
    backdrop.querySelector("#agentModalClose").addEventListener("click", close);
    backdrop.addEventListener("click", (event) => { if (event.target === backdrop) close(); });
    backdrop.querySelector("#agentCopyBtn").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(instructions); backdrop.querySelector("#agentCopyBtn").textContent = "copied!"; } catch {}
    });
  }

  function syncAgentButton() {
    const button = document.getElementById("agentButton");
    if (!button || isPublic) return;
    const off = state.shareAccess === "none" || !state.shareId;
    button.classList.toggle("is-disabled", off);
    const inner = button.querySelector("button");
    if (inner) inner.disabled = off;
  }

  function setStatus(value) {
    const node = document.getElementById("saveStatus");
    if (node) node.textContent = value;
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      method: options.method || "GET",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Request failed.");
    return payload;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }
})();
