import { readFile } from "node:fs/promises";

import { summarizeCars } from "./car-utils.js";

const STUDY_ANSWER_JSON_URL = new URL(
  "../../_refs/xyzw_web_helper/public/answer.json",
  import.meta.url,
);
const STUDY_WEEKLY_TARGET = 10;
const MONTHLY_FISH_TARGET = 320;
const MONTHLY_ARENA_TARGET = 240;
const MAX_FISH_BATCH = 10;
const MAX_MONTHLY_FISH_ITERATIONS = 40;
const MAX_CAR_REFRESH_ATTEMPTS = 12;
const RACING_REFRESH_TICKET_ITEM_ID = 35002;
const CAR_PARTS_ITEM_ID = 35009;
const SUPER_CAR_REFRESH_THRESHOLD = 1;
const MAX_NEW_SMART_SEND_REFRESH_ATTEMPTS = 5;
const BIG_PRIZES = [
  { type: 3, itemId: 3201, value: 10 },
  { type: 3, itemId: 1001, value: 10 },
  { type: 3, itemId: 1022, value: 2000 },
  { type: 2, itemId: 0, value: 2000 },
  { type: 3, itemId: 1023, value: 5 },
  { type: 3, itemId: 1022, value: 2500 },
  { type: 3, itemId: 1001, value: 12 },
];

let studyQuestionBankPromise = null;

function sleep(ms) {
  const numeric = Number(ms || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    setTimeout(resolve, Math.trunc(numeric));
  });
}

function normalizeQuestionText(value) {
  return String(value || "").replace(/\s+/g, "").toLowerCase();
}

function matchQuestion(questionFromDb, actualQuestion) {
  const cleanDb = normalizeQuestionText(questionFromDb);
  const cleanActual = normalizeQuestionText(actualQuestion);
  if (!cleanDb || !cleanActual) {
    return false;
  }
  return cleanActual.includes(cleanDb) || cleanDb.includes(cleanActual);
}

async function loadStudyQuestionBank() {
  if (!studyQuestionBankPromise) {
    studyQuestionBankPromise = readFile(STUDY_ANSWER_JSON_URL, "utf-8")
      .then((content) => JSON.parse(content))
      .then((data) => (Array.isArray(data) ? data : []))
      .catch(() => []);
  }
  return await studyQuestionBankPromise;
}

async function findStudyAnswer(questionText) {
  const questions = await loadStudyQuestionBank();
  for (const item of questions) {
    if (!item?.name || item?.value === undefined || item?.value === null) {
      continue;
    }
    if (matchQuestion(item.name, questionText)) {
      return Number(item.value) || 1;
    }
  }
  return null;
}

function buildWeekStart(value = new Date()) {
  const current = new Date(value);
  current.setHours(0, 0, 0, 0);
  const weekday = (current.getDay() + 6) % 7;
  current.setDate(current.getDate() - weekday);
  return current;
}

function isInCurrentWeek(timestampMs, now = new Date()) {
  const numeric = Number(timestampMs || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return false;
  }
  const weekStart = buildWeekStart(now);
  const nextWeekStart = new Date(weekStart);
  nextWeekStart.setDate(nextWeekStart.getDate() + 7);
  const target = new Date(numeric);
  return target >= weekStart && target < nextWeekStart;
}

function readStatisticsValue(stats, key) {
  if (!stats) {
    return undefined;
  }
  if (typeof stats.get === "function") {
    return stats.get(key);
  }
  if (Object.prototype.hasOwnProperty.call(stats, key)) {
    return stats[key];
  }
  return undefined;
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
  const today = new Date(now);
  today.setHours(0, 0, 0, 0);
  return timestampSeconds < Math.floor(today.getTime() / 1000);
}

function getRoleItemQuantity(roleInfo, itemId) {
  const items = roleInfo?.role?.items || {};
  const item = items?.[itemId] || items?.[String(itemId)] || {};
  return Number(item.quantity || 0) || 0;
}

function getPaidCarExpireTime(roleInfo) {
  const statisticsTime =
    roleInfo?.role?.statisticsTime ||
    roleInfo?.statisticsTime ||
    {};
  const value = readStatisticsValue(statisticsTime, "paid:car:s");
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
}

function getRemainingSecondsFromExpire(expireTime, nowMs = Date.now()) {
  const numeric = Number(expireTime || 0);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return 0;
  }
  const expireMs = numeric > 1e12 ? numeric : numeric * 1000;
  return Math.max(0, Math.floor((expireMs - nowMs) / 1000));
}

function hasSuperCarUnlocked(roleInfo, nowMs = Date.now()) {
  return getRemainingSecondsFromExpire(getPaidCarExpireTime(roleInfo), nowMs) > 0;
}

async function fetchRoleInfoBody(client, timeoutMs) {
  const result = await client.runCommand("role_getroleinfo", {}, {
    timeoutMs,
    responseCommand: "role_getroleinforesp",
  });
  return result.body || {};
}

async function fetchMonthlyActivity(client, timeoutMs) {
  const result = await client.runCommand("activity_get", {}, {
    timeoutMs,
    responseCommand: "activity_getresp",
  });
  return result.body?.activity || result.body || {};
}

function readMonthlyFishCount(activity) {
  return Number(activity?.myMonthInfo?.["2"]?.num || 0) || 0;
}

function readMonthlyArenaCount(activity) {
  return Number(activity?.myArenaInfo?.num || 0) || 0;
}

function countRacingRefreshTickets(rewards) {
  if (!Array.isArray(rewards)) {
    return 0;
  }
  return rewards.reduce((count, reward) => {
    if (Number(reward?.type || 0) === 3 && Number(reward?.itemId || 0) === RACING_REFRESH_TICKET_ITEM_ID) {
      return count + (Number(reward?.value || 0) || 0);
    }
    return count;
  }, 0);
}

function isBigPrize(rewards) {
  if (!Array.isArray(rewards)) {
    return false;
  }
  return BIG_PRIZES.some((prize) =>
    rewards.some(
      (reward) =>
        Number(reward?.type || 0) === prize.type &&
        Number(reward?.itemId || 0) === prize.itemId &&
        Number(reward?.value || 0) >= prize.value,
    ),
  );
}

function shouldSendCar(carInfo, refreshTickets) {
  const color = Number(carInfo?.color || 0);
  const rewards = Array.isArray(carInfo?.rewards) ? carInfo.rewards : [];
  const racingTicketsCount = countRacingRefreshTickets(rewards);

  if (Number(refreshTickets || 0) >= 6) {
    return color >= 5 || racingTicketsCount >= 4 || isBigPrize(rewards);
  }
  return color >= 4 || racingTicketsCount >= 2 || isBigPrize(rewards);
}

async function fetchCarState(client, timeoutMs) {
  const [carResult, roleInfo] = await Promise.all([
    client.runCommand("car_getrolecar", {}, {
      timeoutMs,
      responseCommand: "car_getrolecarresp",
    }),
    fetchRoleInfoBody(client, timeoutMs),
  ]);
  const overview = summarizeCars(carResult.body);
  return {
    roleInfo,
    rawCars: carResult.body || {},
    overview,
    refreshTickets: getRoleItemQuantity(roleInfo, RACING_REFRESH_TICKET_ITEM_ID),
    partsCount: getRoleItemQuantity(roleInfo, CAR_PARTS_ITEM_ID),
    superCarExpireTime: getPaidCarExpireTime(roleInfo),
    superCarRemainingSeconds: getRemainingSecondsFromExpire(
      getPaidCarExpireTime(roleInfo),
    ),
    superCarUnlocked: hasSuperCarUnlocked(roleInfo),
  };
}

async function refreshCar(client, carId, timeoutMs) {
  return await client.runCommand(
    "car_refresh",
    { carId: String(carId) },
    {
      timeoutMs,
      responseCommand: "car_refreshresp",
    },
  );
}

async function upgradeCarResearch(client, timeoutMs) {
  return await client.runCommand(
    "car_research",
    { researchId: 1 },
    {
      timeoutMs,
      responseCommand: "car_researchresp",
    },
  );
}

async function sendCar(client, carId, timeoutMs) {
  return await client.runCommand(
    "car_send",
    {
      carId: String(carId),
      helperId: 0,
      text: "",
    },
    {
      timeoutMs,
      responseCommand: "car_sendresp",
    },
  );
}

function buildSmartCarCandidateMessage(car, statusText, helperId, note = "") {
  const lines = [
    `${car?.gradeLabel || "未知"} id=${car?.id || "-"}`,
    `状态: ${statusText}`,
    `护卫ID: ${helperId || "-"}`,
    `发车时间: ${car?.sendAtText || "-"}`,
  ];
  if (note) {
    lines.push(`备注: ${note}`);
  }
  return lines.join(" | ");
}

function computeClaimableInSeconds(car, nowMs = Date.now()) {
  const sentAt = Number(car?.sendAt || 0);
  if (!sentAt) {
    return 0;
  }
  const sentAtMs = sentAt < 1e12 ? sentAt * 1000 : sentAt;
  return Math.max(0, Math.ceil((4 * 60 * 60 * 1000 - (nowMs - sentAtMs)) / 1000));
}

function normalizeHelperMemberList(result) {
  const helpers = Array.isArray(result?.helpers) ? result.helpers : [];
  return helpers.map((item) => ({
    roleId: String(item?.roleId || "").trim(),
    displayName: String(item?.displayName || item?.name || item?.nickname || "").trim(),
    usedCount: Number(item?.usedCount || 0) || 0,
    maxCount: Number(item?.maxCount || 4) || 4,
    availableCount: Number(item?.availableCount || 0) || 0,
    isAvailable: Boolean(item?.isAvailable ?? (Number(item?.availableCount || 0) > 0)),
    isFull: Boolean(item?.isFull ?? (Number(item?.usedCount || 0) >= Number(item?.maxCount || 4))),
    redQuench: Number(item?.redQuench || 0) || 0,
    power: Number(item?.power || 0) || 0,
  }));
}

function pickHelperFromWhitelist(helpers, whitelist) {
  const normalizedWhitelist = (Array.isArray(whitelist) ? whitelist : [])
    .map((item) => String(item || "").trim())
    .filter(Boolean);
  for (const candidate of normalizedWhitelist) {
    const found = helpers.find((helper) => helper.roleId === candidate && helper.isAvailable);
    if (found) {
      return found;
    }
  }
  return null;
}

function pickRandomAvailableHelper(helpers) {
  const available = helpers.filter((helper) => helper.isAvailable);
  if (!available.length) {
    return null;
  }
  return available[Math.floor(Math.random() * available.length)];
}

async function assignHelperId({
  client,
  tokenId,
  car,
  helpers,
  helperWhitelist,
  timeoutMs,
}) {
  const explicitHelperId = String(car?.helperId || "").trim();
  if (explicitHelperId) {
    return {
      helperId: explicitHelperId,
      helperSource: "car",
      helperNote: "车上已有护卫",
    };
  }

  const fromWhitelist = pickHelperFromWhitelist(helpers, helperWhitelist);
  if (fromWhitelist) {
    return {
      helperId: fromWhitelist.roleId,
      helperSource: "whitelist",
      helperNote: `白名单优先: ${fromWhitelist.displayName || fromWhitelist.roleId}`,
    };
  }

  const randomHelper = pickRandomAvailableHelper(helpers);
  if (randomHelper) {
    return {
      helperId: randomHelper.roleId,
      helperSource: "random",
      helperNote: `随机可用护卫: ${randomHelper.displayName || randomHelper.roleId}`,
    };
  }

  return {
    helperId: 0,
    helperSource: "none",
    helperNote: "无可用护卫",
  };
}

export async function runWeeklyStudyTask(client, timeoutMs) {
  const questionBank = await loadStudyQuestionBank();
  const beforeRoleInfo = await fetchRoleInfoBody(client, timeoutMs);
  const beforeStudy = beforeRoleInfo?.role?.study || {};
  const beforeMaxCorrectNum = Number(beforeStudy.maxCorrectNum || 0) || 0;
  const beforeBeginTimeMs = Number(beforeStudy.beginTime || 0) * 1000;
  if (
    beforeMaxCorrectNum >= STUDY_WEEKLY_TARGET &&
    isInCurrentWeek(beforeBeginTimeMs)
  ) {
    return {
      alreadyCompleted: true,
      completedThisWeek: true,
      questionBankSize: questionBank.length,
      questionCount: 0,
      answeredCount: 0,
      usedDefaultCount: 0,
      rewardAttempts: 0,
      beforeMaxCorrectNum,
      afterMaxCorrectNum: beforeMaxCorrectNum,
    };
  }

  const startResult = await client.runCommand("study_startgame", {}, {
    timeoutMs,
    responseCommand: ["studyresp", "study_startgame", "study_startgameresp"],
  });
  const questionList = Array.isArray(startResult.body?.questionList)
    ? startResult.body.questionList
    : [];
  const studyId =
    startResult.body?.role?.study?.id ||
    beforeStudy.id ||
    startResult.body?.studyId;

  if (!questionList.length || !studyId) {
    throw new Error("未获取到可用的大冲关题目");
  }

  let usedDefaultCount = 0;
  for (let index = 0; index < questionList.length; index += 1) {
    const question = questionList[index] || {};
    let answer = await findStudyAnswer(question.question || "");
    if (answer === null) {
      answer = 1;
      usedDefaultCount += 1;
    }
    await client.send("study_answer", {
      id: studyId,
      option: [answer],
      questionId: [question.id],
    });
    if (index < questionList.length - 1) {
      await sleep(300);
    }
  }

  await sleep(1500);
  let rewardAttempts = 0;
  for (let rewardId = 1; rewardId <= 10; rewardId += 1) {
    await client.send("study_claimreward", { rewardId });
    rewardAttempts += 1;
    await sleep(200);
  }

  await sleep(1000);
  const afterRoleInfo = await fetchRoleInfoBody(client, timeoutMs);
  const afterStudy = afterRoleInfo?.role?.study || {};
  const afterMaxCorrectNum = Number(afterStudy.maxCorrectNum || 0) || 0;
  const afterBeginTimeMs = Number(afterStudy.beginTime || 0) * 1000;

  return {
    alreadyCompleted: false,
    completedThisWeek:
      afterMaxCorrectNum >= STUDY_WEEKLY_TARGET &&
      isInCurrentWeek(afterBeginTimeMs),
    questionBankSize: questionBank.length,
    questionCount: questionList.length,
    answeredCount: questionList.length,
    usedDefaultCount,
    rewardAttempts,
    beforeMaxCorrectNum,
    afterMaxCorrectNum,
  };
}

export async function runMonthlyFishProgressTask(client, timeoutMs) {
  let roleInfo = await fetchRoleInfoBody(client, timeoutMs);
  let activity = await fetchMonthlyActivity(client, timeoutMs);
  const beforeFishNum = readMonthlyFishCount(activity);
  const beforeArenaNum = readMonthlyArenaCount(activity);

  if (beforeFishNum >= MONTHLY_FISH_TARGET) {
    return {
      alreadyCompleted: true,
      completed: true,
      target: MONTHLY_FISH_TARGET,
      arenaTarget: MONTHLY_ARENA_TARGET,
      beforeFishNum,
      afterFishNum: beforeFishNum,
      beforeArenaNum,
      afterArenaNum: beforeArenaNum,
      freeUsed: 0,
      paidCount: 0,
      iterations: 0,
    };
  }

  const statisticsTime =
    roleInfo?.role?.statisticsTime ||
    roleInfo?.statisticsTime ||
    {};
  const lastFreeTime = readStatisticsValue(
    statisticsTime,
    "artifact:normal:lottery:time",
  );

  let freeUsed = 0;
  if (isTodayAvailable(lastFreeTime)) {
    for (let index = 0; index < 3; index += 1) {
      try {
        await client.runCommand(
          "artifact_lottery",
          {
            lotteryNumber: 1,
            newFree: true,
            type: 1,
          },
          {
            timeoutMs,
            responseCommand: "syncrewardresp",
          },
        );
        freeUsed += 1;
        await sleep(500);
      } catch (_error) {
        break;
      }
    }
    if (freeUsed > 0) {
      roleInfo = await fetchRoleInfoBody(client, timeoutMs);
      activity = await fetchMonthlyActivity(client, timeoutMs);
    }
  }

  let currentFishNum = readMonthlyFishCount(activity);
  let iterations = 0;
  let paidCount = 0;
  while (
    currentFishNum < MONTHLY_FISH_TARGET &&
    iterations < MAX_MONTHLY_FISH_ITERATIONS
  ) {
    const batch = Math.min(MAX_FISH_BATCH, MONTHLY_FISH_TARGET - currentFishNum);
    await client.runCommand(
      "artifact_lottery",
      {
        lotteryNumber: batch,
        newFree: true,
        type: 1,
      },
      {
        timeoutMs,
        responseCommand: "syncrewardresp",
      },
    );
    paidCount += batch;
    iterations += 1;
    await sleep(800);
    activity = await fetchMonthlyActivity(client, timeoutMs);
    const updatedFishNum = readMonthlyFishCount(activity);
    if (updatedFishNum <= currentFishNum) {
      break;
    }
    currentFishNum = updatedFishNum;
  }

  return {
    alreadyCompleted: false,
    completed: currentFishNum >= MONTHLY_FISH_TARGET,
    target: MONTHLY_FISH_TARGET,
    arenaTarget: MONTHLY_ARENA_TARGET,
    beforeFishNum,
    afterFishNum: currentFishNum,
    beforeArenaNum,
    afterArenaNum: readMonthlyArenaCount(activity),
    freeUsed,
    paidCount,
    iterations,
  };
}

export async function runSmartCarSendTask(client, timeoutMs) {
  let state = await fetchCarState(client, timeoutMs);
  const before = state.overview;
  const refreshTicketsBefore = state.refreshTickets;
  const idleCars = [...(before.cars || [])]
    .filter((car) => car.status === "idle")
    .sort((left, right) => Number(left.slot || 0) - Number(right.slot || 0));

  const sentCars = [];
  const refreshedCars = [];
  const failures = [];

  if (idleCars.length <= 0) {
    return {
      processedCount: 0,
      idleCountBefore: before.summary?.idleCars || 0,
      idleCountAfter: before.summary?.idleCars || 0,
      refreshTicketsBefore,
      refreshTicketsAfter: state.refreshTickets,
      before,
      after: before,
      sentCars,
      refreshedCars,
      failures,
    };
  }

  for (const initialCar of idleCars) {
    let refreshAttempts = 0;
    while (true) {
      const currentCar = (state.overview.cars || []).find(
        (item) => String(item.id || "") === String(initialCar.id || ""),
      );
      if (!currentCar || currentCar.status !== "idle") {
        break;
      }

      if (shouldSendCar(currentCar, state.refreshTickets)) {
        try {
          await sendCar(client, currentCar.id, timeoutMs);
          sentCars.push({
            carId: String(currentCar.id || ""),
            color: Number(currentCar.color || 0),
            gradeLabel: currentCar.gradeLabel || "",
            reason: "matched",
          });
        } catch (error) {
          failures.push({
            carId: String(currentCar.id || ""),
            stage: "send",
            message: error instanceof Error ? error.message : String(error),
          });
        }
        await sleep(500);
        state = await fetchCarState(client, timeoutMs);
        break;
      }

      const refreshCount = Number(currentCar.refreshCount || 0);
      const canUseTicketRefresh = Number(state.refreshTickets || 0) >= 6;
      const canUseFreeRefresh = refreshCount === 0;
      if (
        refreshAttempts >= MAX_CAR_REFRESH_ATTEMPTS ||
        (!canUseTicketRefresh && !canUseFreeRefresh)
      ) {
        try {
          await sendCar(client, currentCar.id, timeoutMs);
          sentCars.push({
            carId: String(currentCar.id || ""),
            color: Number(currentCar.color || 0),
            gradeLabel: currentCar.gradeLabel || "",
            reason:
              refreshAttempts >= MAX_CAR_REFRESH_ATTEMPTS
                ? "max_refresh_reached"
                : "fallback_send",
          });
        } catch (error) {
          failures.push({
            carId: String(currentCar.id || ""),
            stage: "send",
            message: error instanceof Error ? error.message : String(error),
          });
        }
        await sleep(500);
        state = await fetchCarState(client, timeoutMs);
        break;
      }

      try {
        await refreshCar(client, currentCar.id, timeoutMs);
        refreshedCars.push({
          carId: String(currentCar.id || ""),
          attempt: refreshAttempts + 1,
        });
      } catch (error) {
        failures.push({
          carId: String(currentCar.id || ""),
          stage: "refresh",
          message: error instanceof Error ? error.message : String(error),
        });
        break;
      }
      refreshAttempts += 1;
      await sleep(500);
      state = await fetchCarState(client, timeoutMs);
    }
  }

  return {
    processedCount: idleCars.length,
    idleCountBefore: before.summary?.idleCars || 0,
    idleCountAfter: state.overview.summary?.idleCars || 0,
    refreshTicketsBefore,
    refreshTicketsAfter: state.refreshTickets,
    before,
    after: state.overview,
    sentCars,
    refreshedCars,
    failures,
  };
}

function formatSendTimestamp(sendAt) {
  const numeric = Number(sendAt || 0);
  if (!numeric) {
    return "-";
  }
  const ms = numeric < 1e12 ? numeric * 1000 : numeric;
  const dt = new Date(ms);
  const month = String(dt.getMonth() + 1).padStart(2, "0");
  const day = String(dt.getDate()).padStart(2, "0");
  const hour = String(dt.getHours()).padStart(2, "0");
  const minute = String(dt.getMinutes()).padStart(2, "0");
  const second = String(dt.getSeconds()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}:${second}`;
}

function formatEtaTimestamp(sendAt) {
  const numeric = Number(sendAt || 0);
  if (!numeric) {
    return "-";
  }
  const sentMs = numeric < 1e12 ? numeric * 1000 : numeric;
  const eta = new Date(sentMs + 4 * 60 * 60 * 1000);
  const month = String(eta.getMonth() + 1).padStart(2, "0");
  const day = String(eta.getDate()).padStart(2, "0");
  const hour = String(eta.getHours()).padStart(2, "0");
  const minute = String(eta.getMinutes()).padStart(2, "0");
  const second = String(eta.getSeconds()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}:${second}`;
}

async function fetchLegionHelperState(client, timeoutMs) {
  const roleInfo = await fetchRoleInfoBody(client, timeoutMs);
  const [legionResult, helpingCountResult] = await Promise.all([
    client.runCommand("legion_getinfo", {}, {
      timeoutMs,
      responseCommand: "legion_getinforesp",
    }),
    client.runCommand("car_getmemberhelpingcnt", {}, {
      timeoutMs,
      responseCommand: "car_getmemberhelpingcntresp",
    }),
  ]);
  const helperSnapshot = buildCarHelperSnapshot({
    roleInfo,
    legionInfo: legionResult.body,
    helpingCountInfo: helpingCountResult.body,
    includeSelf: false,
  });
  return {
    roleInfo,
    legionInfo: legionResult.body,
    helperUsage: helpingCountResult.body,
    helperSnapshot,
    helpers: normalizeHelperMemberList(helperSnapshot),
  };
}

async function refreshSmartCarContext(client, timeoutMs) {
  const [carState, helperState] = await Promise.all([
    fetchCarState(client, timeoutMs),
    fetchLegionHelperState(client, timeoutMs),
  ]);
  return {
    ...carState,
    ...helperState,
  };
}

async function performSendWithRetry({
  client,
  carId,
  helperId,
  timeoutMs,
}) {
  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const response = await client.runCommand(
        "car_send",
        {
          carId: String(carId),
          helperId: helperId ? String(helperId) : 0,
          text: "",
          isUpgrade: false,
        },
        {
          timeoutMs,
          responseCommand: "car_sendresp",
        },
      );
      return {
        ok: true,
        response,
        attempts: attempt,
      };
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await sleep(500);
      }
    }
  }
  return {
    ok: false,
    attempts: 2,
    error: lastError instanceof Error ? lastError.message : String(lastError),
  };
}

async function performRefreshWithRetry({
  client,
  carId,
  timeoutMs,
}) {
  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const response = await refreshCar(client, carId, timeoutMs);
      return {
        ok: true,
        response,
        attempts: attempt,
      };
    } catch (error) {
      lastError = error;
      if (attempt < 2) {
        await sleep(500);
      }
    }
  }
  return {
    ok: false,
    attempts: 2,
    error: lastError instanceof Error ? lastError.message : String(lastError),
  };
}

export async function runManualSmartCarSendTask(
  client,
  {
    helper_whitelist = [],
    max_cars = 4,
  } = {},
  timeoutMs,
) {
  let state = await refreshSmartCarContext(client, timeoutMs);
  const before = state.overview;
  const refreshTicketsBefore = state.refreshTickets;
  const superCarUnlockedBefore = state.superCarUnlocked;
  const idleCars = [...(before.cars || [])]
    .filter((car) => car.status === "idle")
    .sort((left, right) => Number(left.slot || 0) - Number(right.slot || 0))
    .slice(0, Math.max(0, Number(max_cars || 4) || 4));

  const details = [];
  const failures = [];

  for (const initialCar of idleCars) {
    let refreshAttempts = 0;
    let lastHelperId = 0;
    let lastHelperNote = "";
    let sent = false;
    let currentStatus = "已经发车";
    let remark = "";

    while (!sent) {
      const currentCar = (state.overview.cars || []).find(
        (item) => String(item.id || "") === String(initialCar.id || ""),
      );
      if (!currentCar || currentCar.status !== "idle") {
        currentStatus = "已经发车";
        break;
      }

      const helperDecision = await assignHelperId({
        client,
        tokenId: "",
        car: currentCar,
        helpers: state.helpers || [],
        helperWhitelist: helper_whitelist,
        timeoutMs,
      });
      lastHelperId = helperDecision.helperId;
      lastHelperNote = helperDecision.helperNote;

      if (shouldSendCar(currentCar, state.refreshTickets)) {
        const sendResult = await performSendWithRetry({
          client,
          carId: currentCar.id,
          helperId: lastHelperId,
          timeoutMs,
        });
        if (sendResult.ok) {
          await sleep(500);
          state = await refreshSmartCarContext(client, timeoutMs);
          const sentCar = (state.overview.cars || []).find(
            (item) => String(item.id || "") === String(currentCar.id || ""),
          );
          currentStatus = sendResult.attempts > 1 ? "本次发车(重试成功)" : "本次发车";
          remark = lastHelperNote;
          details.push({
            carId: String(currentCar.id || ""),
            gradeLabel: currentCar.gradeLabel || "",
            color: Number(currentCar.color || 0),
            status: currentStatus,
            sendAtText: formatSendTimestamp(sentCar?.sendAt),
            etaText: formatEtaTimestamp(sentCar?.sendAt),
            helperId: lastHelperId || 0,
            note: remark,
          });
          sent = true;
          break;
        }

        currentStatus = "发车失败";
        remark = `发车失败: ${sendResult.error || "未知错误"}`;
        failures.push({
          carId: String(currentCar.id || ""),
          stage: "send",
          message: remark,
        });
        details.push({
          carId: String(currentCar.id || ""),
          gradeLabel: currentCar.gradeLabel || "",
          color: Number(currentCar.color || 0),
          status: currentStatus,
          sendAtText: "-",
          etaText: "-",
          helperId: lastHelperId || 0,
          note: remark,
        });
        break;
      }

      const freeRefresh = Number(currentCar.refreshCount || 0) === 0;
      const canUseTicketRefresh = Number(state.refreshTickets || 0) >= 1;
      const canUseSuperCarRefresh =
        Boolean(state.superCarUnlocked) &&
        !freeRefresh &&
        !canUseTicketRefresh &&
        refreshAttempts < SUPER_CAR_REFRESH_THRESHOLD;

      if (
        refreshAttempts >= MAX_NEW_SMART_SEND_REFRESH_ATTEMPTS ||
        (!freeRefresh && !canUseTicketRefresh && !canUseSuperCarRefresh)
      ) {
        const sendResult = await performSendWithRetry({
          client,
          carId: currentCar.id,
          helperId: lastHelperId,
          timeoutMs,
        });
        if (sendResult.ok) {
          await sleep(500);
          state = await refreshSmartCarContext(client, timeoutMs);
          const sentCar = (state.overview.cars || []).find(
            (item) => String(item.id || "") === String(currentCar.id || ""),
          );
          currentStatus = sendResult.attempts > 1 ? "本次发车(重试成功)" : "本次发车";
          remark =
            refreshAttempts >= MAX_NEW_SMART_SEND_REFRESH_ATTEMPTS
              ? "达到刷新上限后直接发车"
              : lastHelperNote || "无可继续刷新，直接发车";
          details.push({
            carId: String(currentCar.id || ""),
            gradeLabel: currentCar.gradeLabel || "",
            color: Number(currentCar.color || 0),
            status: currentStatus,
            sendAtText: formatSendTimestamp(sentCar?.sendAt),
            etaText: formatEtaTimestamp(sentCar?.sendAt),
            helperId: lastHelperId || 0,
            note: remark,
          });
          sent = true;
          break;
        }

        currentStatus = "发车失败";
        remark = `发车失败: ${sendResult.error || "未知错误"}`;
        failures.push({
          carId: String(currentCar.id || ""),
          stage: "send",
          message: remark,
        });
        details.push({
          carId: String(currentCar.id || ""),
          gradeLabel: currentCar.gradeLabel || "",
          color: Number(currentCar.color || 0),
          status: currentStatus,
          sendAtText: "-",
          etaText: "-",
          helperId: lastHelperId || 0,
          note: remark,
        });
        break;
      }

      const refreshResult = await performRefreshWithRetry({
        client,
        carId: currentCar.id,
        timeoutMs,
      });
      if (!refreshResult.ok) {
        failures.push({
          carId: String(currentCar.id || ""),
          stage: "refresh",
          message: `刷新失败: ${refreshResult.error || "未知错误"}`,
        });
        currentStatus = "发车失败";
        remark = `刷新失败: ${refreshResult.error || "未知错误"}`;
        details.push({
          carId: String(currentCar.id || ""),
          gradeLabel: currentCar.gradeLabel || "",
          color: Number(currentCar.color || 0),
          status: currentStatus,
          sendAtText: "-",
          etaText: "-",
          helperId: lastHelperId || 0,
          note: remark,
        });
        break;
      }

      refreshAttempts += 1;
      await sleep(500);
      state = await refreshSmartCarContext(client, timeoutMs);
    }
  }

  return {
    processedCount: idleCars.length,
    idleCountBefore: before.summary?.idleCars || 0,
    idleCountAfter: state.overview.summary?.idleCars || 0,
    refreshTicketsBefore,
    refreshTicketsAfter: state.refreshTickets,
    superCarUnlocked: superCarUnlockedBefore,
    superCarExpireTime: state.superCarExpireTime || 0,
    superCarRemainingSeconds: state.superCarRemainingSeconds || 0,
    helperWhitelist: Array.isArray(helper_whitelist) ? helper_whitelist : [],
    before,
    after: state.overview,
    details,
    failures,
    helpers: state.helpers || [],
  };
}
