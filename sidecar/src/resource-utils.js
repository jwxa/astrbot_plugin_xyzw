function badRequest(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function normalizeAction(action) {
  return String(action || "").trim().toLowerCase();
}

function parsePositiveInt(value, defaultValue, fieldName) {
  if (value === undefined || value === null || value === "") {
    return defaultValue;
  }
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    throw badRequest(`${fieldName} 必须是正整数`);
  }
  return Math.min(Math.trunc(numeric), 999);
}

function isCommandSuccessful(result) {
  const rawCode = result?.code;
  if (rawCode === undefined || rawCode === null || rawCode === "") {
    return !result?.error;
  }
  return Number(rawCode) === 0;
}

const RESOURCE_ACTIONS = {
  recruit_free: {
    label: "免费招募",
    command: "hero_recruit",
    buildParams() {
      return {
        recruitType: 3,
        recruitNumber: 1,
      };
    },
  },
  recruit_paid: {
    label: "付费招募",
    command: "hero_recruit",
    buildParams() {
      return {
        recruitType: 1,
        recruitNumber: 1,
      };
    },
  },
  fish_free: {
    label: "免费钓鱼",
    command: "artifact_lottery",
    responseCommand: "syncrewardresp",
    buildParams() {
      return {
        lotteryNumber: 1,
        newFree: true,
        type: 1,
      };
    },
  },
  open_wood_box: {
    label: "开启木质宝箱",
    command: "item_openbox",
    buildParams(options) {
      return {
        itemId: 2001,
        number: parsePositiveInt(options.count, 10, "count"),
      };
    },
  },
  claim_collection_free: {
    label: "领取珍宝阁免费奖励",
    command: "collection_claimfreereward",
    buildParams() {
      return {};
    },
  },
  claim_discount_daily: {
    label: "领取每日礼包",
    command: "discount_claimreward",
    responseCommand: "syncrewardresp",
    buildParams() {
      return {
        discountId: 1,
      };
    },
  },
  blackmarket_purchase: {
    label: "黑市一键采购",
    command: "store_purchase",
    responseCommand: ["store_buyresp", "store_purchase"],
    buildParams() {
      return {};
    },
  },
  legion_holy_shards: {
    label: "购买军团四圣碎片",
    command: "legion_storebuygoods",
    buildParams() {
      return {
        id: 6,
      };
    },
  },
};

export function buildResourceExecution(action, options = {}) {
  const normalizedAction = normalizeAction(action);
  const spec = RESOURCE_ACTIONS[normalizedAction];
  if (!spec) {
    throw badRequest(`不支持的资源动作: ${action}`);
  }

  return {
    action: normalizedAction,
    label: spec.label,
    command: spec.command,
    responseCommand: spec.responseCommand,
    params: spec.buildParams(options && typeof options === "object" ? options : {}),
  };
}

export async function executeResourceAction(client, execution, timeoutMs) {
  const result = await client.runCommand(execution.command, execution.params, {
    timeoutMs,
    responseCommand: execution.responseCommand,
  });

  return {
    ...execution,
    success: isCommandSuccessful(result),
    code: result.code ?? null,
    error: result.error || "",
    body: result.body,
  };
}

export async function runResourceAction(client, action, options = {}, timeoutMs) {
  const execution = buildResourceExecution(action, options);
  return await executeResourceAction(client, execution, timeoutMs);
}
