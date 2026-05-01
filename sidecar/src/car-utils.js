const FOUR_HOURS_MS = 4 * 60 * 60 * 1000;

export function normalizeCars(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const body = source.body && typeof source.body === "object" ? source.body : source;
  const roleCar = body.roleCar || body.rolecar || {};
  const carMap = roleCar.carDataMap || roleCar.cardatamap;

  if (carMap && typeof carMap === "object") {
    return Object.entries(carMap).map(([id, info], index) => ({
      key: index,
      id,
      ...(info || {}),
    }));
  }

  let items = body.cars || body.list || body.data || body.carList || body.vehicles || [];
  if (!Array.isArray(items) && items && typeof items === "object") {
    items = Object.values(items);
  }
  if (Array.isArray(body) && items.length === 0) {
    items = body;
  }

  return (Array.isArray(items) ? items : []).map((item, index) => ({
    key: index,
    ...(item || {}),
  }));
}

export function gradeLabel(color) {
  const labels = {
    1: "绿·普通",
    2: "蓝·稀有",
    3: "紫·史诗",
    4: "橙·传说",
    5: "红·神话",
    6: "金·传奇",
  };
  return labels[Number(color)] || "未知";
}

export function canClaim(car, nowMs = Date.now()) {
  const sentAt = Number(car?.sendAt || 0);
  if (!sentAt) {
    return false;
  }
  const sentAtMs = sentAt < 1e12 ? sentAt * 1000 : sentAt;
  return nowMs - sentAtMs >= FOUR_HOURS_MS;
}

function buildCarStatus(car, nowMs = Date.now()) {
  const sentAt = Number(car?.sendAt || 0);
  if (!sentAt) {
    return "idle";
  }
  return canClaim(car, nowMs) ? "claimable" : "running";
}

function normalizeSentAt(sentAt) {
  const numeric = Number(sentAt || 0);
  if (!numeric) {
    return 0;
  }
  return numeric < 1e12 ? numeric * 1000 : numeric;
}

export function summarizeCars(raw, nowMs = Date.now()) {
  const cars = normalizeCars(raw).map((car) => {
    const sentAtMs = normalizeSentAt(car.sendAt);
    const status = buildCarStatus(car, nowMs);
    return {
      id: String(car.id ?? ""),
      color: Number(car.color || 0),
      gradeLabel: gradeLabel(car.color),
      rewards: Array.isArray(car.rewards) ? car.rewards : [],
      refreshCount: Number(car.refreshCount ?? 0),
      helperId: car.helperId ?? null,
      sendAt: car.sendAt ?? 0,
      sentAtMs,
      status,
      claimable: status === "claimable",
      claimableInSeconds:
        status === "running" && sentAtMs
          ? Math.max(0, Math.ceil((FOUR_HOURS_MS - (nowMs - sentAtMs)) / 1000))
          : 0,
    };
  });

  const summary = {
    totalCars: cars.length,
    idleCars: cars.filter((car) => car.status === "idle").length,
    runningCars: cars.filter((car) => car.status === "running").length,
    claimableCars: cars.filter((car) => car.status === "claimable").length,
    highestColor: cars.reduce(
      (current, car) => Math.max(current, Number(car.color || 0)),
      0,
    ),
  };

  return {
    summary,
    cars,
  };
}

export function findCarById(raw, carId, nowMs = Date.now()) {
  const normalizedCarId = String(carId ?? "").trim();
  if (!normalizedCarId) {
    return null;
  }

  const overview = summarizeCars(raw, nowMs);
  return (
    overview.cars.find((car) => String(car.id ?? "").trim() === normalizedCarId) ||
    null
  );
}
