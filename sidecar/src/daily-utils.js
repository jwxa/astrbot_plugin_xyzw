function readMapValue(source, key) {
  if (!source) {
    return undefined;
  }
  if (typeof source.get === "function") {
    return source.get(key);
  }
  return source[key];
}

function isTaskCompleted(completeMap, taskId) {
  const value = readMapValue(completeMap, taskId) ?? readMapValue(completeMap, String(taskId));
  return Number(value) === -1;
}

function normalizeTimestampSeconds(value) {
  const numeric = Number(value || 0);
  if (!numeric) {
    return 0;
  }
  return numeric > 1e12 ? Math.trunc(numeric / 1000) : Math.trunc(numeric);
}

function isTodayAvailable(statisticsTimeValue, now = new Date()) {
  const timestampSeconds = normalizeTimestampSeconds(statisticsTimeValue);
  if (!timestampSeconds) {
    return true;
  }
  const today = now.toDateString();
  const recordDate = new Date(timestampSeconds * 1000).toDateString();
  return today !== recordDate;
}

function createStep(taskKey, description, command, params = {}, options = {}) {
  return {
    taskKey,
    description,
    command,
    params,
    responseCommand: options.responseCommand,
    softFail: Boolean(options.softFail),
  };
}

function clampInteger(value, fallback, minimum, maximum) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return fallback;
  }
  return Math.min(maximum, Math.max(minimum, Math.trunc(numeric)));
}

function hasOwn(source, key) {
  return Boolean(source && Object.prototype.hasOwnProperty.call(source, key));
}

function resolveDailyCount(rawOptions, keys, fallback) {
  for (const key of keys) {
    if (hasOwn(rawOptions, key)) {
      return rawOptions[key];
    }
  }
  return fallback;
}

function isCommandSuccessful(result) {
  const rawCode = result?.code;
  if (rawCode === undefined || rawCode === null || rawCode === "") {
    return !result?.error;
  }
  return Number(rawCode) === 0;
}

function sleep(ms) {
  const numeric = Number(ms || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => setTimeout(resolve, Math.trunc(numeric)));
}

function isHangupDailyStep(step) {
  const taskKey = String(step?.taskKey || "");
  return (
    taskKey === "claim_hangup" ||
    taskKey.startsWith("claim_hangup_") ||
    taskKey.startsWith("hangup_share_")
  );
}

const DAILY_TASK_STATUS_DEFINITIONS = [
  { id: 1, name: "登录一次游戏" },
  { id: 2, name: "分享一次游戏" },
  { id: 3, name: "赠送好友3次金币" },
  { id: 4, name: "进行2次招募" },
  { id: 5, name: "领取5次挂机奖励" },
  { id: 6, name: "进行3次点金" },
  { id: 7, name: "开启3次宝箱" },
  { id: 12, name: "黑市购买1次物品（请设置采购清单）" },
  { id: 13, name: "进行1场竞技场战斗" },
  { id: 14, name: "收获1个任意盐罐" },
];

function buildDailyTaskSnapshot(roleInfo) {
  const role = roleInfo?.role ?? {};
  const completeMap = role.dailyTask?.complete ?? {};
  const dailyPoint = Number(role.dailyTask?.dailyPoint ?? 0) || 0;
  const tasks = DAILY_TASK_STATUS_DEFINITIONS.map((task) => ({
    taskId: task.id,
    name: task.name,
    completed: isTaskCompleted(completeMap, task.id),
  }));
  return {
    dailyPoint,
    maxDailyPoint: 100,
    completedCount: tasks.filter((task) => task.completed).length,
    totalCount: tasks.length,
    tasks,
  };
}

function pickArenaTargetId(targets) {
  if (!targets) {
    return null;
  }

  if (Array.isArray(targets)) {
    const candidate = targets[0];
    return candidate?.roleId || candidate?.id || candidate?.targetId || null;
  }

  const candidate =
    targets?.rankList?.[0] ||
    targets?.roleList?.[0] ||
    targets?.targets?.[0] ||
    targets?.targetList?.[0] ||
    targets?.list?.[0];

  if (candidate) {
    return candidate.roleId || candidate.id || candidate.targetId || null;
  }

  return targets?.roleId || targets?.id || targets?.targetId || null;
}

async function executeArenaDailyTask(client, timeoutMs, battleIndex = 1, battleCount = 1) {
  const hour = new Date().getHours();
  if (hour < 6) {
    return {
      status: "skipped",
      code: null,
      message: "当前时间未到 06:00，已跳过竞技场战斗",
    };
  }
  if (hour > 22) {
    return {
      status: "skipped",
      code: null,
      message: "当前时间已过 22:00，已跳过竞技场战斗",
    };
  }

  if (battleIndex === 1) {
    const startResult = await client.runCommand(
      "arena_startarea",
      {},
      {
        timeoutMs,
        responseCommand: "arena_startarearesp",
      },
    );
    if (!isCommandSuccessful(startResult)) {
      return {
        status: "failed",
        code: startResult.code ?? null,
        message: startResult.error || `code=${startResult.code ?? "unknown"}`,
      };
    }
  }

  const targetResult = await client.runCommand(
    "arena_getareatarget",
    {},
    {
      timeoutMs,
      responseCommand: "arena_getareatargetresp",
    },
  );
  if (!isCommandSuccessful(targetResult)) {
    return {
      status: "failed",
      code: targetResult.code ?? null,
      message: targetResult.error || `code=${targetResult.code ?? "unknown"}`,
    };
  }

  const targetId = pickArenaTargetId(targetResult.body);
  if (!targetId) {
    return {
      status: "skipped",
      code: null,
      message: `竞技场战斗 ${battleIndex}/${battleCount} 未找到可用目标，已跳过`,
    };
  }

  const fightResult = await client.runCommand(
    "fight_startareaarena",
    { targetId },
    {
      timeoutMs,
      responseCommand: "fight_startareaarenaresp",
    },
  );
  if (!isCommandSuccessful(fightResult)) {
    return {
      status: "failed",
      code: fightResult.code ?? null,
      message: fightResult.error || `code=${fightResult.code ?? "unknown"}`,
    };
  }

  return {
    status: "success",
    code: Number(fightResult.code ?? 0),
    message: "",
  };
}

async function executeDailyPlanStep(client, step, timeoutMs) {
  if (step.command === "__arena_daily__") {
    return executeArenaDailyTask(
      client,
      timeoutMs,
      Number(step.params?.battleIndex || 1),
      Number(step.params?.battleCount || 1),
    );
  }
  return client.runCommand(step.command, step.params, {
    responseCommand: step.responseCommand,
    timeoutMs,
  });
}

async function executeDailyPlanStepWithRetry(client, step, timeoutMs) {
  const retryable = isHangupDailyStep(step);
  const maxAttempts = retryable ? 2 : 1;
  let lastError = null;

  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const result = await executeDailyPlanStep(client, step, timeoutMs);
      if (
        retryable &&
        attempt < maxAttempts &&
        result &&
        !result.status &&
        !isCommandSuccessful(result)
      ) {
        await sleep(1000);
        continue;
      }
      return result;
    } catch (error) {
      lastError = error;
      if (!retryable || attempt >= maxAttempts) {
        throw error;
      }
      await sleep(1000);
    }
  }

  throw lastError ?? new Error(`步骤执行失败: ${step?.description || step?.taskKey || 'unknown'}`);
}

export function buildSimpleDailyPlan(roleInfo, rawOptions = {}) {
  const options = {
    shareGame: true,
    friendGold: true,
    freeRecruit: true,
    payRecruit: false,
    freeBuyGold: true,
    claimHangUp: true,
    addHangUpTime: true,
    addHangUpTimes: 4,
    openWoodBox: true,
    openWoodBoxCount: 10,
    bottleTimer: true,
    claimBottle: true,
    signIn: true,
    legionSignIn: true,
    claimMail: true,
    claimTaskRewards: true,
    claimPassReward: true,
    claimDiscountReward: true,
    claimCollectionFreeReward: true,
    claimCardReward: true,
    claimPermanentCardReward: true,
    blackMarketPurchase: true,
    arenaBattle: true,
    ...rawOptions,
  };

  const role = roleInfo?.role ?? {};
  const completeMap = role.dailyTask?.complete ?? {};
  const statistics = role.statistics ?? roleInfo?.statistics ?? {};
  const statisticsTime = role.statisticsTime ?? roleInfo?.statisticsTime ?? {};
  const plan = [];
  const recruitCount = clampInteger(
    resolveDailyCount(
      rawOptions,
      ["recruitCount"],
      options.freeRecruit ? (options.payRecruit ? 2 : 1) : 0,
    ),
    0,
    0,
    20,
  );
  const hangUpClaimCount = clampInteger(
    resolveDailyCount(
      rawOptions,
      ["hangUpClaimCount"],
      options.claimHangUp ? 1 : 0,
    ),
    0,
    0,
    5,
  );
  const blackMarketPurchaseCount = clampInteger(
    resolveDailyCount(
      rawOptions,
      ["blackMarketPurchaseCount"],
      options.blackMarketPurchase ? 1 : 0,
    ),
    0,
    0,
    20,
  );
  const arenaBattleCount = clampInteger(
    resolveDailyCount(
      rawOptions,
      ["arenaBattleCount"],
      options.arenaBattle ? 1 : 0,
    ),
    0,
    0,
    3,
  );

  if (options.shareGame && !isTaskCompleted(completeMap, 2)) {
    plan.push(
      createStep(
        "share_game",
        "分享游戏",
        "system_mysharecallback",
        { isSkipShareCard: true, type: 2 },
        { responseCommand: "syncresp" },
      ),
    );
  }

  if (options.friendGold && !isTaskCompleted(completeMap, 3)) {
    plan.push(
      createStep("friend_gold", "赠送好友金币", "friend_batch", {
        friendId: 0,
      }),
    );
  }

  if (recruitCount > 0 && !isTaskCompleted(completeMap, 4)) {
    plan.push(
      createStep(
        "free_recruit",
        recruitCount > 1 ? "免费招募 1/1" : "免费招募",
        "hero_recruit",
        { recruitType: 3, recruitNumber: 1 },
      ),
    );
    for (let index = 1; index < recruitCount; index += 1) {
      plan.push(
        createStep(
          `paid_recruit_${index}`,
          `付费招募 ${index}/${recruitCount - 1}`,
          "hero_recruit",
          { recruitType: 1, recruitNumber: 1 },
        ),
      );
    }
  }

  if (
    options.freeBuyGold &&
    !isTaskCompleted(completeMap, 6) &&
    isTodayAvailable(readMapValue(statisticsTime, "buy:gold"))
  ) {
    for (let index = 0; index < 3; index += 1) {
      plan.push(
        createStep(
          `buy_gold_${index + 1}`,
          `免费点金 ${index + 1}/3`,
          "system_buygold",
          { buyNum: 1 },
        ),
      );
    }
  }

  if (hangUpClaimCount > 0 && !isTaskCompleted(completeMap, 5)) {
    if (hangUpClaimCount === 1) {
      plan.push(
        createStep(
          "claim_hangup",
          "领取挂机奖励",
          "system_claimhangupreward",
          {},
          { softFail: true },
        ),
      );
      if (options.addHangUpTime) {
        for (let index = 0; index < Number(options.addHangUpTimes || 0); index += 1) {
          plan.push(
            createStep(
              `hangup_share_${index + 1}`,
              `挂机加钟 ${index + 1}/${options.addHangUpTimes}`,
              "system_mysharecallback",
              { isSkipShareCard: true, type: 2 },
              { responseCommand: "syncresp", softFail: true },
            ),
          );
        }
      }
    } else {
      const shareTimes = options.addHangUpTime
        ? Math.min(
            Number(options.addHangUpTimes || 0),
            Math.max(0, hangUpClaimCount - 1),
          )
        : 0;
      for (let index = 0; index < hangUpClaimCount; index += 1) {
        plan.push(
          createStep(
            `claim_hangup_${index + 1}`,
            `领取挂机奖励 ${index + 1}/${hangUpClaimCount}`,
            "system_claimhangupreward",
            {},
            { softFail: true },
          ),
        );
        if (index < shareTimes) {
          plan.push(
            createStep(
              `hangup_share_${index + 1}`,
              `挂机加钟 ${index + 1}/${shareTimes}`,
              "system_mysharecallback",
              { isSkipShareCard: true, type: 2 },
              { responseCommand: "syncresp", softFail: true },
            ),
          );
        }
      }
    }
  }

  if (options.openWoodBox && !isTaskCompleted(completeMap, 7)) {
    plan.push(
      createStep(
        "open_wood_box",
        `开启木质宝箱 ${options.openWoodBoxCount} 个`,
        "item_openbox",
        { itemId: 2001, number: Number(options.openWoodBoxCount || 10) },
        { softFail: true },
      ),
    );
  }

  if (options.bottleTimer) {
    plan.push(
      createStep(
        "bottle_stop",
        "停止盐罐计时",
        "bottlehelper_stop",
        { bottleType: -1 },
        { softFail: true },
      ),
    );
    plan.push(
      createStep(
        "bottle_start",
        "开始盐罐计时",
        "bottlehelper_start",
        { bottleType: -1 },
        { softFail: true },
      ),
    );
  }

  if (options.claimBottle && !isTaskCompleted(completeMap, 14)) {
    plan.push(
      createStep(
        "bottle_claim",
        "领取盐罐奖励",
        "bottlehelper_claim",
        {},
        { softFail: true },
      ),
    );
  }

  const freeFishTime =
    readMapValue(statistics, "artifact:normal:lottery:time") ??
    readMapValue(statisticsTime, "artifact:normal:lottery:time");
  if (isTodayAvailable(freeFishTime)) {
    for (let index = 0; index < 3; index += 1) {
      plan.push(
        createStep(
          `free_fish_${index + 1}`,
          `免费钓鱼 ${index + 1}/3`,
          "artifact_lottery",
          { lotteryNumber: 1, newFree: true, type: 1 },
          { responseCommand: "syncrewardresp", softFail: true },
        ),
      );
    }
  }

  if (blackMarketPurchaseCount > 0 && !isTaskCompleted(completeMap, 12)) {
    for (let index = 0; index < blackMarketPurchaseCount; index += 1) {
      plan.push(
        createStep(
          `blackmarket_purchase_${index + 1}`,
          blackMarketPurchaseCount > 1
            ? `黑市购买物品 ${index + 1}/${blackMarketPurchaseCount}`
            : "黑市购买1次物品",
          "store_purchase",
          {},
          {
            responseCommand: ["store_buyresp", "store_purchase"],
            softFail: true,
          },
        ),
      );
    }
  }

  if (arenaBattleCount > 0 && !isTaskCompleted(completeMap, 13)) {
    for (let index = 0; index < arenaBattleCount; index += 1) {
      plan.push({
        taskKey: `arena_daily_${index + 1}`,
        description:
          arenaBattleCount > 1
            ? `竞技场战斗 ${index + 1}/${arenaBattleCount}`
            : "进行1场竞技场战斗",
        command: "__arena_daily__",
        params: { battleIndex: index + 1, battleCount: arenaBattleCount },
        responseCommand: undefined,
        softFail: true,
      });
    }
  }

  if (options.signIn) {
    plan.push(
      createStep(
        "system_signinreward",
        "福利签到",
        "system_signinreward",
        {},
        { responseCommand: "syncrewardresp", softFail: true },
      ),
    );
  }

  if (options.legionSignIn) {
    plan.push(
      createStep(
        "legion_signin",
        "俱乐部签到",
        "legion_signin",
        {},
        { softFail: true },
      ),
    );
  }

  if (options.claimDiscountReward) {
    plan.push(
      createStep(
        "discount_claimreward",
        "领取每日礼包",
        "discount_claimreward",
        { discountId: 1 },
        { responseCommand: "syncrewardresp", softFail: true },
      ),
    );
  }

  if (options.claimCollectionFreeReward) {
    plan.push(
      createStep(
        "collection_claimfreereward",
        "领取免费奖励",
        "collection_claimfreereward",
        {},
        { softFail: true },
      ),
    );
  }

  if (options.claimCardReward) {
    plan.push(
      createStep(
        "card_claimreward",
        "领取免费礼包",
        "card_claimreward",
        { cardId: 1 },
        { responseCommand: "syncrewardresp", softFail: true },
      ),
    );
  }

  if (options.claimPermanentCardReward) {
    plan.push(
      createStep(
        "card_claimreward_4003",
        "领取永久卡礼包",
        "card_claimreward",
        { cardId: 4003 },
        { responseCommand: "syncrewardresp", softFail: true },
      ),
    );
  }

  if (options.claimMail) {
    plan.push(
      createStep(
        "mail_claimallattachment",
        "领取邮件奖励",
        "mail_claimallattachment",
        { category: 0 },
        { softFail: true },
      ),
    );
  }

  if (options.claimTaskRewards) {
    for (let taskId = 1; taskId <= 10; taskId += 1) {
      plan.push(
        createStep(
          `task_claimdailypoint_${taskId}`,
          `领取日常积分奖励第 ${taskId} 档`,
          "task_claimdailypoint",
          { taskId },
          { responseCommand: "syncresp", softFail: true },
        ),
      );
    }
    plan.push(
      createStep(
        "task_claimdailyreward",
        "领取日常任务奖励",
        "task_claimdailyreward",
        { rewardId: 0 },
        { responseCommand: "task_claimdailyrewardresp", softFail: true },
      ),
    );
    plan.push(
      createStep(
        "task_claimweekreward",
        "领取周常任务奖励",
        "task_claimweekreward",
        { rewardId: 0 },
        { responseCommand: "task_claimweekrewardresp", softFail: true },
      ),
    );
  }

  if (options.claimPassReward) {
    plan.push(
      createStep(
        "activity_recyclewarorderrewardclaim",
        "领取通行证奖励",
        "activity_recyclewarorderrewardclaim",
        { actId: 1 },
        { responseCommand: "activity_warorderclaimresp", softFail: true },
      ),
    );
  }

  return plan;
}

export async function runSimpleDailyPlan(client, roleInfo, options = {}, timeoutMs) {
  const plan = buildSimpleDailyPlan(roleInfo, options);
  const steps = [];

  for (const step of plan) {
    try {
      if (typeof client.ensureConnected === "function") {
        await client.ensureConnected();
      }
      const result = await executeDailyPlanStepWithRetry(client, step, timeoutMs);

      if (result?.status === "success") {
        steps.push({
          taskKey: step.taskKey,
          description: step.description,
          command: step.command,
          status: "success",
          code: Number(result.code ?? 0),
        });
      } else if (result?.status === "skipped" || result?.status === "failed") {
        steps.push({
          taskKey: step.taskKey,
          description: step.description,
          command: step.command,
          status: result.status,
          code: result.code ?? null,
          message: result.message || (result.code ?? null),
        });
      } else if (isCommandSuccessful(result)) {
        steps.push({
          taskKey: step.taskKey,
          description: step.description,
          command: step.command,
          status: "success",
          code: Number(result.code ?? 0),
        });
      } else {
        steps.push({
          taskKey: step.taskKey,
          description: step.description,
          command: step.command,
          status: step.softFail ? "skipped" : "failed",
          code: result.code ?? null,
          message: result.error || `code=${result.code ?? "unknown"}`,
        });
      }
    } catch (error) {
      steps.push({
        taskKey: step.taskKey,
        description: step.description,
        command: step.command,
        status: "failed",
        code: null,
        message: error instanceof Error ? error.message : String(error),
      });
    }

    if (isHangupDailyStep(step)) {
      await sleep(1000);
    }
  }

  let finalRoleInfo = roleInfo;
  try {
    if (typeof client.fetchRoleInfo === "function") {
      finalRoleInfo = await client.fetchRoleInfo(timeoutMs);
    }
  } catch (_error) {
    finalRoleInfo = roleInfo;
  }

  return {
    planCode: "simple-daily",
    totalCount: plan.length,
    successCount: steps.filter((step) => step.status === "success").length,
    skippedCount: steps.filter((step) => step.status === "skipped").length,
    failedCount: steps.filter((step) => step.status === "failed").length,
    initialDailyTaskSnapshot: buildDailyTaskSnapshot(roleInfo),
    finalDailyTaskSnapshot: buildDailyTaskSnapshot(finalRoleInfo),
    finalRoleInfo,
    steps,
  };
}
