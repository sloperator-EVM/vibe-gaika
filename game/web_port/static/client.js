(() => {
  const canvas = document.getElementById("game-canvas");
  const stageWrap = document.querySelector(".stage-wrap");
  const statusEl = document.getElementById("status");
  const roundEl = document.getElementById("round");
  const botsEl = document.getElementById("bots");
  const controlsEl = document.getElementById("controls");
  const rendererApi = window.GaicaGameRenderer;

  if (!canvas || !stageWrap || !statusEl || !roundEl || !botsEl || !rendererApi) {
    return;
  }

  const renderer = new rendererApi.GameRenderer({
    canvas,
    stageWrap,
    assetPaths: rendererApi.createDefaultAssetPaths(),
  });

  let latestState = null;
  let manualShoot = false;
  let manualAngle = Math.PI;
  const pressed = new Set();

  function currentManualCommand() {
    const move = { x: 0, y: 0 };
    if (pressed.has("KeyW")) move.y -= 1;
    if (pressed.has("KeyS")) move.y += 1;
    if (pressed.has("KeyA")) move.x -= 1;
    if (pressed.has("KeyD")) move.x += 1;
    const mag = Math.hypot(move.x, move.y) || 1;
    return {
      seq: Number((latestState && latestState.tick) || 0),
      move: [move.x / mag, move.y / mag],
      aim: [Math.cos(manualAngle), Math.sin(manualAngle)],
      shoot: manualShoot,
      kick: false,
      pickup: false,
      drop: false,
      throw: false,
      interact: false,
    };
  }

  async function sendManualCommand() {
    if (!latestState || !(latestState.manual_player_ids || []).includes(2)) {
      return;
    }
    try {
      await fetch("/api/manual-command", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ player_id: 2, command: currentManualCommand() }),
      });
    } catch (err) {
      console.error("manual command failed", err);
    }
  }

  function handleKeyDown(event) {
    if (!(latestState && (latestState.manual_player_ids || []).includes(2))) {
      return;
    }
    if (["KeyW", "KeyA", "KeyS", "KeyD", "ArrowLeft", "ArrowRight", "ArrowUp"].includes(event.code)) {
      event.preventDefault();
    }
    if (["KeyW", "KeyA", "KeyS", "KeyD"].includes(event.code)) {
      pressed.add(event.code);
    } else if (event.code === "ArrowLeft") {
      manualAngle -= 0.22;
    } else if (event.code === "ArrowRight") {
      manualAngle += 0.22;
    } else if (event.code === "ArrowUp") {
      manualShoot = true;
    }
    sendManualCommand();
  }

  function handleKeyUp(event) {
    if (!(latestState && (latestState.manual_player_ids || []).includes(2))) {
      return;
    }
    if (["KeyW", "KeyA", "KeyS", "KeyD"].includes(event.code)) {
      pressed.delete(event.code);
    } else if (event.code === "ArrowUp") {
      manualShoot = false;
    }
    sendManualCommand();
  }

  async function pollState() {
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      const state = await response.json();
      latestState = state;
      renderer.setState(state);

      const series = state.series || null;
      const manualMode = (state.manual_player_ids || []).includes(2);
      statusEl.textContent = `Статус: ${state.status}`;
      const baseRound = `Тик: ${state.tick} | Время: ${state.time_seconds.toFixed(2)} сек | Карта: ${state.level.identifier}`;
      if (series && series.total_rounds > 1) {
        const score = series.score || {};
        const roundNo = Number(series.round || 0);
        const totalRounds = Number(series.total_rounds || 1);
        const s1 = Number(score["1"] || 0);
        const s2 = Number(score["2"] || 0);
        roundEl.textContent = `${baseRound} | Серия: ${roundNo}/${totalRounds} | Счёт: ${s1}:${s2}`;
      } else {
        roundEl.textContent = baseRound;
      }

      const connected = (state.bots_connected || []).join(", ") || "нет";
      const roles = (state.color_roles || [])
        .map((role) => {
          const side = role.color === "red" ? "Красный" : "Зелёный";
          const botLabel = role.bot_name || (role.bot_id ? `bot#${role.bot_id}` : "ожидание");
          return `${side}: ${botLabel}`;
        })
        .join(" | ");
      botsEl.textContent = `Игроки подключены: ${connected}${roles ? ` | ${roles}` : ""}`;

      if (controlsEl) {
        controlsEl.textContent = manualMode
          ? "Режим bot-vs-human: игрок #2 управляется с клавиатуры — WASD движение, ←/→ поворот, ↑ выстрел."
          : "Режим bot-vs-bot: оба игрока управляются ботами.";
      }

      if (manualMode && state.players && state.players[1]) {
        const facing = state.players[1].facing || [-1, 0];
        manualAngle = Math.atan2(facing[1], facing[0]);
      }

      if (state.result) {
        if (state.result.reason === "series_score") {
          const score = state.result.series_score || {};
          const s1 = Number(score["1"] || 0);
          const s2 = Number(score["2"] || 0);
          const winner = state.result.winner_id ? `Победил бот #${state.result.winner_id}` : "Ничья";
          statusEl.textContent = `Матч завершён: ${winner} | Итоговый счёт ${s1}:${s2}`;
        } else {
          const winner = state.result.winner_id ? `Победил бот #${state.result.winner_id}` : "Ничья";
          statusEl.textContent = `Раунд завершён: ${winner} (${state.result.reason})`;
        }
      }
    } catch (err) {
      statusEl.textContent = "Ошибка чтения состояния сервера";
    }
  }

  async function bootstrap() {
    try {
      await renderer.loadAssets();
    } catch (err) {
      statusEl.textContent = `Ошибка загрузки ассетов: ${err}`;
      console.error(err);
      return;
    }

    renderer.resize();
    window.addEventListener("resize", () => renderer.resize());
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("keyup", handleKeyUp);
    setInterval(pollState, 33);
    setInterval(sendManualCommand, 80);
    pollState();
    renderer.start();
  }

  bootstrap();
})();
