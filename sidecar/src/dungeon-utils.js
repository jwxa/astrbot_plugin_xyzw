function badRequest(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function normalizeAction(action) {
  return String(action || "").trim().toLowerCase();
}

function parsePositiveInt(value, defaultValue, fieldName, maxValue = 999999) {
  if (value === undefined || value === null || value === "") {
    return defaultValue;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    throw badRequest(`${fieldName} 必须是正整数`);
  }
  return Math.min(Math.trunc(numeric), maxValue);
}

function parseOptionalPositiveInt(value, fieldName, maxValue = 999999) {
  if (value === undefined || value === null || value === "") {
    return null;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    throw badRequest(`${fieldName} 必须是正整数`);
  }
  return Math.min(Math.trunc(numeric), maxValue);
}

function isCommandSuccessful(result) {
  const rawCode = result?.code;
  if (rawCode === undefined || rawCode === null || rawCode === "") {
    return !result?.error;
  }
  return Number(rawCode) === 0;
}

function wait(timeoutMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, timeoutMs);
  });
}

function isDreamOpen(now = new Date()) {
  const day = now.getDay();
  return day === 0 || day === 1 || day === 3 || day === 4;
}

function buildDateKey(now = new Date()) {
  const year = now.getFullYear().toString().slice(2);
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${year}${month}${day}`;
}

const DREAM_MERCHANT_CONFIG = {
  1: {
    name: "初级商人",
    items: [
      "进阶石",
      "精铁",
      "木质宝箱",
      "青铜宝箱",
      "普通鱼竿",
      "咸神门票",
      "咸神火把",
    ],
  },
  2: {
    name: "中级商人",
    items: [
      "梦魇晶石",
      "进阶石",
      "精铁",
      "黄金宝箱",
      "黄金鱼竿",
      "招募令",
      "橙将碎片",
      "紫将碎片",
    ],
  },
  3: {
    name: "高级商人",
    items: [
      "梦魇晶石",
      "铂金宝箱",
      "黄金鱼竿",
      "招募令",
      "红将碎片",
      "橙将碎片",
      "红将碎片",
      "普通鱼竿",
    ],
  },
};

const DREAM_GOLD_PURCHASE_LIST = [
  "1-5",
  "1-6",
  "2-6",
  "2-7",
  "3-5",
  "3-6",
  "3-7",
];

function getDreamMerchantName(merchantId) {
  return DREAM_MERCHANT_CONFIG[merchantId]?.name || `商人${merchantId}`;
}

function getDreamItemName(merchantId, itemIndex) {
  return DREAM_MERCHANT_CONFIG[merchantId]?.items?.[itemIndex] || `商品${itemIndex}`;
}

function parseDreamPurchaseList(rawList) {
  if (rawList === undefined || rawList === null || rawList === "") {
    return [];
  }

  const values = Array.isArray(rawList)
    ? rawList
    : String(rawList)
        .split(/[,\s]+/)
        .map((item) => item.trim())
        .filter(Boolean);

  const normalizedKeys = [];
  for (const value of values) {
    let merchantId = null;
    let itemIndex = null;

    if (typeof value === "string") {
      const matched = value.trim().match(/^(\d+)-(\d+)$/);
      if (!matched) {
        throw badRequest("purchase_list 项格式必须为 merchantId-itemIndex，例如 1-5");
      }
      merchantId = Number(matched[1]);
      itemIndex = Number(matched[2]);
    } else if (value && typeof value === "object") {
      merchantId = Number(value.merchant_id ?? value.merchantId);
      itemIndex = Number(value.item_index ?? value.itemIndex ?? value.index);
    } else {
      throw badRequest("purchase_list 项格式无效");
    }

    if (
      !Number.isInteger(merchantId) ||
      merchantId <= 0 ||
      !Number.isInteger(itemIndex) ||
      itemIndex < 0
    ) {
      throw badRequest("purchase_list 项格式必须为正整数商人ID和非负整数商品索引");
    }

    normalizedKeys.push(`${merchantId}-${itemIndex}`);
  }

  return [...new Set(normalizedKeys)];
}

function resolveDreamPurchasePreset(rawPreset, defaultValue = "gold_items") {
  const normalized = String(rawPreset ?? defaultValue).trim().toLowerCase();
  if (!normalized) {
    return "gold_items";
  }
  if (["gold", "gold_items", "golditems", "金币", "金币商品"].includes(normalized)) {
    return "gold_items";
  }
  if (["custom", "自定义"].includes(normalized)) {
    return "custom";
  }
  throw badRequest(`不支持的梦境购买预设: ${rawPreset}`);
}

function buildDreamPurchaseOptions(options) {
  const purchaseList = parseDreamPurchaseList(
    options.purchase_list ?? options.purchaseList,
  );
  if (purchaseList.length > 0) {
    return {
      purchasePreset: resolveDreamPurchasePreset(
        options.preset ?? options.mode,
        "custom",
      ),
      purchaseList,
    };
  }

  const purchasePreset = resolveDreamPurchasePreset(options.preset ?? options.mode);
  if (purchasePreset === "gold_items") {
    return {
      purchasePreset,
      purchaseList: [...DREAM_GOLD_PURCHASE_LIST],
    };
  }

  throw badRequest("自定义梦境购买必须提供 purchase_list");
}

function buildFailedResult(execution, result, message, extra = {}) {
  return {
    ...execution,
    success: false,
    skipped: false,
    code: result?.code ?? null,
    error: result?.error || "",
    body: result?.body,
    message,
    ...extra,
  };
}

const DUNGEON_ACTIONS = {
  bosstower_low: {
    label: "宝库前3层",
    kind: "bosstower",
    minTowerId: 1,
    maxTowerId: 3,
    bossBattleCount: 2,
    boxOpenCount: 9,
    intervalMs: 500,
  },
  bosstower_high: {
    label: "宝库后2层",
    kind: "bosstower",
    minTowerId: 4,
    maxTowerId: 5,
    bossBattleCount: 2,
    boxOpenCount: 0,
    intervalMs: 500,
  },
  dream_team: {
    label: "咸王梦境阵容",
    kind: "dream_team",
    buildOptions(options) {
      return {
        teamId: parsePositiveInt(
          options.team_id ?? options.teamId,
          107,
          "team_id",
        ),
      };
    },
  },
  dream_purchase: {
    label: "咸王梦境商店购买",
    kind: "dream_purchase",
    intervalMs: 500,
    buildOptions(options) {
      return buildDreamPurchaseOptions(options);
    },
  },
  weirdtower_overview: {
    label: "怪异塔状态",
    kind: "weirdtower_overview",
  },
  weirdtower_claim_free_energy: {
    label: "怪异塔免费道具",
    kind: "weirdtower_claim_free_energy",
  },
  weirdtower_use_items: {
    label: "怪异塔使用道具",
    kind: "weirdtower_use_items",
    intervalMs: 500,
    buildOptions(options) {
      return {
        maxUses: parseOptionalPositiveInt(
          options.max_uses ?? options.maxUses,
          "max_uses",
          999,
        ),
      };
    },
  },
  weirdtower_climb: {
    label: "怪异塔爬塔",
    kind: "weirdtower_climb",
    intervalMs: 300,
    buildOptions(options) {
      return {
        teamId: parsePositiveInt(
          options.team_id ?? options.teamId,
          1,
          "team_id",
          20,
        ),
        maxFights: parsePositiveInt(
          options.max_fights ?? options.maxFights,
          5,
          "max_fights",
          8,
        ),
      };
    },
  },
  skinchallenge_overview: {
    label: "换皮闯关状态",
    kind: "skinchallenge_overview",
  },
  skinchallenge_tower: {
    label: "换皮闯关挑战",
    kind: "skinchallenge_tower",
    intervalMs: 500,
    buildOptions(options) {
      return {
        towerType: parseOptionalPositiveInt(
          options.tower_type ?? options.towerType,
          "tower_type",
          6,
        ),
      };
    },
  },
  skinchallenge_today: {
    label: "换皮闯关补打",
    kind: "skinchallenge_today",
    intervalMs: 500,
  },
};

export function buildDungeonExecution(action, options = {}) {
  const normalizedAction = normalizeAction(action);
  const spec = DUNGEON_ACTIONS[normalizedAction];
  if (!spec) {
    throw badRequest(`不支持的副本动作: ${action}`);
  }

  const normalizedOptions =
    options && typeof options === "object" && !Array.isArray(options) ? options : {};

  return {
    action: normalizedAction,
    label: spec.label,
    kind: spec.kind,
    minTowerId: spec.minTowerId,
    maxTowerId: spec.maxTowerId,
    bossBattleCount: spec.bossBattleCount ?? 0,
    boxOpenCount: spec.boxOpenCount ?? 0,
    intervalMs: spec.intervalMs ?? 0,
    ...(spec.buildOptions ? spec.buildOptions(normalizedOptions) : {}),
  };
}

async function runBossTowerAction(client, execution, timeoutMs) {
  const infoResult = await client.runCommand("bosstower_getinfo", {}, { timeoutMs });
  if (!isCommandSuccessful(infoResult)) {
    return buildFailedResult(
      execution,
      infoResult,
      "获取宝库信息失败",
    );
  }

  const towerId = Number(infoResult.body?.bossTower?.towerId || 0);
  if (
    !Number.isFinite(towerId) ||
    towerId < execution.minTowerId ||
    towerId > execution.maxTowerId
  ) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: infoResult.code ?? 0,
      error: infoResult.error || "",
      body: infoResult.body,
      towerId,
      message: `当前宝库层数为 ${towerId || 0}，不在 ${execution.label} 执行范围内`,
      executedBossBattles: 0,
      executedBoxOpens: 0,
    };
  }

  let lastResult = infoResult;
  for (let index = 0; index < execution.bossBattleCount; index += 1) {
    const result = await client.runCommand("bosstower_startboss", {}, { timeoutMs });
    if (!isCommandSuccessful(result)) {
      return buildFailedResult(
        execution,
        result,
        `宝库 BOSS 战斗失败（第 ${index + 1}/${execution.bossBattleCount} 次）`,
        {
          towerId,
          executedBossBattles: index,
          executedBoxOpens: 0,
        },
      );
    }
    lastResult = result;
    if (
      execution.intervalMs > 0 &&
      (index + 1 < execution.bossBattleCount || execution.boxOpenCount > 0)
    ) {
      await wait(execution.intervalMs);
    }
  }

  for (let index = 0; index < execution.boxOpenCount; index += 1) {
    const result = await client.runCommand("bosstower_startbox", {}, { timeoutMs });
    if (!isCommandSuccessful(result)) {
      return buildFailedResult(
        execution,
        result,
        `宝库开箱失败（第 ${index + 1}/${execution.boxOpenCount} 次）`,
        {
          towerId,
          executedBossBattles: execution.bossBattleCount,
          executedBoxOpens: index,
        },
      );
    }
    lastResult = result;
    if (execution.intervalMs > 0 && index + 1 < execution.boxOpenCount) {
      await wait(execution.intervalMs);
    }
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: lastResult.code ?? 0,
    error: lastResult.error || "",
    body: lastResult.body,
    towerId,
    executedBossBattles: execution.bossBattleCount,
    executedBoxOpens: execution.boxOpenCount,
    message: `${execution.label}执行完成`,
  };
}

async function runDreamTeamAction(client, execution, timeoutMs) {
  if (!isDreamOpen()) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: null,
      message: "当前不是梦境开放时间（周日/周一/周三/周四）",
    };
  }

  const result = await client.runCommand(
    "dungeon_selecthero",
    { battleTeam: { 0: execution.teamId } },
    {
      timeoutMs,
      responseCommand: "syncrewardresp",
    },
  );

  if (!isCommandSuccessful(result)) {
    return buildFailedResult(
      execution,
      result,
      `梦境阵容切换失败（阵容 ${execution.teamId}）`,
    );
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: result.code ?? 0,
    error: result.error || "",
    body: result.body,
    message: `梦境阵容已切换为 ${execution.teamId}`,
  };
}

function buildDreamUnavailableItems(itemKeys) {
  return itemKeys.map((itemKey) => {
    const [merchantId, itemIndex] = String(itemKey).split("-").map(Number);
    return {
      key: itemKey,
      merchantId,
      itemIndex,
      merchantName: getDreamMerchantName(merchantId),
      itemName: getDreamItemName(merchantId, itemIndex),
    };
  });
}

async function runDreamPurchaseAction(client, execution, timeoutMs) {
  if (!isDreamOpen()) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: null,
      levelId: 0,
      requestedItemCount: execution.purchaseList.length,
      matchedOperationCount: 0,
      successCount: 0,
      failCount: 0,
      unavailableItemCount: execution.purchaseList.length,
      unavailableItems: buildDreamUnavailableItems(execution.purchaseList),
      purchaseResults: [],
      message: "当前不是梦境开放时间（周日/周一/周三/周四）",
    };
  }

  const roleInfoResult = await client.runCommand(
    "role_getroleinfo",
    {},
    {
      timeoutMs,
      responseCommand: "role_getroleinforesp",
    },
  );
  if (!isCommandSuccessful(roleInfoResult)) {
    return buildFailedResult(
      execution,
      roleInfoResult,
      "获取梦境商店数据失败",
    );
  }

  const roleInfo = roleInfoResult.body || {};
  const merchantData = roleInfo?.role?.dungeon?.merchant;
  const levelId = Number(roleInfo?.role?.levelId || 0);
  if (!merchantData || typeof merchantData !== "object") {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: roleInfoResult.code ?? 0,
      error: "",
      body: roleInfo,
      levelId,
      requestedItemCount: execution.purchaseList.length,
      matchedOperationCount: 0,
      successCount: 0,
      failCount: 0,
      unavailableItemCount: execution.purchaseList.length,
      unavailableItems: buildDreamUnavailableItems(execution.purchaseList),
      purchaseResults: [],
      message: "当前未获取到有效的梦境商店数据",
    };
  }

  if (levelId < 4000) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: roleInfoResult.code ?? 0,
      error: "",
      body: roleInfo,
      levelId,
      requestedItemCount: execution.purchaseList.length,
      matchedOperationCount: 0,
      successCount: 0,
      failCount: 0,
      unavailableItemCount: execution.purchaseList.length,
      unavailableItems: buildDreamUnavailableItems(execution.purchaseList),
      purchaseResults: [],
      message: "关卡数小于 4000，当前无法执行梦境购买",
    };
  }

  const matchedKeys = new Set();
  const operations = [];
  for (const itemKey of execution.purchaseList) {
    const [merchantId, itemIndex] = itemKey.split("-").map(Number);
    const merchantItems = merchantData[merchantId] ?? merchantData[String(merchantId)];
    if (!Array.isArray(merchantItems)) {
      continue;
    }

    for (let pos = 0; pos < merchantItems.length; pos += 1) {
      if (Number(merchantItems[pos]) !== itemIndex) {
        continue;
      }
      matchedKeys.add(itemKey);
      operations.push({
        key: itemKey,
        merchantId,
        itemIndex,
        pos,
        merchantName: getDreamMerchantName(merchantId),
        itemName: getDreamItemName(merchantId, itemIndex),
      });
    }
  }

  operations.sort((left, right) => {
    if (left.merchantId !== right.merchantId) {
      return left.merchantId - right.merchantId;
    }
    return right.pos - left.pos;
  });

  const unavailableItems = buildDreamUnavailableItems(
    execution.purchaseList.filter((itemKey) => !matchedKeys.has(itemKey)),
  );
  if (operations.length === 0) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: roleInfoResult.code ?? 0,
      error: "",
      body: roleInfo,
      levelId,
      requestedItemCount: execution.purchaseList.length,
      matchedOperationCount: 0,
      successCount: 0,
      failCount: 0,
      unavailableItemCount: unavailableItems.length,
      unavailableItems,
      purchaseResults: [],
      message: "当前梦境商店没有匹配的可购商品",
    };
  }

  let successCount = 0;
  let failCount = 0;
  const purchaseResults = [];

  for (let index = 0; index < operations.length; index += 1) {
    const operation = operations[index];
    const result = await client.runCommand(
      "dungeon_buymerchant",
      {
        id: operation.merchantId,
        index: operation.itemIndex,
        pos: operation.pos,
      },
      { timeoutMs },
    );
    const success = isCommandSuccessful(result);
    if (success) {
      successCount += 1;
    } else {
      failCount += 1;
    }
    purchaseResults.push({
      ...operation,
      success,
      code: result.code ?? null,
      error: result.error || "",
      body: result.body,
    });

    if (execution.intervalMs > 0 && index + 1 < operations.length) {
      await wait(execution.intervalMs);
    }
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: failCount > 0 ? "PARTIAL_FAILURE" : 0,
    error: "",
    body: roleInfo,
    levelId,
    requestedItemCount: execution.purchaseList.length,
    matchedOperationCount: operations.length,
    successCount,
    failCount,
    unavailableItemCount: unavailableItems.length,
    unavailableItems,
    purchaseResults,
    message:
      failCount > 0
        ? `梦境购买完成: 成功 ${successCount}，失败 ${failCount}，未上架 ${unavailableItems.length}`
        : `梦境购买完成: 成功 ${successCount}，未上架 ${unavailableItems.length}`,
  };
}

async function getWeirdTowerInfo(client, timeoutMs) {
  return await client.runCommand(
    "evotower_getinfo",
    {},
    {
      timeoutMs,
      responseCommand: "evotowerinforesp",
    },
  );
}

async function getMergeBoxInfo(client, timeoutMs) {
  return await client.runCommand(
    "mergebox_getinfo",
    { actType: 1 },
    {
      timeoutMs,
      responseCommand: "mergeboxinforesp",
    },
  );
}

async function getSkinChallengeInfo(client, timeoutMs) {
  return await client.runCommand(
    "towers_getinfo",
    {},
    {
      timeoutMs,
      responseCommand: "towers_getinforesp",
    },
  );
}

function buildWeirdTowerSummary(evoTowerInfo) {
  const evoTower = evoTowerInfo?.body?.evoTower ?? {};
  const towerId = Number(evoTower.towerId || 0);
  return {
    towerId,
    chapter: towerId > 0 ? Math.floor(towerId / 10) : 0,
    floor: towerId > 0 ? (towerId % 10) + 1 : 0,
    energy: Number(evoTower.energy || 0),
    lotteryLeftCnt: Number(evoTower.lotteryLeftCnt || 0),
  };
}

function buildWeirdTowerSnapshot(evoTowerInfo, mergeBoxInfo) {
  const mergeBox = mergeBoxInfo?.body?.mergeBox ?? {};
  const summary = buildWeirdTowerSummary(evoTowerInfo);
  const evoTower = evoTowerInfo?.body?.evoTower ?? {};
  const rawTaskClaimMap = evoTower.taskClaimMap ?? {};
  const todayTaskClaimMap = rawTaskClaimMap[buildDateKey()] ?? {};
  const claimedTaskIds = [1, 2, 3].filter((taskId) => {
    return Boolean(todayTaskClaimMap[taskId] ?? todayTaskClaimMap[String(taskId)]);
  });

  return {
    ...summary,
    freeEnergy: Number(mergeBox.freeEnergy || 0),
    mergeCostTotalCnt: Number(mergeBox.costTotalCnt || 0),
    claimedTaskIds,
  };
}

function getWeirdTowerOpenBoxPosition(costTotalCnt) {
  if (costTotalCnt < 2) {
    return { gridX: 4, gridY: 5 };
  }
  if (costTotalCnt < 102) {
    return { gridX: 7, gridY: 3 };
  }
  return { gridX: 6, gridY: 3 };
}

function normalizeSkinChallengeBody(body) {
  if (!body || typeof body !== "object") {
    return {};
  }
  if (body.actId) {
    return body;
  }
  if (body.towerData && typeof body.towerData === "object" && body.towerData.actId) {
    return body.towerData;
  }
  return body;
}

function parseSkinChallengeWindow(actId) {
  const normalized = String(actId || "").trim();
  if (normalized.length < 6) {
    return null;
  }
  const year = `20${normalized.slice(0, 2)}`;
  const month = normalized.slice(2, 4);
  const day = normalized.slice(4, 6);
  const startDate = new Date(`${year}-${month}-${day}T00:00:00`);
  if (Number.isNaN(startDate.getTime())) {
    return null;
  }
  const endDate = new Date(startDate);
  endDate.setDate(startDate.getDate() + 7);
  return { startDate, endDate };
}

function isSkinChallengeActive(actId, now = new Date()) {
  const window = parseSkinChallengeWindow(actId);
  if (!window) {
    return true;
  }
  return now >= window.startDate && now < window.endDate;
}

function getTodayOpenSkinTowers(now = new Date()) {
  const openTowerMap = {
    5: [1],
    6: [2],
    0: [3],
    1: [4],
    2: [5],
    3: [6],
    4: [1, 2, 3, 4, 5, 6],
  };
  return openTowerMap[now.getDay()] || [];
}

function isSkinTowerCleared(type, levelRewardMap) {
  const key1 = `${type}008`;
  const key2 = Number(key1);
  return Boolean(levelRewardMap?.[key1] || levelRewardMap?.[key2]);
}

function getSkinTowerLevel(type, levelRewardMap) {
  for (let level = 8; level >= 1; level -= 1) {
    const key1 = `${type}00${level}`;
    const key2 = Number(key1);
    if (levelRewardMap?.[key1] || levelRewardMap?.[key2]) {
      return level === 8 ? 8 : level + 1;
    }
  }
  return 1;
}

function buildSkinChallengeSnapshot(infoResult, now = new Date()) {
  const towerData = normalizeSkinChallengeBody(infoResult?.body);
  const levelRewardMap = towerData.levelRewardMap || {};
  const todayOpenTowers = getTodayOpenSkinTowers(now);
  const pendingTowers = todayOpenTowers.filter((towerType) => {
    return !isSkinTowerCleared(towerType, levelRewardMap);
  });

  return {
    actId: towerData.actId ? String(towerData.actId) : "",
    active: Boolean(towerData.actId) && isSkinChallengeActive(towerData.actId, now),
    todayOpenTowers,
    pendingTowers,
    levelRewardMap,
  };
}

async function runWeirdTowerOverviewAction(client, execution, timeoutMs) {
  const [evoTowerInfo, mergeBoxInfo] = await Promise.all([
    getWeirdTowerInfo(client, timeoutMs),
    getMergeBoxInfo(client, timeoutMs),
  ]);

  if (!isCommandSuccessful(evoTowerInfo)) {
    return buildFailedResult(
      execution,
      evoTowerInfo,
      "获取怪异塔信息失败",
    );
  }
  if (!isCommandSuccessful(mergeBoxInfo)) {
    return buildFailedResult(
      execution,
      mergeBoxInfo,
      "获取怪异塔道具信息失败",
    );
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: 0,
    error: "",
    body: {
      evoTower: evoTowerInfo.body?.evoTower ?? null,
      mergeBox: mergeBoxInfo.body?.mergeBox ?? null,
    },
    ...buildWeirdTowerSnapshot(evoTowerInfo, mergeBoxInfo),
    message: "怪异塔状态获取完成",
  };
}

async function runWeirdTowerClaimFreeEnergyAction(client, execution, timeoutMs) {
  const mergeBoxInfo = await getMergeBoxInfo(client, timeoutMs);
  if (!isCommandSuccessful(mergeBoxInfo)) {
    return buildFailedResult(
      execution,
      mergeBoxInfo,
      "获取怪异塔免费道具信息失败",
    );
  }

  const freeEnergy = Number(mergeBoxInfo.body?.mergeBox?.freeEnergy || 0);
  if (freeEnergy <= 0) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: mergeBoxInfo.body,
      freeEnergyClaimed: 0,
      message: "当前没有可领取的怪异塔免费道具",
    };
  }

  const claimResult = await client.runCommand(
    "mergebox_claimfreeenergy",
    { actType: 1 },
    { timeoutMs },
  );
  if (!isCommandSuccessful(claimResult)) {
    return buildFailedResult(
      execution,
      claimResult,
      "领取怪异塔免费道具失败",
      {
        freeEnergyClaimed: 0,
      },
    );
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: claimResult.code ?? 0,
    error: claimResult.error || "",
    body: claimResult.body,
    freeEnergyClaimed: freeEnergy,
    message: `已领取怪异塔免费道具 ${freeEnergy} 个`,
  };
}

async function runWeirdTowerUseItemsAction(client, execution, timeoutMs) {
  const [mergeBoxInfo, evoTowerInfo] = await Promise.all([
    getMergeBoxInfo(client, timeoutMs),
    getWeirdTowerInfo(client, timeoutMs),
  ]);
  if (!isCommandSuccessful(mergeBoxInfo)) {
    return buildFailedResult(
      execution,
      mergeBoxInfo,
      "获取怪异塔道具信息失败",
    );
  }
  if (!isCommandSuccessful(evoTowerInfo)) {
    return buildFailedResult(
      execution,
      evoTowerInfo,
      "获取怪异塔信息失败",
    );
  }

  const before = buildWeirdTowerSnapshot(evoTowerInfo, mergeBoxInfo);
  let costTotalCnt = before.mergeCostTotalCnt;
  let lotteryLeftCnt = before.lotteryLeftCnt;
  if (lotteryLeftCnt <= 0) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: {
        evoTower: evoTowerInfo.body?.evoTower ?? null,
        mergeBox: mergeBoxInfo.body?.mergeBox ?? null,
      },
      before,
      after: before,
      targetUses: 0,
      processedCount: 0,
      claimCostProgressAttempted: false,
      claimCostProgressSuccess: false,
      message: "当前没有剩余怪异塔道具可使用",
    };
  }

  const targetUses = execution.maxUses
    ? Math.min(execution.maxUses, lotteryLeftCnt)
    : lotteryLeftCnt;
  let processedCount = 0;

  while (lotteryLeftCnt > 0 && processedCount < targetUses) {
    const position = getWeirdTowerOpenBoxPosition(costTotalCnt);
    const openResult = await client.runCommand(
      "mergebox_openbox",
      {
        actType: 1,
        pos: position,
      },
      { timeoutMs },
    );
    if (!isCommandSuccessful(openResult)) {
      return buildFailedResult(
        execution,
        openResult,
        `怪异塔使用道具失败（第 ${processedCount + 1}/${targetUses} 次）`,
        {
          before,
          after: {
            ...before,
            mergeCostTotalCnt: costTotalCnt,
            lotteryLeftCnt,
          },
          targetUses,
          processedCount,
          claimCostProgressAttempted: false,
          claimCostProgressSuccess: false,
        },
      );
    }

    costTotalCnt += 1;
    lotteryLeftCnt -= 1;
    processedCount += 1;

    if (processedCount < targetUses && lotteryLeftCnt > 0) {
      await wait(execution.intervalMs);
    }
  }

  let claimCostProgressAttempted = false;
  let claimCostProgressSuccess = false;
  try {
    claimCostProgressAttempted = true;
    const progressResult = await client.runCommand(
      "mergebox_claimcostprogress",
      { actType: 1 },
      { timeoutMs },
    );
    claimCostProgressSuccess = isCommandSuccessful(progressResult);
  } catch (_error) {
    claimCostProgressSuccess = false;
  }

  const [finalMergeBoxInfo, finalEvoTowerInfo] = await Promise.all([
    getMergeBoxInfo(client, timeoutMs),
    getWeirdTowerInfo(client, timeoutMs),
  ]);
  const after = (
    isCommandSuccessful(finalMergeBoxInfo) && isCommandSuccessful(finalEvoTowerInfo)
  )
    ? buildWeirdTowerSnapshot(finalEvoTowerInfo, finalMergeBoxInfo)
    : {
        ...before,
        mergeCostTotalCnt: costTotalCnt,
        lotteryLeftCnt,
      };

  return {
    ...execution,
    success: true,
    skipped: false,
    code: 0,
    error: "",
    body: {
      evoTower: finalEvoTowerInfo.body?.evoTower ?? evoTowerInfo.body?.evoTower ?? null,
      mergeBox: finalMergeBoxInfo.body?.mergeBox ?? mergeBoxInfo.body?.mergeBox ?? null,
    },
    before,
    after,
    targetUses,
    processedCount,
    claimCostProgressAttempted,
    claimCostProgressSuccess,
    message: `怪异塔已使用 ${processedCount} 个道具`,
  };
}

async function claimWeirdTowerDailyTasks(client, evoTowerBody, timeoutMs) {
  const rawTaskClaimMap = evoTowerBody?.evoTower?.taskClaimMap ?? evoTowerBody?.taskClaimMap ?? {};
  const todayTaskClaimMap = rawTaskClaimMap[buildDateKey()] ?? {};
  const claimedTaskIds = [];

  for (const taskId of [1, 2, 3]) {
    if (todayTaskClaimMap[taskId] ?? todayTaskClaimMap[String(taskId)]) {
      continue;
    }
    try {
      const claimResult = await client.runCommand(
        "evotower_claimtask",
        { taskId },
        { timeoutMs },
      );
      if (isCommandSuccessful(claimResult)) {
        claimedTaskIds.push(taskId);
        await wait(200);
      }
    } catch (_error) {
      // 任务未达成时服务端会直接报错，这里按 Web 侧现有策略静默跳过。
    }
  }

  return claimedTaskIds;
}

async function runWeirdTowerClimbAction(client, execution, timeoutMs) {
  const teamInfo = await client.runCommand("presetteam_getinfo", {}, { timeoutMs });
  if (!isCommandSuccessful(teamInfo)) {
    return buildFailedResult(
      execution,
      teamInfo,
      "获取阵容信息失败",
    );
  }

  const originalTeamId = Number(teamInfo.body?.presetTeamInfo?.useTeamId || 0) || null;
  let switchedFormation = false;
  let restoredFormation = false;
  let outcome = null;

  try {
    if (originalTeamId !== execution.teamId) {
      const switchResult = await client.runCommand(
        "presetteam_saveteam",
        { teamId: execution.teamId },
        { timeoutMs },
      );
      if (!isCommandSuccessful(switchResult)) {
        outcome = buildFailedResult(
          execution,
          switchResult,
          `切换怪异塔阵容失败（阵容 ${execution.teamId}）`,
          {
            originalTeamId,
            restoredFormation: false,
            switchedFormation: false,
          },
        );
        return outcome;
      }
      switchedFormation = true;
      await wait(200);
    }

    let infoResult = await getWeirdTowerInfo(client, timeoutMs);
    if (!isCommandSuccessful(infoResult)) {
      outcome = buildFailedResult(
        execution,
        infoResult,
        "获取怪异塔信息失败",
        {
          originalTeamId,
          restoredFormation: false,
          switchedFormation,
        },
      );
      return outcome;
    }

    const before = buildWeirdTowerSummary(infoResult);
    if (before.energy <= 0) {
      outcome = {
        ...execution,
        success: true,
        skipped: true,
        code: 0,
        error: "",
        body: infoResult.body,
        originalTeamId,
        switchedFormation,
        restoredFormation: false,
        executedFightCount: 0,
        claimedTaskIds: [],
        before,
        after: before,
        message: "当前怪异塔能量为 0，无需继续爬塔",
      };
      return outcome;
    }

    let executedFightCount = 0;
    const claimedTaskIds = [];

    while (
      executedFightCount < execution.maxFights &&
      Number(infoResult.body?.evoTower?.energy || 0) > 0
    ) {
      const readyResult = await client.runCommand(
        "evotower_readyfight",
        {},
        {
          timeoutMs,
          responseCommand: ["evotower_readyfightresp", "evotower_readyfight"],
        },
      );
      if (!isCommandSuccessful(readyResult)) {
        outcome = buildFailedResult(
          execution,
          readyResult,
          `怪异塔准备战斗失败（第 ${executedFightCount + 1} 次）`,
          {
            originalTeamId,
            switchedFormation,
            restoredFormation: false,
            executedFightCount,
            claimedTaskIds,
            before,
            after: buildWeirdTowerSummary(infoResult),
          },
        );
        return outcome;
      }

      const fightResult = await client.runCommand(
        "evotower_fight",
        {
          battleNum: 1,
          winNum: 1,
        },
        {
          timeoutMs,
          responseCommand: "evotower_fightresp",
        },
      );
      if (!isCommandSuccessful(fightResult)) {
        outcome = buildFailedResult(
          execution,
          fightResult,
          `怪异塔战斗失败（第 ${executedFightCount + 1} 次）`,
          {
            originalTeamId,
            switchedFormation,
            restoredFormation: false,
            executedFightCount,
            claimedTaskIds,
            before,
            after: buildWeirdTowerSummary(infoResult),
          },
        );
        return outcome;
      }

      executedFightCount += 1;
      await wait(execution.intervalMs);

      infoResult = await getWeirdTowerInfo(client, timeoutMs);
      if (!isCommandSuccessful(infoResult)) {
        outcome = buildFailedResult(
          execution,
          infoResult,
          "刷新怪异塔状态失败",
          {
            originalTeamId,
            switchedFormation,
            restoredFormation: false,
            executedFightCount,
            claimedTaskIds,
            before,
            after: buildWeirdTowerSummary(infoResult),
          },
        );
        return outcome;
      }

      const newClaimedTaskIds = await claimWeirdTowerDailyTasks(
        client,
        infoResult.body,
        timeoutMs,
      );
      if (newClaimedTaskIds.length > 0) {
        claimedTaskIds.push(...newClaimedTaskIds);
      }
    }

    const after = buildWeirdTowerSummary(infoResult);
    outcome = {
      ...execution,
      success: true,
      skipped: false,
      code: 0,
      error: "",
      body: infoResult.body,
      originalTeamId,
      switchedFormation,
      restoredFormation: false,
      executedFightCount,
      claimedTaskIds: [...new Set(claimedTaskIds)],
      before,
      after,
      message: `怪异塔已执行 ${executedFightCount} 次战斗`,
    };
    return outcome;
  } finally {
    if (switchedFormation && originalTeamId && originalTeamId !== execution.teamId) {
      try {
        const restoreResult = await client.runCommand(
          "presetteam_saveteam",
          { teamId: originalTeamId },
          { timeoutMs },
        );
        restoredFormation = isCommandSuccessful(restoreResult);
      } catch (_error) {
        restoredFormation = false;
      }
    }
    if (outcome && typeof outcome === "object") {
      outcome.restoredFormation = restoredFormation;
    }
  }
}

async function runSkinChallengeOverviewAction(client, execution, timeoutMs) {
  const infoResult = await getSkinChallengeInfo(client, timeoutMs);
  if (!isCommandSuccessful(infoResult)) {
    return buildFailedResult(
      execution,
      infoResult,
      "获取换皮闯关活动信息失败",
    );
  }

  const snapshot = buildSkinChallengeSnapshot(infoResult);
  if (!snapshot.actId) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      message: "当前未获取到有效的换皮闯关活动信息",
    };
  }

  if (!snapshot.active) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      message: "当前换皮闯关活动未开放或已结束",
    };
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: 0,
    error: "",
    body: infoResult.body,
    ...snapshot,
    message: "换皮闯关状态获取完成",
  };
}

function extractSkinFightCurrentHp(fightBody) {
  return Number(
    fightBody?.battleData?.result?.accept?.ext?.curHP ??
    fightBody?.result?.accept?.ext?.curHP ??
    NaN,
  );
}

async function executeSkinChallengeTarget(
  client,
  execution,
  infoResult,
  targetTowerType,
  timeoutMs,
) {
  let snapshot = buildSkinChallengeSnapshot(infoResult);

  if (!snapshot.todayOpenTowers.includes(targetTowerType)) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      targetTowerType,
      executedFightCount: 0,
      successFightCount: 0,
      failedFightCount: 0,
      beforeLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
      afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
      towerCleared: false,
      _infoResult: infoResult,
      message: `Boss ${targetTowerType} 今日未开放`,
    };
  }

  if (isSkinTowerCleared(targetTowerType, snapshot.levelRewardMap)) {
    const currentLevel = getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap);
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      targetTowerType,
      executedFightCount: 0,
      successFightCount: 0,
      failedFightCount: 0,
      beforeLevel: currentLevel,
      afterLevel: currentLevel,
      towerCleared: true,
      _infoResult: infoResult,
      message: `Boss ${targetTowerType} 已通关`,
    };
  }

  const beforeLevel = getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap);
  let needStart = true;
  let executedFightCount = 0;
  let successFightCount = 0;
  let failedFightCount = 0;

  while (!isSkinTowerCleared(targetTowerType, snapshot.levelRewardMap)) {
    if (needStart) {
      const startResult = await client.runCommand(
        "towers_start",
        { towerType: targetTowerType },
        {
          timeoutMs,
          responseCommand: "towers_startresp",
        },
      );
      if (!isCommandSuccessful(startResult)) {
        return buildFailedResult(
          execution,
          startResult,
          `换皮闯关开始挑战失败（Boss ${targetTowerType}）`,
          {
            ...snapshot,
            targetTowerType,
            executedFightCount,
            successFightCount,
            failedFightCount,
            beforeLevel,
            afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
            towerCleared: false,
          },
        );
      }
      await wait(execution.intervalMs);
    }

    const fightResult = await client.runCommand(
      "towers_fight",
      { towerType: targetTowerType },
      {
        timeoutMs,
        responseCommand: "towers_fightresp",
      },
    );
    if (!isCommandSuccessful(fightResult)) {
      return buildFailedResult(
        execution,
        fightResult,
        `换皮闯关战斗失败（Boss ${targetTowerType}）`,
        {
          ...snapshot,
          targetTowerType,
          executedFightCount,
          successFightCount,
          failedFightCount,
          beforeLevel,
          afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
          towerCleared: false,
        },
      );
    }

    executedFightCount += 1;
    const currentHp = extractSkinFightCurrentHp(fightResult.body);
    if (currentHp === 0) {
      successFightCount += 1;
      needStart = false;
      failedFightCount = 0;
      await wait(execution.intervalMs);
    } else {
      failedFightCount += 1;
      needStart = true;
      if (failedFightCount >= 3) {
        return {
          ...execution,
          success: true,
          skipped: false,
          code: fightResult.code ?? 0,
          error: fightResult.error || "",
          body: fightResult.body,
          ...snapshot,
          targetTowerType,
          executedFightCount,
          successFightCount,
          failedFightCount,
          beforeLevel,
          afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
          towerCleared: false,
          _infoResult: infoResult,
          message: `Boss ${targetTowerType} 连续失败 3 次，已停止本次挑战`,
        };
      }
      await wait(1000);
    }

    infoResult = await getSkinChallengeInfo(client, timeoutMs);
    if (!isCommandSuccessful(infoResult)) {
      return buildFailedResult(
        execution,
        infoResult,
        "刷新换皮闯关状态失败",
        {
          ...snapshot,
          targetTowerType,
          executedFightCount,
          successFightCount,
          failedFightCount,
          beforeLevel,
          afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
          towerCleared: false,
        },
      );
    }
    snapshot = buildSkinChallengeSnapshot(infoResult);
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: 0,
    error: "",
    body: infoResult.body,
    ...snapshot,
    targetTowerType,
    executedFightCount,
    successFightCount,
    failedFightCount,
    beforeLevel,
    afterLevel: getSkinTowerLevel(targetTowerType, snapshot.levelRewardMap),
    towerCleared: true,
    _infoResult: infoResult,
    message: `Boss ${targetTowerType} 已完成当前可挑战层数`,
  };
}

async function runSkinChallengeTowerAction(client, execution, timeoutMs) {
  const infoResult = await getSkinChallengeInfo(client, timeoutMs);
  if (!isCommandSuccessful(infoResult)) {
    return buildFailedResult(
      execution,
      infoResult,
      "获取换皮闯关活动信息失败",
    );
  }

  const snapshot = buildSkinChallengeSnapshot(infoResult);
  if (!snapshot.actId) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      message: "当前未获取到有效的换皮闯关活动信息",
    };
  }
  if (!snapshot.active) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      message: "当前换皮闯关活动未开放或已结束",
    };
  }

  const targetTowerType = execution.towerType || snapshot.pendingTowers[0] || null;
  if (!targetTowerType) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      targetTowerType: null,
      executedFightCount: 0,
      successFightCount: 0,
      failedFightCount: 0,
      beforeLevel: 0,
      afterLevel: 0,
      towerCleared: false,
      message: "当前没有可挑战的换皮闯关 Boss",
    };
  }

  const towerResult = await executeSkinChallengeTarget(
    client,
    execution,
    infoResult,
    targetTowerType,
    timeoutMs,
  );
  delete towerResult._infoResult;
  return towerResult;
}

async function runSkinChallengeTodayAction(client, execution, timeoutMs) {
  let infoResult = await getSkinChallengeInfo(client, timeoutMs);
  if (!isCommandSuccessful(infoResult)) {
    return buildFailedResult(
      execution,
      infoResult,
      "获取换皮闯关活动信息失败",
    );
  }

  let snapshot = buildSkinChallengeSnapshot(infoResult);
  if (!snapshot.actId) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      bossResults: [],
      totalExecutedFightCount: 0,
      completedTowerCount: 0,
      partialTowerCount: 0,
      skippedTowerCount: 0,
      message: "当前未获取到有效的换皮闯关活动信息",
    };
  }
  if (!snapshot.active) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      bossResults: [],
      totalExecutedFightCount: 0,
      completedTowerCount: 0,
      partialTowerCount: 0,
      skippedTowerCount: 0,
      message: "当前换皮闯关活动未开放或已结束",
    };
  }

  const targetTowers = [...snapshot.pendingTowers];
  if (targetTowers.length == 0) {
    return {
      ...execution,
      success: true,
      skipped: true,
      code: 0,
      error: "",
      body: infoResult.body,
      ...snapshot,
      bossResults: [],
      totalExecutedFightCount: 0,
      completedTowerCount: 0,
      partialTowerCount: 0,
      skippedTowerCount: 0,
      message: "今日开放的换皮闯关 Boss 已全部通关",
    };
  }

  const bossResults = [];
  for (const targetTowerType of targetTowers) {
    const towerResult = await executeSkinChallengeTarget(
      client,
      execution,
      infoResult,
      targetTowerType,
      timeoutMs,
    );
    bossResults.push({
      towerType: targetTowerType,
      skipped: Boolean(towerResult.skipped),
      towerCleared: Boolean(towerResult.towerCleared),
      executedFightCount: Number(towerResult.executedFightCount || 0),
      successFightCount: Number(towerResult.successFightCount || 0),
      failedFightCount: Number(towerResult.failedFightCount || 0),
      beforeLevel: Number(towerResult.beforeLevel || 0),
      afterLevel: Number(towerResult.afterLevel || 0),
      message: towerResult.message || "",
    });

    if (!towerResult.success) {
      delete towerResult._infoResult;
      towerResult.bossResults = bossResults;
      towerResult.totalExecutedFightCount = bossResults.reduce(
        (total, item) => total + item.executedFightCount,
        0,
      );
      towerResult.completedTowerCount = bossResults.filter((item) => item.towerCleared).length;
      towerResult.partialTowerCount = bossResults.filter(
        (item) => !item.skipped && !item.towerCleared,
      ).length;
      towerResult.skippedTowerCount = bossResults.filter((item) => item.skipped).length;
      return towerResult;
    }

    if (towerResult._infoResult) {
      infoResult = towerResult._infoResult;
      snapshot = buildSkinChallengeSnapshot(infoResult);
    }
  }

  return {
    ...execution,
    success: true,
    skipped: false,
    code: 0,
    error: "",
    body: infoResult.body,
    ...snapshot,
    bossResults,
    totalExecutedFightCount: bossResults.reduce(
      (total, item) => total + item.executedFightCount,
      0,
    ),
    completedTowerCount: bossResults.filter((item) => item.towerCleared).length,
    partialTowerCount: bossResults.filter(
      (item) => !item.skipped && !item.towerCleared,
    ).length,
    skippedTowerCount: bossResults.filter((item) => item.skipped).length,
    message: `换皮闯关补打完成，已处理 ${bossResults.length} 个 Boss`,
  };
}

export async function executeDungeonAction(client, execution, timeoutMs) {
  if (execution.kind === "bosstower") {
    return await runBossTowerAction(client, execution, timeoutMs);
  }
  if (execution.kind === "dream_team") {
    return await runDreamTeamAction(client, execution, timeoutMs);
  }
  if (execution.kind === "dream_purchase") {
    return await runDreamPurchaseAction(client, execution, timeoutMs);
  }
  if (execution.kind === "weirdtower_overview") {
    return await runWeirdTowerOverviewAction(client, execution, timeoutMs);
  }
  if (execution.kind === "weirdtower_claim_free_energy") {
    return await runWeirdTowerClaimFreeEnergyAction(client, execution, timeoutMs);
  }
  if (execution.kind === "weirdtower_use_items") {
    return await runWeirdTowerUseItemsAction(client, execution, timeoutMs);
  }
  if (execution.kind === "weirdtower_climb") {
    return await runWeirdTowerClimbAction(client, execution, timeoutMs);
  }
  if (execution.kind === "skinchallenge_overview") {
    return await runSkinChallengeOverviewAction(client, execution, timeoutMs);
  }
  if (execution.kind === "skinchallenge_tower") {
    return await runSkinChallengeTowerAction(client, execution, timeoutMs);
  }
  if (execution.kind === "skinchallenge_today") {
    return await runSkinChallengeTodayAction(client, execution, timeoutMs);
  }
  throw badRequest(`未实现的副本动作类型: ${execution.kind}`);
}

export async function runDungeonAction(client, action, options = {}, timeoutMs) {
  const execution = buildDungeonExecution(action, options);
  return await executeDungeonAction(client, execution, timeoutMs);
}
