const canvas = document.getElementById("arena");
const ctx = canvas.getContext("2d");
const blueHandEl = document.getElementById("blueHand");
const redHandEl = document.getElementById("redHand");
const logEl = document.getElementById("log");
const clockEl = document.getElementById("clock");
const statusEl = document.getElementById("status");
const blueElixirEl = document.getElementById("blueElixir");
const redElixirEl = document.getElementById("redElixir");
const netlineEl = document.getElementById("netline");
const pauseBtn = document.getElementById("pauseBtn");
const stepBtn = document.getElementById("stepBtn");
const resetBtn = document.getElementById("resetBtn");

let state = null;
let selected = null;
let hoverWorld = null;
let paused = false;
let lastHandsKey = "";

const sideColor = {
  blue: "#2867d8",
  red: "#d53a34"
};

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

function worldToScreen(x, y) {
  const r = arenaRect();
  return { x: r.x + x * r.tile, y: r.y + y * r.tile };
}

function screenToWorld(px, py) {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const r = arenaRect();
  const x = (px - rect.left) * scaleX;
  const y = (py - rect.top) * scaleY;
  return {
    x: Math.max(0, Math.min(18, (x - r.x) / r.tile)),
    y: Math.max(0, Math.min(32, (y - r.y) / r.tile))
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
    ctx.fillStyle = tileFill(tile);
    ctx.fillRect(r.x + tile.col * r.tile, r.y + tile.row * r.tile, r.tile, r.tile);
    ctx.strokeStyle = "rgba(25,35,25,0.13)";
    ctx.lineWidth = 1;
    ctx.strokeRect(r.x + tile.col * r.tile, r.y + tile.row * r.tile, r.tile, r.tile);
  }

  for (const bridge of state.arena.bridges || []) {
    ctx.fillStyle = "rgba(176, 119, 65, 0.62)";
    ctx.fillRect(r.x + bridge.x * r.tile, r.y + bridge.y * r.tile, bridge.w * r.tile, bridge.h * r.tile);
    ctx.strokeStyle = "rgba(88, 55, 28, 0.45)";
    ctx.lineWidth = 2;
    ctx.strokeRect(r.x + bridge.x * r.tile, r.y + bridge.y * r.tile, bridge.w * r.tile, bridge.h * r.tile);
  }

  drawPlacementMask(r);
  drawPlacementPreview(r);

  ctx.strokeStyle = "rgba(20,30,20,0.5)";
  ctx.lineWidth = 2;
  ctx.strokeRect(r.x, r.y, r.w, r.h);

  for (const projectile of state.projectiles) {
    drawProjectile(projectile);
  }

  drawHoveredRanges();

  for (const entity of state.entities) {
    drawEntity(entity);
  }
}

function drawPlacementMask(r) {
  const selectedCard = getSelectedCard();
  if (!selectedCard) return;
  ctx.fillStyle = "rgba(205, 38, 38, 0.18)";
  for (const tile of state.arena.tiles) {
    if (isInvalidPlacementTile(selected.side, selectedCard.cardId, tile)) {
      ctx.fillRect(r.x + tile.col * r.tile, r.y + tile.row * r.tile, r.tile, r.tile);
    }
  }
}

function drawPlacementPreview(r) {
  const selectedCard = getSelectedCard();
  if (!selectedCard || !hoverWorld) return;
  const center = snapWorld(hoverWorld);
  const tile = tileAtWorld(center);
  if (!tile || isInvalidPlacementTile(selected.side, selectedCard.cardId, tile)) return;
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

  const previews = placementPreviews(meta, selected.side, center);
  for (const preview of previews) {
    const p = worldToScreen(preview.x, preview.y);
    ctx.save();
    ctx.globalAlpha = 0.34;
    if (preview.kind === "building") {
      const size = preview.footprint * r.tile;
      ctx.fillStyle = sideColor[selected.side];
      ctx.fillRect(p.x - size / 2, p.y - size / 2, size, size);
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 2;
      ctx.strokeRect(p.x - size / 2, p.y - size / 2, size, size);
    } else {
      const radius = Math.max(8, preview.radius * r.tile);
      ctx.beginPath();
      ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = sideColor[selected.side];
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
  ctx.beginPath();
  ctx.arc(p.x, p.y, Math.max(4, projectile.radius * r.tile), 0, Math.PI * 2);
  ctx.fillStyle = projectile.cardId === "fireball" ? "#f47a23" : sideColor[projectile.side];
  ctx.fill();
  ctx.strokeStyle = "rgba(255,255,255,0.9)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
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

  if (!projectile.effectDone) return;
  const targets = projectile.visualTargets || [];
  for (const target of targets) {
    const end = worldToScreen(target.x, target.y);
    ctx.save();
    ctx.strokeStyle = "#eaf7ff";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(end.x, end.y - r.tile * 2.2);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
    ctx.strokeStyle = "rgba(94, 197, 255, 0.9)";
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(end.x - 4, end.y - r.tile * 1.2);
    ctx.lineTo(end.x + 3, end.y - r.tile * 0.55);
    ctx.lineTo(end.x - 2, end.y);
    ctx.stroke();
    ctx.restore();
  }
}

function hoveredEntity() {
  if (!state || !hoverWorld) return null;
  const r = arenaRect();
  for (let index = state.entities.length - 1; index >= 0; index -= 1) {
    const entity = state.entities[index];
    const dx = hoverWorld.x - entity.x;
    const dy = hoverWorld.y - entity.y;
    const hoverRadius = Math.max(entity.radius || 0, entity.footprint ? entity.footprint / 2 : 0, 8 / r.tile);
    if (Math.hypot(dx, dy) <= hoverRadius) return entity;
  }
  return null;
}

function drawHoveredRanges() {
  const entity = hoveredEntity();
  if (!entity) return;
  const r = arenaRect();
  const p = worldToScreen(entity.x, entity.y);

  drawRangeCircle(p, entity.sightRange, r.tile, {
    alpha: 0.72,
    dash: [4, 5],
    width: 1
  });
  drawRangeCircle(p, entity.attackRange, r.tile, {
    alpha: 0.9,
    dash: [],
    width: 1.25
  });
}

function drawRangeCircle(center, rangeTiles, tileSize, style) {
  if (!Number.isFinite(rangeTiles) || rangeTiles <= 0) return;
  ctx.save();
  ctx.beginPath();
  ctx.arc(center.x, center.y, rangeTiles * tileSize, 0, Math.PI * 2);
  ctx.strokeStyle = `rgba(255,255,255,${style.alpha})`;
  ctx.lineWidth = style.width;
  ctx.setLineDash(style.dash);
  ctx.stroke();
  ctx.restore();
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
  ctx.fillStyle = "rgba(0,0,0,0.35)";
  ctx.fillRect(p.x - barW / 2, p.y - radius - barH - 5, barW, barH);
  ctx.fillStyle = entity.side === "blue" ? "#4d91ff" : "#ef817a";
  ctx.fillRect(p.x - barW / 2, p.y - radius - barH - 5, barW * hpRatio, barH);

  drawFacingArrow(entity, p, radius);

  ctx.font = `${Math.max(10, Math.min(14, radius * 0.72))}px ui-sans-serif, system-ui`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineWidth = 3;
  ctx.strokeStyle = "rgba(255,255,255,0.88)";
  ctx.fillStyle = "#102010";
  const label = entity.kind === "tower" ? String(entity.hp) : entity.label;
  const labelY = entity.kind === "tower" && entity.side === "red"
    ? p.y - radius - 18
    : p.y + radius + 10;
  ctx.strokeText(label, p.x, labelY);
  ctx.fillText(label, p.x, labelY);
}

function drawFacingArrow(entity, p, radius) {
  if (!entity.facing) return;
  const dx = entity.facing.x;
  const dy = entity.facing.y;
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
  clockEl.textContent = `t=${state.tick}  ${state.seconds.toFixed(1)}s`;
  blueElixirEl.textContent = `Blue ${state.players.blue.elixir.toFixed(1)}`;
  redElixirEl.textContent = `Red ${state.players.red.elixir.toFixed(1)}`;
  netlineEl.textContent = `lat ${state.net.simulatedLatencyTicks}  place ${state.net.placementDelayTicks}  sync ${state.net.lockstepDelayTicks}`;
  if (state.game && state.game.over) {
    statusEl.textContent = state.game.winner ? `${capitalize(state.game.winner)} wins` : "Draw";
  } else if (state.game && state.game.phase === "sudden_death") {
    statusEl.textContent = `Sudden death  ${state.game.elixirMultiplier}x`;
  } else {
    statusEl.textContent = `Live  ${state.game ? state.game.elixirMultiplier : 1}x`;
  }

  const selectedKey = selected ? `${selected.side}:${selected.slot}` : "none";
  const handKey = [
    selectedKey,
    state.players.blue.hand.map(card => card.cardId).join(","),
    state.players.red.hand.map(card => card.cardId).join(",")
  ].join("|");
  if (handKey !== lastHandsKey) {
    renderHand("blue", blueHandEl, 1);
    renderHand("red", redHandEl, 5);
    lastHandsKey = handKey;
  }

  logEl.innerHTML = state.logs.slice(-18).reverse().map(line => `<div>${line}</div>`).join("");
}

function renderHand(side, container, firstHotkey) {
  container.innerHTML = "";
  for (const card of state.players[side].hand) {
    const meta = state.cards[card.cardId];
    const hotkey = firstHotkey + card.slot;
    const button = document.createElement("button");
    const isSelected = selected && selected.side === side && selected.slot === card.slot;
    button.className = `card ${side}-card${isSelected ? " active" : ""}`;
    button.dataset.side = side;
    button.dataset.slot = card.slot;
    button.innerHTML = `<span class="hotkey">${hotkey}</span><span class="swatch" style="background:${meta.secondaryColor}"></span><span class="name">${meta.name}</span><span class="cost">${meta.elixir}</span>`;
    button.addEventListener("click", () => selectCard(side, card.slot));
    container.appendChild(button);
  }
}

function selectCard(side, slot) {
  selected = { side, slot };
  lastHandsKey = "";
  renderPanel();
}

function getSelectedCard() {
  if (!state || !selected) return null;
  return state.players[selected.side].hand.find(card => card.slot === selected.slot) || null;
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

async function poll() {
  try {
    const response = await fetch("/api/state", { cache: "no-store" });
    state = await response.json();
    renderPanel();
  } catch (error) {
    netlineEl.textContent = String(error);
  }
}

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });
  const data = await response.json();
  if (data.state) state = data.state;
  else state = data;
  renderPanel();
}

canvas.addEventListener("click", async event => {
  const selectedCard = getSelectedCard();
  if (!state || !selectedCard) return;
  const world = snapWorld(screenToWorld(event.clientX, event.clientY));
  await postJson("/api/play", {
    side: selected.side,
    handSlot: selected.slot,
    x: world.x,
    y: world.y,
    clientTick: state.tick
  });
});

canvas.addEventListener("mousemove", event => {
  hoverWorld = screenToWorld(event.clientX, event.clientY);
});

canvas.addEventListener("mouseleave", () => {
  hoverWorld = null;
});

document.addEventListener("keydown", event => {
  if (event.target && ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const key = Number(event.key);
  if (!Number.isInteger(key) || key < 1 || key > 8) return;
  if (key <= 4) selectCard("blue", key - 1);
  else selectCard("red", key - 5);
});

pauseBtn.addEventListener("click", async () => {
  paused = !paused;
  pauseBtn.textContent = paused ? "Play" : "Pause";
  await postJson("/api/pause", { paused });
});

stepBtn.addEventListener("click", async () => {
  await postJson("/api/step", {});
});

resetBtn.addEventListener("click", async () => {
  selected = null;
  await postJson("/api/reset", {});
});

function capitalize(value) {
  if (!value) return "";
  return value[0].toUpperCase() + value.slice(1);
}

function frame() {
  drawArena();
  requestAnimationFrame(frame);
}

setInterval(poll, 90);
poll();
frame();
