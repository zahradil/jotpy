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

  const DEFAULT_WIDTH = 144;
  const MIN_WIDTH = 40;
  const MAX_WIDTH = 2000;
  const ROW_HEAD_WIDTH = 52;
  const FAR = 1e9;

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
  let gridHost = null;
  // Selected cell, and its last known position for when that row or column disappears.
  let sel = null;
  let selIndex = { row: 0, col: 0 };
  // Cell being typed into: { rowId, columnId, original, mode, input }. "replace" mode started by typing, so arrows leave the cell.
  let edit = null;
  // Column header being renamed: { columnId, input }.
  let rename = null;
  // A drag or resize holds renders until the mouse is released.
  let gesture = null;
  let renderQueued = false;

  renderShell();
  if (tooLargeAtStart) {
    showTooLarge();
    return;
  }
  connect();

  function renderShell() {
    const editButtons = canEdit
      ? `<jot-button variant="ghost" size="sm" id="deleteRow" class="hidden">Delete row</jot-button><jot-button variant="ghost" size="sm" id="deleteColumn" class="hidden">Delete column</jot-button><jot-button variant="ghost" size="sm" id="addColumn">Add column</jot-button><jot-button variant="ghost" size="sm" id="addRow">Add row</jot-button>`
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
            <jot-icon-button icon="preview" label="Open cell (Space)" id="openCell" class="hidden"></jot-icon-button>
            <jot-icon-button icon="robot" label="Agent setup" id="agentButton"></jot-icon-button>
            ${share}
            <button type="button" class="jot-btn-icon jot-btn-icon--md theme-toggle" aria-label="Toggle theme">${themeIcon(document.documentElement.getAttribute("data-theme") || "dark")}</button>
          </div>
        </header>
        <div class="sheet-cellbar hidden" id="cellBar">
          <span class="sheet-cellbar-ref" id="cellBarRef"></span>
          <div class="sheet-cellbar-value" id="cellBarValue"></div>
        </div>
        <div class="sheet-scroll" id="gridHost" tabindex="0"></div>
        <div class="modal-backdrop hidden" id="modalBackdrop"></div>
      </div>
    `;
    titleInput = document.getElementById("titleInput");
    gridHost = document.getElementById("gridHost");
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
    document.getElementById("deleteRow")?.addEventListener("click", deleteSelectedRow);
    document.getElementById("deleteColumn")?.addEventListener("click", deleteSelectedColumn);
    document.getElementById("openCell").addEventListener("click", openCellModal);
    document.getElementById("cellBar").addEventListener("dblclick", openCellModal);
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
    gridHost.addEventListener("mousedown", onGridMouseDown);
    gridHost.addEventListener("dblclick", onGridDblClick);
    gridHost.addEventListener("keydown", onGridKeyDown);
  }

  function deleteSelectedRow() {
    if (!sel) return;
    commitEdit();
    const index = rowIndex(sel.rowId);
    if (index < 0 || !window.confirm(`Delete row ${index + 1}?`)) return;
    sendOps([{ op: "delete_row", row: sel.rowId }]);
  }

  function deleteSelectedColumn() {
    if (!sel) return;
    commitEdit();
    const column = columnById(sel.columnId);
    if (!column || !window.confirm(`Delete column ${column.name}?`)) return;
    sendOps([{ op: "delete_column", name: column.name }]);
  }

  // The bar under the topbar shows the whole value of the selected cell, wrapped.
  function syncCellBar() {
    const bar = document.getElementById("cellBar");
    const open = document.getElementById("openCell");
    const column = sel ? columnById(sel.columnId) : null;
    const index = sel ? rowIndex(sel.rowId) : -1;
    const shown = Boolean(column) && index >= 0;
    bar?.classList.toggle("hidden", !shown);
    open?.classList.toggle("hidden", !shown);
    if (!shown) return;
    const value = cellValue(sel.rowId, sel.columnId);
    const ref = document.getElementById("cellBarRef");
    const text = document.getElementById("cellBarValue");
    ref.textContent = `${column.name} · ${index + 1}`;
    ref.title = ref.textContent;
    text.textContent = value || "(empty)";
    text.classList.toggle("is-empty", !value);
    text.scrollTop = 0;
  }

  // Space or the eye button opens the selected cell in a dialog, to read and, with edit access, to change.
  function openCellModal() {
    if (!sel) return;
    commitEdit();
    const column = columnById(sel.columnId);
    const index = rowIndex(sel.rowId);
    const backdrop = document.getElementById("modalBackdrop");
    if (!column || index < 0 || !backdrop) return;
    const { rowId, columnId } = sel;
    const original = cellValue(rowId, columnId);
    backdrop.classList.remove("hidden");
    backdrop.innerHTML = `
      <div class="modal cell-modal" role="dialog" aria-modal="true">
        <div class="settings-header">
          <h2 class="settings-title">${escapeHtml(column.name)} · ${index + 1}</h2>
          <jot-icon-button icon="close" label="Close" id="cellModalClose"></jot-icon-button>
        </div>
        <textarea id="cellModalText" class="cell-modal-text" spellcheck="false"${canEdit ? "" : " readonly"}></textarea>
        <div class="modal-actions cell-modal-actions">
          <span class="cell-modal-hint">${canEdit ? "Ctrl+Enter saves · Esc closes" : "Esc closes"}</span>
          <jot-button variant="ghost" size="sm" id="cellModalCopy">Copy</jot-button>
          ${canEdit ? `<jot-button variant="primary" size="sm" id="cellModalSave">Save</jot-button>` : ""}
        </div>
      </div>
    `;
    const text = backdrop.querySelector("#cellModalText");
    text.value = original;
    const close = () => {
      if (canEdit && text.value !== original && !window.confirm("Discard changes?")) return;
      backdrop.classList.add("hidden");
      backdrop.innerHTML = "";
      focusGrid();
    };
    const save = () => {
      const current = columnById(columnId);
      if (current && text.value !== original) {
        setLocal(rowId, columnId, text.value);
        restoreCell(rowId, columnId);
        sendOps([{ op: "set", row: rowId, column: current.name, value: text.value }]);
      }
      backdrop.classList.add("hidden");
      backdrop.innerHTML = "";
      focusGrid();
    };
    backdrop.onclick = (event) => { if (event.target === backdrop) close(); };
    backdrop.querySelector("#cellModalClose").addEventListener("click", close);
    backdrop.querySelector("#cellModalSave")?.addEventListener("click", save);
    backdrop.querySelector("#cellModalCopy").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(text.value);
        backdrop.querySelector("#cellModalCopy button").textContent = "Copied";
      } catch {}
    });
    backdrop.querySelector(".cell-modal").addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(); }
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey) && canEdit) { event.preventDefault(); save(); }
    });
    text.focus({ preventScroll: true });
    text.setSelectionRange(0, 0);
    text.scrollTop = 0;
  }

  // The topbar delete buttons act on the selected cell's row and column.
  function syncDeleteButtons() {
    const row = document.getElementById("deleteRow");
    const column = document.getElementById("deleteColumn");
    if (!row || !column) return;
    const index = sel ? rowIndex(sel.rowId) : -1;
    const selected = sel ? columnById(sel.columnId) : null;
    row.classList.toggle("hidden", index < 0);
    column.classList.toggle("hidden", !selected);
    const rowButton = row.querySelector("button");
    const columnButton = column.querySelector("button");
    if (rowButton && index >= 0) rowButton.textContent = `Delete row ${index + 1}`;
    if (columnButton && selected) columnButton.textContent = `Delete column ${selected.name}`;
  }

  function onGridMouseDown(event) {
    if (event.button !== 0) return;
    const target = event.target;
    if (target.closest("input")) return;
    const resize = target.closest("[data-resize-column]");
    if (resize) {
      if (canEdit) startResize(event, resize.dataset.resizeColumn);
      return;
    }
    const columnHead = target.closest("th[data-column-id]");
    const rowHead = target.closest("th[data-row-head]");
    const cell = target.closest("td[data-row-id]");
    if (!columnHead && !rowHead && !cell) return;
    // Keep focus on the grid instead of letting the browser select text.
    event.preventDefault();
    commitEdit();
    if (cell) {
      select(cell.dataset.rowId, cell.dataset.columnId);
    } else if (rowHead) {
      select(rowHead.dataset.rowHead, sel ? sel.columnId : state.columns[0]?.id);
    } else {
      select(sel ? sel.rowId : state.rows[0]?.id, columnHead.dataset.columnId);
    }
    focusGrid();
    if (!canEdit) return;
    if (rowHead) startDrag(event, "row", rowHead.dataset.rowHead);
    else if (columnHead) startDrag(event, "column", columnHead.dataset.columnId);
  }

  function onGridDblClick(event) {
    if (!canEdit) return;
    const resize = event.target.closest("[data-resize-column]");
    if (resize) {
      autoFit(resize.dataset.resizeColumn);
      return;
    }
    const name = event.target.closest("[data-column-name]");
    if (name) {
      beginRename(name.dataset.columnName);
      return;
    }
    const cell = event.target.closest("td[data-row-id]");
    if (!cell || (edit && cell.contains(edit.input))) return;
    select(cell.dataset.rowId, cell.dataset.columnId);
    beginEdit("edit");
  }

  function onGridKeyDown(event) {
    if (event.target !== gridHost || gesture || event.isComposing) return;
    const ctrl = event.ctrlKey || event.metaKey;
    const altGraph = event.getModifierState && event.getModifierState("AltGraph");
    let handled = true;
    switch (event.key) {
      case "ArrowUp": moveSelection(ctrl ? -FAR : -1, 0); break;
      case "ArrowDown": moveSelection(ctrl ? FAR : 1, 0); break;
      case "ArrowLeft": moveSelection(0, ctrl ? -FAR : -1); break;
      case "ArrowRight": moveSelection(0, ctrl ? FAR : 1); break;
      case "Home": moveSelection(ctrl ? -FAR : 0, -FAR); break;
      case "End": moveSelection(ctrl ? FAR : 0, FAR); break;
      case "PageUp": moveSelection(-pageRows(), 0); break;
      case "PageDown": moveSelection(pageRows(), 0); break;
      case "Tab": tabMove(event.shiftKey); break;
      case "Enter":
        if (event.shiftKey || !canEdit || !sel) moveSelection(event.shiftKey ? -1 : 1, 0);
        else beginEdit("edit");
        break;
      case "F2": beginEdit("edit"); break;
      case " ": openCellModal(); break;
      case "Delete":
      case "Backspace": clearSelected(); break;
      default:
        if ([...event.key].length === 1 && (altGraph || (!ctrl && !event.altKey)) && canEdit && sel) {
          beginEdit("replace", event.key);
        } else {
          handled = false;
        }
    }
    if (handled) event.preventDefault();
  }

  function onEditKeyDown(event) {
    event.stopPropagation();
    if (event.isComposing || !edit) return;
    const replace = edit.mode === "replace";
    let move = null;
    switch (event.key) {
      case "Enter": move = [event.shiftKey ? -1 : 1, 0]; break;
      case "Tab": move = "tab"; break;
      case "ArrowUp": move = [-1, 0]; break;
      case "ArrowDown": move = [1, 0]; break;
      case "ArrowLeft": if (replace) move = [0, -1]; break;
      case "ArrowRight": if (replace) move = [0, 1]; break;
      case "Escape":
        event.preventDefault();
        cancelEdit();
        return;
    }
    if (!move) return;
    event.preventDefault();
    commitEdit();
    focusGrid();
    if (move === "tab") tabMove(event.shiftKey);
    else moveSelection(move[0], move[1]);
  }

  function select(rowId, columnId) {
    if (!rowId || !columnId) return;
    const changed = !sel || sel.rowId !== rowId || sel.columnId !== columnId;
    sel = { rowId, columnId };
    selIndex = { row: rowIndex(rowId), col: columnIndex(columnId) };
    paintSelection();
    const cell = cellElement(rowId, columnId);
    if (cell) cell.scrollIntoView({ block: "nearest", inline: "nearest" });
    if (changed) sendPresence(rowId, columnId);
  }

  function moveSelection(deltaRow, deltaColumn) {
    if (!state.rows.length || !state.columns.length) return;
    if (!sel) {
      select(state.rows[0].id, state.columns[0].id);
      return;
    }
    const row = clamp(rowIndex(sel.rowId) + deltaRow, 0, state.rows.length - 1);
    const col = clamp(columnIndex(sel.columnId) + deltaColumn, 0, state.columns.length - 1);
    select(state.rows[row].id, state.columns[col].id);
  }

  function tabMove(back) {
    if (!sel) {
      moveSelection(0, 0);
      return;
    }
    const col = columnIndex(sel.columnId);
    const row = rowIndex(sel.rowId);
    const last = state.columns.length - 1;
    if (!back && col === last && row < state.rows.length - 1) moveSelection(1, -FAR);
    else if (back && col === 0 && row > 0) moveSelection(-1, FAR);
    else moveSelection(0, back ? -1 : 1);
  }

  function pageRows() {
    const row = gridHost.querySelector("tbody tr");
    const height = row ? row.offsetHeight : 34;
    return Math.max(1, Math.floor(gridHost.clientHeight / height) - 2);
  }

  function validateSelection() {
    if (!state.rows.length || !state.columns.length) {
      sel = null;
      return;
    }
    if (!sel) return;
    let row = rowIndex(sel.rowId);
    let col = columnIndex(sel.columnId);
    if (row < 0) row = clamp(selIndex.row, 0, state.rows.length - 1);
    if (col < 0) col = clamp(selIndex.col, 0, state.columns.length - 1);
    sel = { rowId: state.rows[row].id, columnId: state.columns[col].id };
    selIndex = { row, col };
  }

  function paintSelection() {
    for (const node of gridHost.querySelectorAll(".sheet-active, .sheet-head-active")) {
      node.classList.remove("sheet-active", "sheet-head-active");
    }
    syncDeleteButtons();
    syncCellBar();
    if (!sel) return;
    cellElement(sel.rowId, sel.columnId)?.classList.add("sheet-active");
    gridHost.querySelector(`th[data-column-id="${CSS.escape(sel.columnId)}"]`)?.classList.add("sheet-head-active");
    gridHost.querySelector(`th[data-row-head="${CSS.escape(sel.rowId)}"]`)?.classList.add("sheet-head-active");
  }

  function beginEdit(mode, initial) {
    if (!canEdit || !sel || edit) return;
    const cell = cellElement(sel.rowId, sel.columnId);
    if (!cell) return;
    const original = cellValue(sel.rowId, sel.columnId);
    // A one-line input would drop the line breaks, so such a cell opens in the dialog.
    if (initial == null && original.includes("\n")) {
      openCellModal();
      return;
    }
    const value = initial != null ? initial : original;
    edit = { rowId: sel.rowId, columnId: sel.columnId, original, mode, input: null };
    mountEditor(cell, value, value.length, value.length, true);
  }

  function mountEditor(cell, value, start, end, focus) {
    const input = document.createElement("input");
    input.className = "sheet-input";
    input.spellcheck = false;
    input.autocomplete = "off";
    input.value = value;
    cell.textContent = "";
    cell.appendChild(input);
    cell.classList.add("sheet-editing");
    edit.input = input;
    if (focus) {
      input.focus({ preventScroll: true });
      input.setSelectionRange(start, end);
    }
    input.addEventListener("keydown", onEditKeyDown);
    input.addEventListener("blur", () => {
      if (!rendering && edit && edit.input === input) commitEdit();
    });
  }

  function commitEdit() {
    if (!edit) return;
    const { rowId, columnId, original, input } = edit;
    const value = input ? input.value : original;
    edit = null;
    const column = columnById(columnId);
    if (column && value !== original) {
      setLocal(rowId, columnId, value);
      sendOps([{ op: "set", row: rowId, column: column.name, value }]);
    }
    restoreCell(rowId, columnId);
  }

  function cancelEdit() {
    if (!edit) return;
    const { rowId, columnId } = edit;
    edit = null;
    restoreCell(rowId, columnId);
    focusGrid();
  }

  function clearSelected() {
    if (!canEdit || !sel) return;
    const column = columnById(sel.columnId);
    if (!column || cellValue(sel.rowId, sel.columnId) === "") return;
    setLocal(sel.rowId, sel.columnId, "");
    restoreCell(sel.rowId, sel.columnId);
    sendOps([{ op: "set", row: sel.rowId, column: column.name, value: "" }]);
  }

  function restoreCell(rowId, columnId) {
    const cell = cellElement(rowId, columnId);
    if (!cell) return;
    rendering = true;
    cell.textContent = oneLine(cellValue(rowId, columnId));
    rendering = false;
    cell.classList.remove("sheet-editing");
    syncCellBar();
  }

  function beginRename(columnId, value, start, end) {
    const column = columnById(columnId);
    const name = gridHost.querySelector(`[data-column-name="${CSS.escape(columnId)}"]`);
    if (!column || !name) return;
    commitEdit();
    const input = document.createElement("input");
    input.className = "sheet-input";
    input.spellcheck = false;
    input.value = value != null ? value : column.name;
    rename = { columnId, input };
    name.replaceWith(input);
    input.focus();
    if (start != null) input.setSelectionRange(start, end);
    else input.select();
    input.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); focusGrid(); }
      if (event.key === "Escape") {
        event.preventDefault();
        rename = null;
        renderGrid();
        focusGrid();
      }
    });
    input.addEventListener("blur", () => {
      if (rendering || !rename || rename.input !== input) return;
      rename = null;
      const current = columnById(columnId);
      const next = input.value;
      if (current && next && next !== current.name) {
        sendOps([{ op: "rename_column", name: current.name, newName: next }]);
      }
      renderGrid();
    });
  }

  function startResize(event, columnId) {
    event.preventDefault();
    const column = columnById(columnId);
    const col = gridHost.querySelector(`col[data-col="${CSS.escape(columnId)}"]`);
    const table = gridHost.querySelector("table");
    if (!column || !col || !table) return;
    commitEdit();
    const startX = event.clientX;
    const startWidth = columnWidth(column);
    const startTable = tableWidth();
    let width = startWidth;
    gesture = { kind: "resize" };
    document.body.classList.add("sheet-resizing");
    const onMove = (moveEvent) => {
      width = clamp(Math.round(startWidth + moveEvent.clientX - startX), MIN_WIDTH, MAX_WIDTH);
      col.style.width = `${width}px`;
      table.style.width = `${startTable - startWidth + width}px`;
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.classList.remove("sheet-resizing");
      gesture = null;
      if (width !== startWidth) setWidth(column, width);
      flushRender();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function autoFit(columnId) {
    const column = columnById(columnId);
    const head = gridHost.querySelector(`th[data-column-id="${CSS.escape(columnId)}"]`);
    if (!column || !head) return;
    const sample = gridHost.querySelector("td.sheet-cell") || head;
    const context = document.createElement("canvas").getContext("2d");
    context.font = getComputedStyle(sample).font;
    let widest = 0;
    for (const row of state.rows) widest = Math.max(widest, context.measureText(cellValue(row.id, columnId)).width);
    context.font = getComputedStyle(head).font;
    widest = Math.max(widest, context.measureText(column.name).width);
    setWidth(column, clamp(Math.ceil(widest) + 20, MIN_WIDTH, 600));
  }

  function setWidth(column, width) {
    column.width = width;
    renderGrid();
    sendOps([{ op: "resize_column", name: column.name, width }]);
  }

  function startDrag(event, kind, id) {
    const startX = event.clientX;
    const startY = event.clientY;
    let active = false;
    let indicator = null;
    let target;
    const onMove = (moveEvent) => {
      if (!active) {
        if (Math.abs(moveEvent.clientX - startX) + Math.abs(moveEvent.clientY - startY) < 5) return;
        active = true;
        gesture = { kind: "drag" };
        document.body.classList.add("sheet-dragging");
        for (const node of draggedNodes(kind, id)) node.classList.add("sheet-dragged");
        indicator = document.createElement("div");
        indicator.className = `sheet-drop sheet-drop-${kind}`;
        gridHost.appendChild(indicator);
      }
      autoScroll(moveEvent);
      target = dropTarget(kind, moveEvent);
      if (!target) return;
      const table = gridHost.querySelector("table");
      if (kind === "row") {
        Object.assign(indicator.style, { top: `${target.at - 1}px`, left: "0px", width: `${table.offsetWidth}px` });
      } else {
        Object.assign(indicator.style, { left: `${target.at - 1}px`, top: "0px", height: `${table.offsetHeight}px` });
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      if (!active) return;
      gesture = null;
      document.body.classList.remove("sheet-dragging");
      indicator.remove();
      for (const node of gridHost.querySelectorAll(".sheet-dragged")) node.classList.remove("sheet-dragged");
      const list = kind === "row" ? state.rows : state.columns;
      if (target && moveLocal(list, id, target.before)) {
        const op = kind === "row"
          ? { op: "move_row", row: id }
          : { op: "move_column", name: columnById(id).name };
        if (target.before) op.before = target.before;
        renderGrid();
        sendOps([op]);
      } else {
        flushRender();
      }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  function draggedNodes(kind, id) {
    const key = CSS.escape(id);
    if (kind === "row") return gridHost.querySelectorAll(`tr[data-row="${key}"] > *`);
    return gridHost.querySelectorAll(`th[data-column-id="${key}"], td[data-column-id="${key}"]`);
  }

  // Where a drop lands: before which item (null = at the end), and the line position in scroll coordinates.
  function dropTarget(kind, event) {
    const hostRect = gridHost.getBoundingClientRect();
    const items = kind === "row"
      ? [...gridHost.querySelectorAll("tbody tr[data-row]")]
      : [...gridHost.querySelectorAll("thead th[data-column-id]")];
    if (!items.length) return null;
    for (const item of items) {
      const rect = item.getBoundingClientRect();
      if (kind === "row" ? event.clientY < rect.top + rect.height / 2 : event.clientX < rect.left + rect.width / 2) {
        return {
          before: kind === "row" ? item.dataset.row : item.dataset.columnId,
          at: kind === "row" ? rect.top - hostRect.top + gridHost.scrollTop : rect.left - hostRect.left + gridHost.scrollLeft,
        };
      }
    }
    const rect = items[items.length - 1].getBoundingClientRect();
    return {
      before: null,
      at: kind === "row" ? rect.bottom - hostRect.top + gridHost.scrollTop : rect.right - hostRect.left + gridHost.scrollLeft,
    };
  }

  function autoScroll(event) {
    const rect = gridHost.getBoundingClientRect();
    const edge = 40;
    if (event.clientY < rect.top + edge) gridHost.scrollTop -= 20;
    else if (event.clientY > rect.bottom - edge) gridHost.scrollTop += 20;
    if (event.clientX < rect.left + edge) gridHost.scrollLeft -= 20;
    else if (event.clientX > rect.right - edge) gridHost.scrollLeft += 20;
  }

  // Moves the item in place; false when the drop leaves the order as it was.
  function moveLocal(list, id, before) {
    const from = list.findIndex((item) => item.id === id);
    if (from < 0) return false;
    const next = list[from + 1];
    if (before === id || (before && next && before === next.id) || (!before && from === list.length - 1)) return false;
    const [item] = list.splice(from, 1);
    const to = before ? list.findIndex((entry) => entry.id === before) : list.length;
    if (to < 0) {
      list.splice(from, 0, item);
      return false;
    }
    list.splice(to, 0, item);
    return true;
  }

  function flushRender() {
    if (!renderQueued) return;
    renderQueued = false;
    renderGrid();
  }

  function renderGrid() {
    if (!gridHost || gridHost.querySelector(".sheet-too-large")) return;
    if (gesture) {
      renderQueued = true;
      return;
    }
    // Keep a half-typed cell or column name across the rebuild.
    const editing = edit && edit.input ? snapshot(edit.input) : null;
    const renaming = rename ? snapshot(rename.input) : null;
    rendering = true;
    const cols = state.columns.map((column) => `<col data-col="${escapeHtml(column.id)}" style="width:${columnWidth(column)}px">`).join("");
    const head = state.columns.map((column) => `
      <th data-column-id="${escapeHtml(column.id)}">
        <div class="sheet-col">
          <span class="sheet-col-name" data-column-name="${escapeHtml(column.id)}">${escapeHtml(column.name)}</span>
        </div>
        ${canEdit ? `<div class="sheet-resize" data-resize-column="${escapeHtml(column.id)}"></div>` : ""}
      </th>
    `).join("");
    const body = state.rows.map((row, index) => {
      const cells = state.columns.map((column) => {
        const value = row.cells && row.cells[column.id] != null ? row.cells[column.id] : "";
        return `<td class="sheet-cell" data-row-id="${escapeHtml(row.id)}" data-column-id="${escapeHtml(column.id)}">${escapeHtml(oneLine(value))}</td>`;
      }).join("");
      return `<tr data-row="${escapeHtml(row.id)}">
        <th class="sheet-row-head" data-row-head="${escapeHtml(row.id)}">${index + 1}</th>
        ${cells}
      </tr>`;
    }).join("");
    gridHost.innerHTML = `
      <table class="sheet-grid" style="width:${tableWidth()}px">
        <colgroup><col style="width:${ROW_HEAD_WIDTH}px">${cols}</colgroup>
        <thead><tr><th class="sheet-corner"></th>${head}</tr></thead>
        <tbody>${body || ""}</tbody>
      </table>
    `;
    validateSelection();
    if (edit) {
      const cell = cellElement(edit.rowId, edit.columnId);
      if (cell && editing) mountEditor(cell, editing.value, editing.start, editing.end, editing.focused);
      else edit = null;
    }
    paintSelection();
    paintCursors();
    rendering = false;
    if (rename) {
      const { columnId } = rename;
      rename = null;
      if (renaming && columnById(columnId)) beginRename(columnId, renaming.value, renaming.start, renaming.end);
    }
    if (editing && editing.focused && !edit) focusGrid();
    const version = document.getElementById("sheetVersion");
    if (version) version.textContent = `v${state.version}`;
  }

  function snapshot(input) {
    return {
      value: input.value,
      start: input.selectionStart,
      end: input.selectionEnd,
      focused: document.activeElement === input,
    };
  }

  function paintCursors() {
    for (const cell of gridHost.querySelectorAll(".sheet-peer")) {
      cell.classList.remove("sheet-peer");
      cell.style.outline = "";
      cell.style.outlineOffset = "";
      cell.removeAttribute("title");
    }
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
      const cell = cellElement(rowId, columnId);
      if (!cell) continue;
      cell.classList.add("sheet-peer");
      cell.style.outline = `2px solid ${peers[0].color || "var(--blue)"}`;
      cell.style.outlineOffset = "-2px";
      cell.title = peers.map((peer) => peer.name || "Someone").join(", ");
    }
  }

  function focusGrid() {
    if (gridHost) gridHost.focus({ preventScroll: true });
  }

  function cellElement(rowId, columnId) {
    return gridHost.querySelector(`td[data-row-id="${CSS.escape(rowId)}"][data-column-id="${CSS.escape(columnId)}"]`);
  }

  function cellValue(rowId, columnId) {
    const row = state.rows.find((item) => item.id === rowId);
    return row && row.cells && row.cells[columnId] != null ? row.cells[columnId] : "";
  }

  // The grid keeps every row one line tall; the cell bar and dialog show the real line breaks.
  function oneLine(value) {
    return value.replace(/\r?\n/g, " ↵ ");
  }

  function setLocal(rowId, columnId, value) {
    const row = state.rows.find((item) => item.id === rowId);
    if (!row) return;
    row.cells = row.cells || {};
    row.cells[columnId] = value;
  }

  function columnById(columnId) {
    return state.columns.find((item) => item.id === columnId);
  }

  function columnWidth(column) {
    return column.width || DEFAULT_WIDTH;
  }

  function tableWidth() {
    return state.columns.reduce((sum, column) => sum + columnWidth(column), ROW_HEAD_WIDTH);
  }

  function rowIndex(rowId) {
    return state.rows.findIndex((item) => item.id === rowId);
  }

  function columnIndex(columnId) {
    return state.columns.findIndex((item) => item.id === columnId);
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
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
    document.getElementById("deleteRow")?.remove();
    document.getElementById("deleteColumn")?.remove();
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
    // First load selects the top-left cell so the keyboard works right away. Peers are not told.
    const first = !sel && state.rows.length && state.columns.length && document.activeElement === document.body;
    if (first) sel = { rowId: state.rows[0].id, columnId: state.columns[0].id };
    renderGrid();
    if (first) focusGrid();
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
        paintCursors();
        return;
      }
      if (message.type === "presence-leave") {
        state.peers.delete(message.clientId);
        paintCursors();
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

  function resync() {
    const old = ws;
    connect();
    if (old) old.close();
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
        // The grid may already show the rejected change, so load the server state again.
        if (message.error !== "Disconnected") resync();
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
      'Pořadí a šířka: {"op": "move_row", "row": "<id>", "before": "<id řádku>"}, {"op": "move_column", "name": "jméno", "before": "<id sloupce>"}, {"op": "resize_column", "name": "jméno", "width": 200}. Bez before jde na konec.',
      "id řádku je pole id v JSON a první sloupec _id v CSV. id sloupce pro before je v columnIds.",
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
    backdrop.onclick = (event) => { if (event.target === backdrop) close(); };
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
