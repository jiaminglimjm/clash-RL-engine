const canvas = document.getElementById("arena");
const ctx = canvas.getContext("2d");
const remoteShellEl = document.getElementById("remoteShell");
const matchTitleEl = document.getElementById("matchTitle");
const matchSubtitleEl = document.getElementById("matchSubtitle");
const timeLeftBadgeEl = document.getElementById("timeLeftBadge");
const centerOverlayEl = document.getElementById("centerOverlay");
const handRowEl = document.getElementById("handRow");
const nextCardEl = document.getElementById("nextCard");
const elixirBarEl = document.getElementById("elixirBar");
const elixirCountEl = document.getElementById("elixirCount");
const nameInputEl = document.getElementById("nameInput");
const joinBlueBtn = document.getElementById("joinBlueBtn");
const joinRedBtn = document.getElementById("joinRedBtn");
const spectateBtn = document.getElementById("spectateBtn");
const blueSeatEl = document.getElementById("blueSeat");
const redSeatEl = document.getElementById("redSeat");
const deckPanelEl = document.getElementById("deckPanel");
const deckSlotsEl = document.getElementById("deckSlots");
const deckActionsEl = document.getElementById("deckActions");
const readyBtn = document.getElementById("readyBtn");
const newLobbyBtn = document.getElementById("newLobbyBtn");
const readyStatusEl = document.getElementById("readyStatus");
const cardCatalogEl = document.getElementById("cardCatalog");
const historyListEl = document.getElementById("historyList");
const netStatusEl = document.getElementById("netStatus");
const hashStatusEl = document.getElementById("hashStatus");

const TOKEN_KEY = "clashbot.two.token.v1";
const NAME_KEY = "clashbot.two.name.v1";
const ART_EXTENSIONS = ["webp", "png", "jpg", "jpeg"];
const SHOW_CARD_ELIXIR_BADGE = false;

let token = localStorage.getItem(TOKEN_KEY);
if (!token) {
  token = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
  localStorage.setItem(TOKEN_KEY, token);
}

let state = null;
let previousState = null;
let lastStateAt = performance.now();
let selectedSlot = null;
let hoverWorld = null;
let pointerDownOnArena = false;
const coarsePointerQuery = window.matchMedia("(pointer: coarse)");
let localDeck = [];
let localDeckSide = null;
let localDeckKey = "";
let deckDirty = false;
let selectedCatalogCard = null;
let deckSaveTimer = null;
let lastHandKey = "";
let lastDeckRenderKey = "";
let lastHistoryKey = "";
let lastPhase = null;
let lastRttMs = null;
let lastError = "";
let lastErrorAt = 0;
const cardArtSourceCache = new Map();
const missingCardArt = new Set();

const sideColor = {
  blue: "#2867d8",
  red: "#d53a34"
};

nameInputEl.value = localStorage.getItem(NAME_KEY) || "";
nameInputEl.placeholder = "Name";

function isFlipped() {
  return state && state.remote && state.remote.viewerSide === "red";
}

function viewerSide() {
  return state && state.remote ? state.remote.viewerSide : null;
}

function arenaRect() {
  const pad = 30;
  const tile = Math.min((canvas.width - pad * 2) / 18, (canvas.height - pad * 2) / 32);
  const width = tile * 18;
  const height = tile * 32;
  return {
    x: (canvas.width - width) / 2,
    y: (canvas.height - height) / 2,
    w: width,
    h: height,
    tile
  };
}

function tileScreenRect(col, row, w = 1, h = 1) {
  const r = arenaRect();
  if (isFlipped()) {
    return {
      x: r.x + (18 - col - w) * r.tile,
      y: r.y + (32 - row - h) * r.tile,
      w: w * r.tile,
      h: h * r.tile
    };
  }
  return {
    x: r.x + col * r.tile,
    y: r.y + row * r.tile,
    w: w * r.tile,
    h: h * r.tile
  };
}

function worldToScreen(x, y) {
  const r = arenaRect();
  const sx = isFlipped() ? 18 - x : x;
  const sy = isFlipped() ? 32 - y : y;
  return { x: r.x + sx * r.tile, y: r.y + sy * r.tile };
}

function screenToWorld(px, py) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const r = arenaRect();
  const x = (px - rect.left) * scaleX;
  const y = (py - rect.top) * scaleY;
  const localX = Math.max(0, Math.min(18, (x - r.x) / r.tile));
  const localY = Math.max(0, Math.min(32, (y - r.y) / r.tile));
  return {
    x: isFlipped() ? 18 - localX : localX,
    y: isFlipped() ? 32 - localY : localY
  };
}

function snapWorld(pos) {
  return {
    x: Math.floor(Math.max(0, Math.min(17.999, pos.x))) + 0.5,
    y: Math.floor(Math.max(0, Math.min(31.999, pos.y))) + 0.5
  };
}

function tileAtWorld(pos) {
  const row = Math.floor(Math.max(0, Math.min(31.999, pos.y)));
  const col = Math.floor(Math.max(0, Math.min(17.999, pos.x)));
  return state.arena.tiles.find(tile => tile.row === row && tile.col === col);
}

function tileFill(tile) {
  if (tile.bridge) return "#b8874f";
  if (tile.type === "river") return "#5c8fcf";
  if (tile.type === "banned") return "#405145";
  if (tile.type === "princess") return "#acc69e";
  if (tile.type === "crown") return "#a8bea0";
  return "#78b56e";
}

function drawArena() {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!state) return;

  const r = arenaRect();
  ctx.fillStyle = "#d9e4d2";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  for (const tile of state.arena.tiles) {
    const rect = tileScreenRect(tile.col, tile.row);
    ctx.fillStyle = tileFill(tile);
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeStyle = "rgba(25,35,25,0.13)";
    ctx.lineWidth = 1;
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  }

  for (const bridge of state.arena.bridges || []) {
    const rect = tileScreenRect(bridge.x, bridge.y, bridge.w, bridge.h);
    ctx.fillStyle = "rgba(176, 119, 65, 0.62)";
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeStyle = "rgba(88, 55, 28, 0.45)";
    ctx.lineWidth = 2;
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
  }

  drawPlacementMask(r);
  drawPlacementPreview(r);

  ctx.strokeStyle = "rgba(20,30,20,0.5)";
  ctx.lineWidth = 2;
  ctx.strokeRect(r.x, r.y, r.w, r.h);

  const previousProjectiles = byId(previousState ? previousState.projectiles : []);
  for (const projectile of state.projectiles) {
    drawProjectile(interpolated(projectile, previousProjectiles.get(projectile.id)));
  }

  const previousEntities = byId(previousState ? previousState.entities : []);
  for (const entity of state.entities) {
    drawEntity(interpolated(entity, previousEntities.get(entity.id)));
  }
}

function byId(items) {
  const result = new Map();
  for (const item of items || []) result.set(item.id, item);
  return result;
}

function interpolationAlpha() {
  if (!state || !previousState || state.tick <= previousState.tick) return 1;
  const tickRate = state.remote ? state.remote.tickRate : 30;
  const interval = ((state.tick - previousState.tick) / tickRate) * 1000;
  if (interval <= 0) return 1;
  return Math.max(0, Math.min(1, (performance.now() - lastStateAt) / interval));
}

function interpolated(current, previous) {
  if (!previous) return current;
  const alpha = interpolationAlpha();
  return {
    ...current,
    x: previous.x + (current.x - previous.x) * alpha,
    y: previous.y + (current.y - previous.y) * alpha
  };
}

function drawPlacementMask(r) {
  const side = viewerSide();
  const selectedCard = getSelectedCard();
  if (!side || !selectedCard || state.remote.phase !== "running") return;
  ctx.fillStyle = "rgba(205, 38, 38, 0.18)";
  for (const tile of state.arena.tiles) {
    if (isInvalidPlacementTile(side, selectedCard.cardId, tile)) {
      const rect = tileScreenRect(tile.col, tile.row);
      ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    }
  }
}

function drawPlacementPreview(r) {
  const side = viewerSide();
  const selectedCard = getSelectedCard();
  if (!side || !selectedCard || !hoverWorld || state.remote.phase !== "running") return;
  const center = snapWorld(hoverWorld);
  const tile = tileAtWorld(center);
  if (!tile || isInvalidPlacementTile(side, selectedCard.cardId, tile)) return;
  const meta = state.cards[selectedCard.cardId];

  if (meta.kind === "spell" && meta.spellRadius > 0) {
    const p = worldToScreen(center.x, center.y);
    ctx.beginPath();
    ctx.arc(p.x, p.y, meta.spellRadius * r.tile, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(244, 122, 35, 0.18)";
    ctx.fill();
    ctx.strokeStyle = "rgba(244, 122, 35, 0.72)";
    ctx.lineWidth = 2;
    ctx.stroke();
    return;
  }

  const previews = placementPreviews(meta, side, center);
  for (const preview of previews) {
    const p = worldToScreen(preview.x, preview.y);
    ctx.save();
    ctx.globalAlpha = 0.34;
    if (preview.kind === "building") {
      const size = preview.footprint * r.tile;
      ctx.fillStyle = sideColor[side];
      ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.strokeRect(p.x - size / 2, p.y - size / 2, size, size);
    } else {
      const radius = Math.max(8, preview.radius * r.tile);
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = sideColor[side];
      ctx.fill();
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(p.x, p.y, Math.max(4, preview.radius * r.tile * 0.55), 0, Math.PI * 2);
    ctx.fillStyle = meta.secondaryColor;
    ctx.fill();
    ctx.restore();
  }
}

function placementPreviews(meta, side, center) {
  const units = meta.units.length === 1 && meta.formation.length > 1
    ? meta.formation.map(() => meta.units[0])
    : meta.units;
  const formation = meta.formation.length === 1 && units.length > 1
    ? units.map(() => meta.formation[0])
    : meta.formation;
  const mirrorY = side === "red" ? -1 : 1;
  return units.map((unit, index) => {
    const offset = formation[index] || { x: 0, y: 0 };
    return {
      x: Math.max(0.05, Math.min(17.95, center.x + offset.x)),
      y: Math.max(0.05, Math.min(31.95, center.y + offset.y * mirrorY)),
      radius: unit.radius,
      footprint: unit.footprint,
      kind: unit.kind
    };
  });
}

function drawProjectile(projectile) {
  const p = worldToScreen(projectile.x, projectile.y);
  const r = arenaRect();
  if (projectile.cardId === "lightning") {
    drawLightningEffect(projectile, p, r);
    return;
  }
  const isGoblinBarrel = projectile.cardId === "goblin_barrel";
  const radius = isGoblinBarrel
    ? Math.max(9, projectile.radius * r.tile * 1.35)
    : Math.max(4, projectile.radius * r.tile);
  ctx.beginPath();
  ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = isGoblinBarrel
    ? "#34b653"
    : projectile.cardId === "fireball" ? "#f47a23" : sideColor[projectile.side];
  ctx.fill();
  ctx.strokeStyle = isGoblinBarrel ? "#0f6c32" : "rgba(255,255,255,0.9)";
  ctx.lineWidth = isGoblinBarrel ? 2.5 : 1.5;
  ctx.stroke();
  if (isGoblinBarrel) {
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius * 0.45, 0, Math.PI * 2);
    ctx.fillStyle = "#9df0a5";
    ctx.fill();
  }
}

function drawLightningEffect(projectile, center, r) {
  const radius = Math.max(4, projectile.radius * r.tile);
  ctx.save();
  ctx.beginPath();
  ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
  ctx.strokeStyle = projectile.effectDone ? "rgba(255,255,255,0.45)" : "rgba(255,255,255,0.88)";
  ctx.lineWidth = projectile.effectDone ? 1.5 : 2;
  ctx.setLineDash(projectile.effectDone ? [] : [6, 5]);
  ctx.stroke();
  ctx.restore();

  const targets = projectile.visualTargets || [];
  for (let index = 0; index < targets.length; index += 1) {
    const target = targets[index];
    const end = worldToScreen(target.x, target.y);
    const latest = index === targets.length - 1 && !projectile.effectDone;
    const alpha = latest ? 1 : 0.72;
    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.shadowColor = "rgba(80, 205, 255, 0.95)";
    ctx.shadowBlur = latest ? 14 : 8;
    ctx.strokeStyle = `rgba(63, 181, 255, ${0.55 * alpha})`;
    ctx.lineWidth = latest ? 8 : 6;
    ctx.beginPath();
    ctx.moveTo(end.x, end.y - r.tile * 2.8);
    ctx.lineTo(end.x - 7, end.y - r.tile * 1.65);
    ctx.lineTo(end.x + 5, end.y - r.tile * 0.85);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();

    ctx.strokeStyle = "#eaf7ff";
    ctx.lineWidth = latest ? 4 : 3;
    ctx.shadowBlur = latest ? 10 : 5;
    ctx.beginPath();
    ctx.moveTo(end.x, end.y - r.tile * 2.8);
    ctx.lineTo(end.x - 7, end.y - r.tile * 1.65);
    ctx.lineTo(end.x + 5, end.y - r.tile * 0.85);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
    ctx.strokeStyle = "rgba(94, 197, 255, 0.9)";
    ctx.lineWidth = 2;
    ctx.shadowBlur = 0;
    ctx.beginPath();
    ctx.moveTo(end.x + 5, end.y - r.tile * 2.0);
    ctx.lineTo(end.x - 3, end.y - r.tile * 1.35);
    ctx.lineTo(end.x + 4, end.y - r.tile * 0.7);
    ctx.stroke();
    ctx.restore();
  }
}

function drawEntity(entity) {
  const p = worldToScreen(entity.x, entity.y);
  const r = arenaRect();
  const radius = Math.max(8, entity.radius * r.tile);
  const footprint = Math.max(radius * 2, entity.footprint * r.tile);
  const alpha = entity.deployTicks > 0 ? 0.52 : 1;
  const hitAge = entity.lastHitTick === null ? 999 : state.tick - entity.lastHitTick;
  const hitBlink = hitAge >= 0 && hitAge < 6;

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = sideColor[entity.side];
  ctx.lineWidth = hitBlink ? 5 : entity.kind === "tower" ? 3 : 2;
  ctx.strokeStyle = hitBlink ? "#ffe66b" : entity.active === false ? "#303a2e" : "#ffffff";
  if (entity.kind === "building") {
    ctx.fillRect(p.x - footprint / 2, p.y - footprint / 2, footprint, footprint);
    ctx.strokeRect(p.x - footprint / 2, p.y - footprint / 2, footprint, footprint);
  } else {
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.arc(p.x, p.y, Math.max(4, radius * 0.55), 0, Math.PI * 2);
  ctx.fillStyle = entity.secondaryColor || "#eeeeee";
  ctx.fill();
  ctx.strokeStyle = "rgba(0,0,0,0.28)";
  ctx.stroke();
  ctx.restore();

  const hpRatio = Math.max(0, entity.hp / entity.maxHp);
  const barW = Math.max(22, radius * 2.2);
  const barH = entity.kind === "tower" ? 8 : 4;
  const barY = healthBarY(entity, p, radius, barH);
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(p.x - barW / 2, barY, barW, barH);
  ctx.fillStyle = entity.side === "blue" ? "#4d91ff" : "#ef817a";
  ctx.fillRect(p.x - barW / 2, barY, barW * hpRatio, barH);

  drawFacingArrow(entity, p, radius);

  ctx.font = `${Math.max(10, Math.min(14, radius * 0.72))}px ui-sans-serif, system-ui`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(255,255,255,0.88)";
  ctx.fillStyle = "#102010";
  const label = entity.kind === "tower" ? String(entity.hp) : entity.label;
  const labelY = labelYForEntity(entity, p, radius, barY, barH);
  ctx.strokeText(label, p.x, labelY);
  ctx.fillText(label, p.x, labelY);
}

function healthBarY(entity, p, radius, barH) {
  if (entity.kind !== "tower") return p.y - radius - barH - 5;
  if (towerInfoBelow(entity)) return p.y + radius + 6;
  return p.y - radius - barH - 6;
}

function labelYForEntity(entity, p, radius, barY, barH) {
  if (entity.kind !== "tower") return p.y + radius + 10;
  return towerInfoBelow(entity) ? barY + barH + 9 : barY - 7;
}

function towerInfoBelow(entity) {
  if (entity.kind !== "tower") return false;
  const side = viewerSide();
  if (side) return entity.side === side;
  return entity.side === "blue";
}

function drawFacingArrow(entity, p, radius) {
  if (!entity.facing) return;
  const dx = isFlipped() ? -entity.facing.x : entity.facing.x;
  const dy = isFlipped() ? -entity.facing.y : entity.facing.y;
  if (Math.abs(dx) + Math.abs(dy) < 0.01) return;
  const start = radius * 0.15;
  const end = radius * 1.28;
  const sx = p.x + dx * start;
  const sy = p.y + dy * start;
  const ex = p.x + dx * end;
  const ey = p.y + dy * end;
  ctx.strokeStyle = "rgba(15, 25, 18, 0.72)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(sx, sy);
  ctx.lineTo(ex, ey);
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(ex, ey);
  ctx.lineTo(ex - dx * 5 - dy * 3, ey - dy * 5 + dx * 3);
  ctx.lineTo(ex - dx * 5 + dy * 3, ey - dy * 5 - dx * 3);
  ctx.closePath();
  ctx.fillStyle = "rgba(15, 25, 18, 0.72)";
  ctx.fill();
}

function renderPanel() {
  if (!state) return;
  remoteShellEl.classList.toggle("lobby-mode", state.remote.phase === "lobby");
  renderMatchBar();
  renderSeats();
  renderHand();
  renderDeckEditor();
  renderHistory();
  renderNet();
}

function renderMatchBar() {
  const remote = state.remote;
  const phase = remote.phase;

  if (phase === "ended") {
    matchTitleEl.textContent = state.game.winner ? `${capitalize(state.game.winner)} wins` : "Draw";
  } else if (phase === "lobby") {
    matchTitleEl.textContent = "Lobby";
  } else {
    matchTitleEl.textContent = state.game.phase === "sudden_death" ? "Sudden Death" : "Live";
  }

  const side = viewerSide();
  const sideText = side ? `${capitalize(side)} view` : "Spectator";
  matchSubtitleEl.textContent = `${sideText}  t=${state.tick}  ${state.seconds.toFixed(1)}s`;

  const overlay = overlayText();
  centerOverlayEl.textContent = overlay;
  centerOverlayEl.classList.toggle("visible", Boolean(overlay));
  renderTimeLeft();
}

function overlayText() {
  if (lastError && performance.now() - lastErrorAt < 3200) return lastError;
  if (!state) return "";
  if (!viewerSide() && state.remote.phase === "lobby") return "Join a seat";
  if (state.remote.phase === "lobby") {
    const own = state.remote.players[viewerSide()];
    if (own && own.ready) return "Waiting";
    return "Deck";
  }
  if (state.remote.phase === "ended") {
    const crowns = crownScore();
    if (!state.game.winner) return `Draw ${crowns.blue}-${crowns.red}`;
    return `${capitalize(state.game.winner)} ${crowns.blue}-${crowns.red}`;
  }
  return "";
}

function crownScore() {
  const living = { blue: new Set(), red: new Set() };
  for (const entity of state.entities || []) {
    if (entity.kind === "tower" && entity.towerRole) {
      living[entity.side].add(entity.towerRole);
    }
  }
  return {
    blue: crownsFor("blue", living.red),
    red: crownsFor("red", living.blue)
  };
}

function crownsFor(side, enemyRoles) {
  if (!enemyRoles.has("king")) return 3;
  return Math.max(0, Math.min(3, 3 - enemyRoles.size));
}

function renderTimeLeft() {
  const timeState = timeLeftState();
  timeLeftBadgeEl.innerHTML = "";
  const label = document.createElement("span");
  label.className = "time-label";
  label.textContent = timeState.label;
  const value = document.createElement("span");
  value.className = "time-value";
  value.textContent = timeState.value;
  timeLeftBadgeEl.appendChild(label);
  timeLeftBadgeEl.appendChild(value);
  if (timeState.multiplier > 1) {
    const badge = document.createElement("span");
    badge.className = "multiplier-badge";
    badge.textContent = `${timeState.multiplier}x`;
    timeLeftBadgeEl.appendChild(badge);
  }
}

function timeLeftState() {
  if (!state || !state.game) return { label: "Time left:", value: "3:00", multiplier: 1 };
  if (state.game.over) return { label: "Game over:", value: "0:00", multiplier: 1 };
  const tickRate = state.remote ? state.remote.tickRate : 30;
  const suddenDeathTick = state.game.suddenDeathTick || 0;
  const matchEndTick = state.game.matchEndTick || suddenDeathTick;
  const inOvertime = state.tick >= suddenDeathTick;
  const targetTick = inOvertime ? matchEndTick : suddenDeathTick;
  const remainingTicks = Math.max(0, targetTick - state.tick);
  return {
    label: inOvertime ? "Overtime:" : "Time left:",
    value: formatDuration(remainingTicks / tickRate),
    multiplier: state.game.elixirMultiplier || 1
  };
}

function renderSeats() {
  renderSeat("blue", blueSeatEl);
  renderSeat("red", redSeatEl);
  const blue = state.remote.players.blue;
  const red = state.remote.players.red;
  joinBlueBtn.disabled = Boolean(blue.occupied && !blue.takeoverAvailable && !blue.isYou);
  joinRedBtn.disabled = Boolean(red.occupied && !red.takeoverAvailable && !red.isYou);
  joinBlueBtn.classList.toggle("active-side", Boolean(blue.isYou));
  joinRedBtn.classList.toggle("active-side", Boolean(red.isYou));
}

function renderSeat(side, container) {
  const player = state.remote.players[side];
  const status = player.isYou
    ? "You"
    : player.ready
      ? "Ready"
      : player.connected
        ? "Online"
        : player.occupied
          ? "Away"
          : "Open";
  container.innerHTML = `
    <div>
      <div class="seat-name">${escapeHtml(player.name)}</div>
      <div class="seat-meta">${capitalize(side)} ${player.occupied && player.lastSeenSeconds !== null ? `${player.lastSeenSeconds}s` : ""}</div>
    </div>
    <div class="seat-badge">${status}</div>
  `;
  container.classList.toggle("is-you", Boolean(player.isYou));
}

function renderHand() {
  const side = viewerSide();
  const phase = state.remote.phase;
  const hand = side ? state.players[side].hand : [];
  const next = side ? state.players[side].nextCard : null;
  renderElixirBar(side);
  const key = [
    side || "spectator",
    phase,
    selectedSlot === null ? "none" : selectedSlot,
    hand.map(card => card.cardId).join(","),
    next ? next.cardId : "none"
  ].join("|");
  if (key === lastHandKey) {
    updateHandAffordability();
    return;
  }
  lastHandKey = key;

  handRowEl.innerHTML = "";
  if (!side) {
    for (let i = 0; i < 4; i++) {
      const empty = document.createElement("div");
      empty.className = "empty-slot";
      handRowEl.appendChild(empty);
    }
    nextCardEl.innerHTML = "";
    updateHandAffordability();
    return;
  }

  for (const card of hand) {
    const button = makeCard(card.cardId, "battle-card", "");
    button.dataset.slot = String(card.slot);
    button.classList.toggle("selected", selectedSlot === card.slot);
    button.disabled = phase !== "running";
    button.addEventListener("click", () => {
      selectedSlot = card.slot;
      lastHandKey = "";
      renderHand();
    });
    handRowEl.appendChild(button);
  }

  nextCardEl.innerHTML = "";
  if (next) {
    const card = makeCard(next.cardId, "mini-card", "");
    card.disabled = true;
    nextCardEl.appendChild(card);
  }
  updateHandAffordability();
}

function updateHandAffordability() {
  const side = viewerSide();
  if (!side || !state) return;
  const elixir = state.players[side].elixir;
  for (const button of handRowEl.querySelectorAll(".battle-card")) {
    const cardId = button.dataset.cardId;
    const slot = Number(button.dataset.slot);
    const meta = state.cards[cardId];
    button.classList.toggle("selected", selectedSlot === slot);
    button.classList.toggle("unaffordable", Boolean(meta && elixir < meta.elixir));
  }
}

function renderElixirBar(side) {
  const elixir = side && state ? state.players[side].elixir : 0;
  const clamped = Math.max(0, Math.min(10, elixir));
  elixirBarEl.innerHTML = "";
  for (let i = 0; i < 10; i++) {
    const fill = Math.max(0, Math.min(1, clamped - i));
    const percent = Math.round(fill * 100);
    const segment = document.createElement("span");
    segment.className = "elixir-segment";
    segment.style.background = `linear-gradient(90deg, #ff80c8 0%, #a65bd8 ${percent}%, #ead7ee ${percent}%, #ead7ee 100%)`;
    elixirBarEl.appendChild(segment);
  }
  elixirCountEl.textContent = elixir.toFixed(1);
}

function renderDeckEditor() {
  const side = viewerSide();
  const phase = state.remote.phase;
  deckPanelEl.hidden = !side || phase === "running";
  if (!side || phase === "running") {
    readyStatusEl.textContent = "";
    return;
  }
  deckPanelEl.classList.toggle("ended-only", phase === "ended");
  deckActionsEl.classList.toggle("lobby-ready", phase === "lobby");
  deckActionsEl.classList.toggle("ended-actions", phase === "ended");

  syncLocalDeck();
  const player = state.remote.players[side];
  renderReadyStatus(side, phase, player);
  const canEdit = phase !== "running";
  const deckKey = [
    side,
    phase,
    deckDirty,
    player.ready,
    selectedCatalogCard || "none",
    localDeck.join(","),
    state.remote.cardCatalog.map(card => card.cardId).join(",")
  ].join("|");
  if (deckKey === lastDeckRenderKey) return;
  lastDeckRenderKey = deckKey;

  deckSlotsEl.innerHTML = "";
  for (let i = 0; i < 8; i++) {
    const cardId = localDeck[i];
    if (!cardId) {
      const empty = document.createElement("div");
      empty.className = "empty-slot";
      empty.textContent = String(i + 1);
      deckSlotsEl.appendChild(empty);
      empty.addEventListener("click", () => {
        if (!canEdit || !selectedCatalogCard) return;
        setDeckSlot(i, selectedCatalogCard);
      });
      continue;
    }
    const button = makeCard(cardId, "mini-card", "");
    button.disabled = !canEdit;
    button.addEventListener("click", () => {
      if (!canEdit) return;
      if (selectedCatalogCard) {
        setDeckSlot(i, selectedCatalogCard);
      } else {
        localDeck.splice(i, 1);
        deckDirty = true;
        lastDeckRenderKey = "";
        renderDeckEditor();
        scheduleDeckAutoSave();
      }
    });
    deckSlotsEl.appendChild(button);
  }

  readyBtn.disabled = !canEdit || localDeck.length !== 8;
  readyBtn.textContent = player.ready ? "Unready" : "Ready";
  readyBtn.hidden = phase === "ended";
  readyBtn.classList.toggle("primary-ready", phase === "lobby");
  newLobbyBtn.hidden = phase !== "ended";
  newLobbyBtn.textContent = "Next Match";
  newLobbyBtn.classList.toggle("primary-ready", phase === "ended");

  cardCatalogEl.innerHTML = "";
  for (const card of state.remote.cardCatalog) {
    const active = localDeck.includes(card.cardId);
    if (active) continue;
    const button = makeCard(card.cardId, "mini-card catalog-card", "");
    button.classList.toggle("selected-pool", selectedCatalogCard === card.cardId);
    button.disabled = !canEdit;
    button.addEventListener("click", () => selectCatalogCard(card.cardId));
    cardCatalogEl.appendChild(button);
  }
}

function renderReadyStatus(side, phase, player) {
  if (phase !== "lobby" || !player.ready) {
    readyStatusEl.textContent = "";
    return;
  }
  const opponentSide = side === "blue" ? "red" : "blue";
  const opponent = state.remote.players[opponentSide];
  const waitingFor = opponent && opponent.connected ? opponent.name : "opponent";
  readyStatusEl.textContent = `waiting for ${waitingFor}...`;
}

function selectCatalogCard(cardId) {
  if (!viewerSide() || state.remote.phase === "running") return;
  selectedCatalogCard = selectedCatalogCard === cardId ? null : cardId;
  lastDeckRenderKey = "";
  renderDeckEditor();
}

function setDeckSlot(index, cardId) {
  if (!viewerSide() || state.remote.phase === "running") return;
  localDeck = localDeck.filter(existing => existing !== cardId);
  if (index >= localDeck.length) {
    localDeck.push(cardId);
  } else {
    localDeck[index] = cardId;
  }
  localDeck = localDeck.slice(0, 8);
  selectedCatalogCard = null;
  deckDirty = true;
  lastDeckRenderKey = "";
  renderDeckEditor();
  scheduleDeckAutoSave();
}

function syncLocalDeck() {
  const side = viewerSide();
  if (!side) {
    localDeck = [];
    localDeckSide = null;
    localDeckKey = "";
    deckDirty = false;
    selectedCatalogCard = null;
    return;
  }
  const serverDeck = state.remote.players[side].deck;
  const serverKey = `${side}:${serverDeck.join(",")}`;
  if (localDeckSide !== side || (!deckDirty && localDeckKey !== serverKey)) {
    localDeck = [...serverDeck];
    localDeckSide = side;
    localDeckKey = serverKey;
    deckDirty = false;
    if (selectedCatalogCard && localDeck.includes(selectedCatalogCard)) selectedCatalogCard = null;
    lastDeckRenderKey = "";
  }
}

function renderHistory() {
  const records = state.remote.history || [];
  const historyKey = JSON.stringify(records.map(historyRecordKey));
  if (historyKey === lastHistoryKey) return;
  lastHistoryKey = historyKey;
  historyListEl.innerHTML = "";
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "history-meta";
    empty.textContent = "No games yet";
    historyListEl.appendChild(empty);
    return;
  }
  for (const record of records) {
    const item = document.createElement("div");
    item.className = "history-item";
    const title = document.createElement("div");
    title.className = "history-result";
    const winner = historyWinnerLabel(record);
    const crowns = record.outcome && record.outcome.crowns ? record.outcome.crowns : { blue: 0, red: 0 };
    const winnerEl = document.createElement("span");
    winnerEl.textContent = winner;
    const scoreEl = document.createElement("span");
    scoreEl.textContent = `${crowns.blue}-${crowns.red}`;
    title.appendChild(winnerEl);
    title.appendChild(scoreEl);
    const meta = document.createElement("div");
    meta.className = "history-meta";
    const when = new Date(record.endedAt).toLocaleString();
    meta.textContent = `${when}  ${formatDuration(record.durationSeconds)}`;
    item.appendChild(title);
    item.appendChild(meta);
    item.appendChild(makeHistoryDeckSummary(record));
    historyListEl.appendChild(item);
  }
}

function historyRecordKey(record) {
  const crowns = record.outcome && record.outcome.crowns ? record.outcome.crowns : {};
  return {
    matchId: record.matchId,
    endedAt: record.endedAt,
    winner: record.outcome ? record.outcome.winner : null,
    crowns: { blue: crowns.blue, red: crowns.red },
    players: record.players || {},
    decks: record.decks || {}
  };
}

function historyWinnerLabel(record) {
  const winnerSide = record.outcome ? record.outcome.winner : null;
  if (!winnerSide) return "Draw";
  const name = record.players && record.players[winnerSide] ? record.players[winnerSide] : capitalize(winnerSide);
  return `${name} Wins!`;
}

function makeHistoryDeckSummary(record) {
  const wrapper = document.createElement("div");
  wrapper.className = "history-decks";
  for (const side of ["blue", "red"]) {
    const row = document.createElement("div");
    row.className = `history-deck-row ${side}-history`;
    const label = document.createElement("div");
    label.className = "history-player";
    label.textContent = record.players && record.players[side] ? record.players[side] : capitalize(side);
    const cards = document.createElement("div");
    cards.className = "history-deck-cards";
    for (const cardId of (record.decks && record.decks[side] ? record.decks[side] : [])) {
      cards.appendChild(makeHistoryDeckCard(cardId));
    }
    row.appendChild(label);
    row.appendChild(cards);
    wrapper.appendChild(row);
  }
  return wrapper;
}

function makeHistoryDeckCard(cardId) {
  const meta = state.cards[cardId];
  const card = document.createElement("span");
  card.className = "history-card";
  card.title = meta ? meta.name : cardId;
  card.style.background = `linear-gradient(160deg, #f7f7f0, ${meta && meta.secondaryColor ? meta.secondaryColor : "#dde6d8"})`;
  const img = makeArtImage(cardId);
  if (img) card.appendChild(img);
  const tint = document.createElement("span");
  tint.className = "card-tint";
  card.appendChild(tint);
  const name = document.createElement("span");
  name.className = "history-card-name";
  name.textContent = meta ? meta.name : cardId;
  card.appendChild(name);
  return card;
}

function renderNet() {
  const revision = state.remote.revision;
  const delay = state.net.placementDelayTicks;
  const rtt = lastRttMs === null ? "" : `  poll ${lastRttMs}ms`;
  netStatusEl.textContent = `rev ${revision}  place ${delay} ticks${rtt}`;
  hashStatusEl.textContent = state.stateHash ? state.stateHash.slice(0, 16) : "";
}

function makeCard(cardId, className, hotkey) {
  const meta = state.cards[cardId];
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.dataset.cardId = cardId;
  button.style.background = `linear-gradient(160deg, #f7f7f0, ${meta.secondaryColor || "#dde6d8"})`;
  const img = makeArtImage(cardId);
  if (img) button.appendChild(img);
  const tint = document.createElement("span");
  tint.className = "card-tint";
  button.appendChild(tint);
  if (hotkey !== "") {
    const key = document.createElement("span");
    key.className = "card-hotkey";
    key.textContent = String(hotkey);
    button.appendChild(key);
  }
  if (SHOW_CARD_ELIXIR_BADGE) {
    const cost = document.createElement("span");
    cost.className = "card-cost";
    cost.textContent = String(meta.elixir);
    button.appendChild(cost);
  }
  const name = document.createElement("span");
  name.className = "card-name";
  name.textContent = meta.name;
  button.appendChild(name);
  return button;
}

function makeArtImage(cardId) {
  if (missingCardArt.has(cardId)) return null;
  const img = document.createElement("img");
  img.className = "card-art";
  img.alt = "";
  const cachedSource = cardArtSourceCache.get(cardId);
  let index = cachedSource ? ART_EXTENSIONS.indexOf(cachedSource.split(".").pop()) : 0;
  if (index < 0) index = 0;
  img.onload = () => {
    cardArtSourceCache.set(cardId, img.src);
  };
  img.onerror = () => {
    index += 1;
    if (index >= ART_EXTENSIONS.length) {
      missingCardArt.add(cardId);
      img.remove();
      return;
    }
    img.src = `/two/card-art/${cardId}.${ART_EXTENSIONS[index]}`;
  };
  img.src = cachedSource || `/two/card-art/${cardId}.${ART_EXTENSIONS[index]}`;
  return img;
}

function getSelectedCard() {
  const side = viewerSide();
  if (!state || !side || selectedSlot === null) return null;
  return state.players[side].hand.find(card => card.slot === selectedSlot) || null;
}

function isInvalidPlacementTile(side, cardId, tile) {
  const meta = state.cards[cardId];
  if (!meta || tile.type === "banned") return true;
  if (meta.kind === "spell") return false;

  let placeable = !["river", "princess", "crown"].includes(tile.type);
  const ownAlive = princessAlive(side);
  const enemyAlive = princessAlive(side === "blue" ? "red" : "blue");

  if (side === "blue") {
    if (tile.type === "princess" && tile.col < 9 && !ownAlive.left) placeable = true;
    if (tile.type === "princess" && tile.col > 8 && !ownAlive.right) placeable = true;
    if (tile.row < 11) placeable = false;
    if (enemyAlive.left && tile.row < 17 && tile.col < 9) placeable = false;
    if (enemyAlive.right && tile.row < 17 && tile.col > 8) placeable = false;
    return !placeable;
  }

  if (tile.type === "princess" && tile.col < 9 && !ownAlive.left) placeable = true;
  if (tile.type === "princess" && tile.col > 8 && !ownAlive.right) placeable = true;
  if (tile.row > 20) placeable = false;
  if (enemyAlive.left && tile.row > 14 && tile.col < 9) placeable = false;
  if (enemyAlive.right && tile.row > 14 && tile.col > 8) placeable = false;
  return !placeable;
}

function princessAlive(side) {
  return {
    left: state.entities.some(entity => entity.side === side && entity.kind === "tower" && entity.towerRole === "left_princess"),
    right: state.entities.some(entity => entity.side === side && entity.kind === "tower" && entity.towerRole === "right_princess")
  };
}

async function pollLoop() {
  while (true) {
    const since = state && state.remote ? state.remote.revision : 0;
    const started = performance.now();
    try {
      const response = await fetch(`/api/two/state?token=${encodeURIComponent(token)}&since=${since}`, {
        cache: "no-store"
      });
      if (!response.ok) throw new Error(await response.text());
      const nextState = await response.json();
      lastRttMs = Math.round(performance.now() - started);
      receiveState(nextState);
    } catch (error) {
      showError(String(error));
      await sleep(850);
    }
  }
}

function receiveState(nextState) {
  const previousPhase = lastPhase;
  previousState = state;
  state = nextState;
  lastPhase = state && state.remote ? state.remote.phase : null;
  lastStateAt = performance.now();
  if (selectedSlot !== null && (selectedSlot < 0 || selectedSlot > 3)) selectedSlot = null;
  renderPanel();
  if (previousPhase && previousPhase !== "running" && lastPhase === "running") {
    scrollToArena();
  }
}

function scrollToArena() {
  window.requestAnimationFrame(() => {
    const playArea = document.querySelector(".play-area");
    if (playArea) {
      playArea.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, token })
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  receiveState(data.state || data);
  return data;
}

canvas.addEventListener("click", async event => {
  const side = viewerSide();
  const selectedCard = getSelectedCard();
  if (!state || !side || !selectedCard || state.remote.phase !== "running") return;
  const world = snapWorld(screenToWorld(event.clientX, event.clientY));
  const tile = tileAtWorld(world);
  if (!tile || isInvalidPlacementTile(side, selectedCard.cardId, tile)) return;
  try {
    await postJson("/api/two/play", {
      handSlot: selectedSlot,
      x: world.x,
      y: world.y,
      clientTick: state.tick
    });
    selectedSlot = null;
    hoverWorld = null;
    lastHandKey = "";
    renderHand();
  } catch (error) {
    showError(String(error.message || error));
  }
});

canvas.addEventListener("pointerdown", event => {
  pointerDownOnArena = true;
  hoverWorld = screenToWorld(event.clientX, event.clientY);
});

canvas.addEventListener("pointermove", event => {
  if (coarsePointerQuery.matches && !pointerDownOnArena) return;
  hoverWorld = screenToWorld(event.clientX, event.clientY);
});

canvas.addEventListener("pointerup", () => {
  pointerDownOnArena = false;
  if (coarsePointerQuery.matches) hoverWorld = null;
});

canvas.addEventListener("pointercancel", () => {
  pointerDownOnArena = false;
  hoverWorld = null;
});

canvas.addEventListener("pointerleave", () => {
  pointerDownOnArena = false;
  hoverWorld = null;
});

document.addEventListener("keydown", event => {
  if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const key = Number(event.key);
  if (!Number.isInteger(key) || key < 1 || key > 4) return;
  selectedSlot = key - 1;
  lastHandKey = "";
  renderHand();
});

joinBlueBtn.addEventListener("click", () => joinSide("blue"));
joinRedBtn.addEventListener("click", () => joinSide("red"));
spectateBtn.addEventListener("click", () => {
  postJson("/api/two/leave", {}).catch(error => showError(String(error.message || error)));
});

readyBtn.addEventListener("click", async () => {
  try {
    if (deckDirty) await submitDeck();
    const side = viewerSide();
    const ready = !state.remote.players[side].ready;
    await postJson("/api/two/ready", { ready });
  } catch (error) {
    showError(String(error.message || error));
  }
});

newLobbyBtn.addEventListener("click", async () => {
  try {
    deckDirty = false;
    await postJson("/api/two/new-lobby", {});
  } catch (error) {
    showError(String(error.message || error));
  }
});

nameInputEl.addEventListener("change", () => {
  localStorage.setItem(NAME_KEY, nameInputEl.value.trim());
});

async function joinSide(side) {
  const name = nameInputEl.value.trim() || (side === "blue" ? "Player 1" : "Player 2");
  localStorage.setItem(NAME_KEY, name);
  try {
    await postJson("/api/two/join", { side, name });
  } catch (error) {
    showError(String(error.message || error));
  }
}

function scheduleDeckAutoSave() {
  if (deckSaveTimer !== null) {
    clearTimeout(deckSaveTimer);
    deckSaveTimer = null;
  }
  if (!viewerSide() || state.remote.phase === "running" || localDeck.length !== 8) return;
  deckSaveTimer = setTimeout(async () => {
    deckSaveTimer = null;
    try {
      await submitDeck();
    } catch (error) {
      showError(String(error.message || error));
    }
  }, 280);
}

async function submitDeck() {
  if (localDeck.length !== 8) throw new Error("Deck needs 8 cards");
  if (deckSaveTimer !== null) {
    clearTimeout(deckSaveTimer);
    deckSaveTimer = null;
  }
  await postJson("/api/two/deck", { deck: localDeck });
  deckDirty = false;
  localDeckKey = `${viewerSide()}:${localDeck.join(",")}`;
  lastDeckRenderKey = "";
  renderPanel();
}

function showError(message) {
  lastError = message.replace(/^Error:\s*/, "");
  lastErrorAt = performance.now();
  renderPanel();
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function capitalize(value) {
  if (!value) return "";
  return value[0].toUpperCase() + value.slice(1);
}

function formatDuration(seconds) {
  const total = Math.round(seconds || 0);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function frame() {
  drawArena();
  requestAnimationFrame(frame);
}

pollLoop();
frame();
