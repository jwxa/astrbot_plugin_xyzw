import WebSocket from "ws";

import { g_utils } from "../vendor/xyzw/bonProtocol.js";

function normalizeCommandName(value) {
  return String(value || "").trim().toLowerCase();
}

const DEFAULT_ROLE_INFO_BODY = {
  clientVersion: "2.21.2-fa918e1997301834-wx",
  inviteUid: 0,
  platform: "hortor",
  platformExt: "mix",
  scene: "",
};

const DEFAULT_COMMAND_BODIES = new Map([
  ["role_getroleinfo", DEFAULT_ROLE_INFO_BODY],
  ["system_getdatabundlever", { isAudit: false }],
  ["system_custom", { key: "", value: 0 }],
  ["system_buygold", { buyNum: 1 }],
  ["system_claimhangupreward", {}],
  ["system_signinreward", {}],
  ["system_mysharecallback", { isSkipShareCard: true, type: 2 }],
  ["task_claimdailypoint", { taskId: 1 }],
  ["task_claimdailyreward", { rewardId: 0 }],
  ["task_claimweekreward", { rewardId: 0 }],
  ["friend_batch", { friendId: 0 }],
  ["hero_recruit", { byClub: false, recruitNumber: 1, recruitType: 3 }],
  ["item_openbox", { itemId: 2001, number: 10 }],
  ["item_batchclaimboxpointreward", {}],
  ["item_openpack", {}],
  ["mail_claimallattachment", { category: 0 }],
  ["collection_claimfreereward", {}],
  ["discount_claimreward", { discountId: 1 }],
  ["card_claimreward", { cardId: 1 }],
  ["bottlehelper_claim", {}],
  ["bottlehelper_start", { bottleType: -1 }],
  ["bottlehelper_stop", { bottleType: -1 }],
  ["legion_signin", {}],
  ["activity_recyclewarorderrewardclaim", { actId: 1 }],
]);

const RESPONSE_TO_COMMAND_MAP = {
  fight_startpvpresp: "fight_startpvp",
  activity_getresp: "activity_get",
  fight_startlevelresp: "fight_startlevel",
  collection_goodslistresp: "collection_goodslist",
  collection_claimfreerewardresp: "collection_claimfreereward",
  legion_getarearankresp: "legion_getarearank",
  legionwar_getgoldmonthwarrankresp: "legionwar_getgoldmonthwarrank",
  nightmare_getroleinforesp: "nightmare_getroleinfo",
  studyresp: "study_startgame",
  role_getroleinforesp: "role_getroleinfo",
  hero_recruitresp: "hero_recruit",
  friend_batchresp: "friend_batch",
  system_claimhanguprewardresp: "system_claimhangupreward",
  item_openboxresp: ["item_openbox", "item_batchclaimboxpointreward"],
  bottlehelper_claimresp: "bottlehelper_claim",
  bottlehelper_startresp: "bottlehelper_start",
  bottlehelper_stopresp: "bottlehelper_stop",
  legion_signinresp: "legion_signin",
  fight_startbossresp: "fight_startboss",
  fight_startlegionbossresp: "fight_startlegionboss",
  fight_startareaarenaresp: "fight_startareaarena",
  arena_startarearesp: "arena_startarea",
  arena_getareatargetresp: "arena_getareatarget",
  arena_getarearankresp: "arena_getarearank",
  presetteam_saveteamresp: "presetteam_saveteam",
  presetteam_getinforesp: "presetteam_getinfo",
  mail_claimallattachmentresp: "mail_claimallattachment",
  store_buyresp: "store_purchase",
  system_getdatabundleverresp: "system_getdatabundlever",
  tower_claimrewardresp: "tower_claimreward",
  fight_starttowerresp: "fight_starttower",
  evotowerinforesp: "evotower_getinfo",
  evotower_fightresp: "evotower_fight",
  evotower_getlegionjoinmembersresp: "evotower_getlegionjoinmembers",
  mergeboxinforesp: "mergebox_getinfo",
  mergebox_claimfreeenergyresp: "mergebox_claimfreeenergy",
  mergebox_openboxresp: "mergebox_openbox",
  mergebox_automergeitemresp: "mergebox_automergeitem",
  mergebox_mergeitemresp: "mergebox_mergeitem",
  mergebox_claimcostprogressresp: "mergebox_claimcostprogress",
  mergebox_claimmergeprogressresp: "mergebox_claimmergeprogress",
  evotower_claimtaskresp: "evotower_claimtask",
  item_openpackresp: "item_openpack",
  equipment_quenchresp: "equipment_quench",
  rank_getserverrankresp: "rank_getserverrank",
  legion_claimpayloadtaskresp: "legion_claimpayloadtask",
  legion_claimpayloadtaskprogressresp: "legion_claimpayloadtaskprogress",
  saltroad_getwartyperesp: "saltroad_getwartype",
  saltroad_getsaltroadwartotalrankresp: "saltroad_getsaltroadwartotalrank",
  warguess_getrankresp: "warguess_getrank",
  warguess_startguessresp: "warguess_startguess",
  warguess_getguesscoinrewardresp: "warguess_getguesscoinreward",
  league_getbattlefieldresp: "league_getbattlefield",
  league_getgroupopponentresp: "league_getgroupopponent",
  legion_signupresp: "legion_signup",
  legion_payloadsignupresp: "legion_payloadsignup",
  legion_researchresp: "legion_research",
  legion_resetresearchresp: "legion_resetresearch",
  pearl_replaceskillresp: "pearl_replaceskill",
  matchteam_getroleteaminforesp: "matchteam_getroleteaminfo",
  bosstower_getinforesp: "bosstower_getinfo",
  bosstower_startbossresp: "bosstower_startboss",
  bosstower_startboxresp: "bosstower_startbox",
  discount_getdiscountinforesp: "discount_getdiscountinfo",
  hero_heroupgradestarresp: "hero_heroupgradestar",
  hero_rebirthresp: "hero_rebirth",
  hero_heroupgradelevelresp: "hero_heroupgradelevel",
  hero_heroupgradeorderresp: "hero_heroupgradeorder",
  book_upgraderesp: "book_upgrade",
  book_claimpointrewardresp: "book_claimpointreward",
  legion_getinforesp: "legion_getinfo",
  legion_getinforresp: "legion_getinfo",
  car_getrolecarresp: "car_getrolecar",
  car_refreshresp: "car_refresh",
  car_claimresp: "car_claim",
  car_sendresp: "car_send",
  car_getmemberhelpingcntresp: "car_getmemberhelpingcnt",
  car_getmemberrankresp: "car_getmemberrank",
  car_researchresp: "car_research",
  car_claimpartconsumerewardresp: "car_claimpartconsumereward",
  role_gettargetteamresp: "role_gettargetteam",
  activity_warorderclaimresp: "activity_recyclewarorderrewardclaim",
  bosstower_gethelprankresp: "bosstower_gethelprank",
  legacy_getinforesp: "legacy_getinfo",
  legacy_claimhangupresp: "legacy_claimhangup",
  legacy_sendgiftresp: "legacy_sendgift",
  legacy_getgiftsresp: "legacy_getgifts",
  towers_getinforesp: "towers_getinfo",
  towers_startresp: "towers_start",
  towers_fightresp: "towers_fight",
  task_claimdailyrewardresp: "task_claimdailyreward",
  task_claimweekrewardresp: "task_claimweekreward",
  syncresp: [
    "system_mysharecallback",
    "task_claimdailypoint",
    "role_commitpassword",
    "hero_gointobattle",
    "hero_gobackbattle",
    "lordweapon_changedefaultweapon",
  ],
  syncrewardresp: [
    "system_buygold",
    "discount_claimreward",
    "card_claimreward",
    "artifact_lottery",
    "genie_sweep",
    "genie_buysweep",
    "system_signinreward",
    "dungeon_selecthero",
    "artifact_exchange",
    "hero_exchange",
  ],
};

function buildCommandResponseMap() {
  const commandResponseMap = new Map();

  for (const [responseCommand, originalCommands] of Object.entries(
    RESPONSE_TO_COMMAND_MAP,
  )) {
    const normalizedResponse = normalizeCommandName(responseCommand);
    const commands = Array.isArray(originalCommands)
      ? originalCommands
      : [originalCommands];

    for (const command of commands) {
      const normalizedCommand = normalizeCommandName(command);
      if (!normalizedCommand || !normalizedResponse) {
        continue;
      }
      const responseCommands = commandResponseMap.get(normalizedCommand) ?? [];
      responseCommands.push(normalizedResponse);
      commandResponseMap.set(normalizedCommand, responseCommands);
    }
  }

  for (const [command, responseCommands] of commandResponseMap.entries()) {
    commandResponseMap.set(command, [...new Set(responseCommands)]);
  }

  return commandResponseMap;
}

const COMMAND_RESPONSE_MAP = buildCommandResponseMap();
const BATTLE_COMMANDS = new Set([
  "fight_startareaarena",
  "fight_startpvp",
  "fight_starttower",
  "fight_startboss",
  "fight_startlegionboss",
  "fight_startdungeon",
]);
const RANDOM_SEED_XOR_A = 2118920861;
const RANDOM_SEED_XOR_B = 797788954;
const RANDOM_SEED_XOR_C = 1513922175;

function normalizeCommandBody(command, body) {
  const normalizedCommand = normalizeCommandName(command);
  const normalizedBody =
    body && typeof body === "object" && !Array.isArray(body) ? body : {};
  const defaultBody = DEFAULT_COMMAND_BODIES.get(normalizedCommand);

  if (defaultBody) {
    return {
      ...defaultBody,
      ...normalizedBody,
    };
  }

  return normalizedBody;
}

function readStatisticsValue(stats, key) {
  if (!stats) {
    return undefined;
  }
  try {
    if (typeof stats.get === "function") {
      return stats.get(key);
    }
    if (Object.prototype.hasOwnProperty.call(stats, key)) {
      return stats[key];
    }
  } catch (_error) {
    return undefined;
  }
  return undefined;
}

function extractBattleVersion(payload) {
  const candidates = [
    payload?.battleData?.version,
    payload?.battleVersion,
    payload?.role?.battleVersion,
    payload?.version,
  ];
  for (const value of candidates) {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && numeric > 0) {
      return numeric;
    }
  }
  return null;
}

function extractLastLoginTimestamp(payload) {
  if (!payload) {
    return null;
  }

  const candidateSources = [
    payload?.role?.statistics,
    payload?.statistics,
    payload?.role?.statisticsTime,
    payload?.statisticsTime,
  ];
  const candidateKeys = [
    "last:login:time",
    "lastLoginTime",
    "last_login_time",
  ];

  for (const stats of candidateSources) {
    if (!stats) {
      continue;
    }
    for (const key of candidateKeys) {
      const value = readStatisticsValue(stats, key);
      if (value === undefined || value === null) {
        continue;
      }
      const numeric = Number(value);
      if (Number.isFinite(numeric) && numeric > 0) {
        return numeric;
      }
    }
  }

  return null;
}

function generateRandomSeed(lastLoginTime) {
  if (lastLoginTime === undefined || lastLoginTime === null) {
    return 0;
  }

  const numericTime = Number(lastLoginTime);
  if (!Number.isFinite(numericTime)) {
    return 0;
  }

  let seed = numericTime | 0;
  seed ^= RANDOM_SEED_XOR_A;
  seed = ((seed << 16) | (seed >>> 16)) >>> 0;
  seed ^= RANDOM_SEED_XOR_B;
  seed ^= RANDOM_SEED_XOR_C;
  return seed >>> 0;
}

function toArrayBuffer(data) {
  if (data instanceof ArrayBuffer) {
    return data;
  }
  if (Buffer.isBuffer(data)) {
    return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
  }
  if (ArrayBuffer.isView(data)) {
    return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength);
  }
  throw new Error("无法处理的 WebSocket 二进制消息类型");
}

function stringifyCloseReason(reason) {
  if (typeof reason === "string") {
    return reason;
  }
  if (Buffer.isBuffer(reason)) {
    return reason.toString("utf8");
  }
  return String(reason ?? "");
}

function clampTimeoutMs(value, fallback = 10000) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return fallback;
  }
  return Math.min(Math.max(Math.trunc(numeric), 1000), 180000);
}

function normalizeResponseCommands(command, responseCommand) {
  const normalizedCommand = normalizeCommandName(command);
  const values = [];
  if (Array.isArray(responseCommand)) {
    values.push(...responseCommand);
  } else if (responseCommand) {
    values.push(responseCommand);
  }
  const mappedResponseCommands = COMMAND_RESPONSE_MAP.get(normalizedCommand);
  if (mappedResponseCommands) {
    values.push(...mappedResponseCommands);
  }
  if (normalizedCommand) {
    values.push(`${normalizedCommand}resp`);
    values.push(normalizedCommand);
  }

  return [...new Set(
    values
      .map(normalizeCommandName)
      .filter(Boolean),
  )];
}

function describeReadyState(ws) {
  const state = ws?.readyState;
  switch (state) {
    case WebSocket.CONNECTING:
      return "CONNECTING";
    case WebSocket.OPEN:
      return "OPEN";
    case WebSocket.CLOSING:
      return "CLOSING";
    case WebSocket.CLOSED:
      return "CLOSED";
    default:
      return "UNINITIALIZED";
  }
}

function isSocketUnavailableError(error) {
  const message =
    error instanceof Error ? error.message : String(error ?? "");
  return [
    "websocket 已关闭",
    "websocket 未连接",
    "not opened",
    "readystate",
    "socket closed",
  ].some((keyword) => message.toLowerCase().includes(keyword));
}

export function buildWebSocketUrl(tokenString) {
  return `wss://xxz-xyzw.hortorgames.com/agent?p=${encodeURIComponent(tokenString)}&e=x&lang=chinese`;
}

export function summarizeRoleInfo(roleInfo) {
  const role = roleInfo?.role ?? {};
  return {
    roleId: role.id ?? null,
    roleName: role.name ?? role.roleName ?? role.nickname ?? null,
    serverName:
      role.serverName ?? roleInfo?.serverName ?? role.server ?? roleInfo?.server ?? null,
    level: role.level ?? null,
    vipLevel: role.vipLevel ?? role.vip?.level ?? null,
    headImg: role.headImg ?? null,
    hangUpMinutes: role.hangUpMinute ?? role.hangup?.minute ?? null,
    towerId: role.tower?.id ?? null,
    studyMaxCorrectNum: role.study?.maxCorrectNum ?? null,
    legionName: roleInfo?.legion?.name ?? role.legion?.name ?? null,
  };
}

export class XyzwWsClient {
  constructor(options) {
    this.url = options.url;
    this.timeoutMs = clampTimeoutMs(options.timeoutMs);
    this.ws = null;
    this.connected = false;
    this.ack = 0;
    this.seq = 1;
    this.pending = new Map();
    this.pendingByResp = new Map();
    this._closed = false;
    this.connectionPromise = null;
    this.lastCloseError = null;
    this.battleVersion = null;
    this.randomSeedSynced = false;
    this.lastRandomSeedSource = null;
    this.lastRandomSeed = null;
  }

  isSocketOpen() {
    return Boolean(this.ws && this.ws.readyState === WebSocket.OPEN);
  }

  async connect(forceReconnect = false) {
    if (forceReconnect) {
      await this.close();
    } else if (this.isSocketOpen()) {
      return;
    }

    if (this.connectionPromise) {
      await this.connectionPromise;
      return;
    }

    const connectionPromise = new Promise((resolve, reject) => {
      let opened = false;
      let settled = false;
      const ws = new WebSocket(this.url);
      this.ws = ws;
      this.connected = false;
      this._closed = false;
      this.ack = 0;
      this.seq = 1;
      this.randomSeedSynced = false;
      this.lastRandomSeed = null;

      const cleanup = () => {
        ws.removeAllListeners("open");
        ws.removeAllListeners("error");
      };

      const resolveOnce = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve();
      };

      const rejectOnce = (error) => {
        if (settled) {
          return;
        }
        settled = true;
        reject(error);
      };

      ws.on("open", () => {
        opened = true;
        cleanup();
        this.connected = true;
        this.lastCloseError = null;
        resolveOnce();
      });

      ws.on("error", (error) => {
        this.connected = false;
        if (!opened) {
          cleanup();
          if (this.ws === ws) {
            this.ws = null;
          }
          rejectOnce(error);
        }
      });

      ws.on("message", (data) => {
        this._handleMessage(data);
      });

      ws.on("close", (code, reason) => {
        this.connected = false;
        const closeReason = stringifyCloseReason(reason);
        const error = new Error(
          `WebSocket 已关闭: code=${code}, reason=${closeReason || "empty"}`,
        );
        this.lastCloseError = error;
        if (this.ws === ws) {
          this.ws = null;
        }
        if (!opened) {
          cleanup();
          rejectOnce(error);
          return;
        }
        this._rejectAll(error);
      });
    });

    this.connectionPromise = connectionPromise;
    try {
      await connectionPromise;
    } finally {
      if (this.connectionPromise === connectionPromise) {
        this.connectionPromise = null;
      }
    }
  }

  async ensureConnected(forceReconnect = false) {
    if (forceReconnect || !this.isSocketOpen()) {
      await this.connect(forceReconnect);
    }
  }

  async close() {
    this._closed = true;
    this.connected = false;
    const ws = this.ws;
    this.ws = null;
    if (!ws) {
      return;
    }
    if (
      ws.readyState === WebSocket.CLOSING ||
      ws.readyState === WebSocket.CLOSED
    ) {
      return;
    }

    await new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) {
          return;
        }
        settled = true;
        resolve();
      };
      ws.once("close", finish);
      ws.close(1000, "normal");
      setTimeout(finish, 1000);
    });
  }

  async fetchRoleInfo(timeoutMs = this.timeoutMs) {
    const result = await this.runCommand("role_getroleinfo", {}, {
      timeoutMs,
      responseCommand: "role_getroleinforesp",
    });
    this._updateRoleContext(result.body);
    await this._syncRandomSeedFromRoleInfo(result.body);
    try {
      await this.fetchBattleVersion(timeoutMs);
    } catch (_error) {
      // 非战斗命令链路允许缺省 battleVersion，真正需要时再显式失败。
    }
    return result.body;
  }

  async fetchBattleVersion(timeoutMs = this.timeoutMs) {
    const result = await this.runCommand("fight_startlevel", {}, {
      timeoutMs,
      responseCommand: "fight_startlevelresp",
    });
    const battleVersion = extractBattleVersion(result.body);
    if (battleVersion) {
      this.battleVersion = battleVersion;
    }
    return this.battleVersion;
  }

  async runCommand(command, body = {}, options = {}) {
    const reconnectRetries = Math.max(
      0,
      Number(options.reconnectRetries ?? 1),
    );
    const normalizedCommand = normalizeCommandName(command);
    let lastError = null;

    for (let attempt = 0; attempt <= reconnectRetries; attempt += 1) {
      try {
        await this.ensureConnected(attempt > 0);
        if (!this.randomSeedSynced && this.lastRandomSeedSource) {
          await this._syncRandomSeedFromTimestamp(this.lastRandomSeedSource);
        }
        if (BATTLE_COMMANDS.has(normalizedCommand) && !this.battleVersion) {
          await this.fetchRoleInfo(options.timeoutMs ?? this.timeoutMs);
          if (!this.battleVersion) {
            await this.fetchBattleVersion(options.timeoutMs ?? this.timeoutMs);
          }
        }
        const message = await this.sendCommand(command, body, options);
        if (normalizedCommand === "role_getroleinfo") {
          this._updateRoleContext(message.getData());
          await this._syncRandomSeedFromRoleInfo(message.getData());
        } else if (normalizedCommand === "fight_startlevel") {
          const battleVersion = extractBattleVersion(message.getData());
          if (battleVersion) {
            this.battleVersion = battleVersion;
          }
        }
        return {
          cmd: message.cmd,
          seq: message.seq ?? 0,
          ack: message.ack ?? 0,
          code: message.code ?? 0,
          error: message.error ?? "",
          body: message.getData(),
        };
      } catch (error) {
        lastError = error;
        if (attempt >= reconnectRetries || !isSocketUnavailableError(error)) {
          throw error;
        }
      }
    }

    throw lastError ?? new Error(`命令执行失败: ${command}`);
  }

  async sendCommand(command, body, options = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error(
        `WebSocket 未连接: readyState=${describeReadyState(this.ws)}`,
      );
    }

    const timeoutMs = clampTimeoutMs(options.timeoutMs, this.timeoutMs);
    const responseCommands = normalizeResponseCommands(
      command,
      options.responseCommand,
    );
    const preparedBody = this._prepareCommandBody(command, body);
    const packet = {
      ack: this.ack,
      seq: this.seq++,
      time: Date.now(),
      cmd: command,
      body: g_utils.bon.encode(preparedBody),
    };

    const encoded = g_utils.encode(packet, "x");

    return await new Promise((resolve, reject) => {
      const pendingEntry = {
        resolve,
        reject,
        responseSeq: packet.seq,
        responseCommands,
        timeoutId: null,
      };
      const timeoutId = setTimeout(() => {
        this._clearPendingEntry(pendingEntry);
        reject(new Error(`请求超时: ${command}`));
      }, timeoutMs);
      pendingEntry.timeoutId = timeoutId;
      this.pendingByResp.set(packet.seq, pendingEntry);
      for (const responseCommand of responseCommands) {
        this.pending.set(responseCommand, pendingEntry);
      }
      this.ws.send(encoded, (error) => {
        if (!error) {
          return;
        }
        clearTimeout(timeoutId);
        this._clearPendingEntry(pendingEntry);
        reject(error);
      });
    });
  }

  async send(command, body = {}) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error(
        `WebSocket 未连接: readyState=${describeReadyState(this.ws)}`,
      );
    }

    const packet = {
      ack: this.ack,
      seq: this.seq++,
      time: Date.now(),
      cmd: command,
      body: g_utils.bon.encode(this._prepareCommandBody(command, body)),
    };
    const encoded = g_utils.encode(packet, "x");
    await new Promise((resolve, reject) => {
      this.ws.send(encoded, (error) => {
        if (error) {
          reject(error);
          return;
        }
        resolve();
      });
    });
    return {
      cmd: command,
      seq: packet.seq,
      ack: packet.ack,
    };
  }

  _prepareCommandBody(command, body) {
    const normalizedCommand = normalizeCommandName(command);
    const normalizedBody = normalizeCommandBody(command, body);
    if (
      BATTLE_COMMANDS.has(normalizedCommand) &&
      !Object.prototype.hasOwnProperty.call(normalizedBody, "battleVersion") &&
      this.battleVersion
    ) {
      return {
        battleVersion: this.battleVersion,
        ...normalizedBody,
      };
    }
    return normalizedBody;
  }

  _updateRoleContext(roleInfo) {
    const battleVersion = extractBattleVersion(roleInfo);
    if (battleVersion) {
      this.battleVersion = battleVersion;
    }
    const lastLoginTime = extractLastLoginTimestamp(roleInfo);
    if (lastLoginTime) {
      this.lastRandomSeedSource = lastLoginTime;
    }
  }

  async _syncRandomSeedFromRoleInfo(roleInfo) {
    const lastLoginTime = extractLastLoginTimestamp(roleInfo);
    if (!lastLoginTime) {
      return;
    }
    this.lastRandomSeedSource = lastLoginTime;
    await this._syncRandomSeedFromTimestamp(lastLoginTime);
  }

  async _syncRandomSeedFromTimestamp(lastLoginTime) {
    if (!lastLoginTime) {
      return;
    }
    if (
      this.randomSeedSynced &&
      this.lastRandomSeedSource === lastLoginTime
    ) {
      return;
    }
    const randomSeed = generateRandomSeed(lastLoginTime);
    await this.send("system_custom", {
      key: "randomSeed",
      value: randomSeed,
    });
    this.randomSeedSynced = true;
    this.lastRandomSeedSource = lastLoginTime;
    this.lastRandomSeed = randomSeed;
  }

  _handleMessage(data) {
    try {
      const message = g_utils.parse(toArrayBuffer(data), "auto");
      if (message.seq) {
        this.ack = message.seq;
      }

      const responseSeq = Number(message.resp);
      if (Number.isFinite(responseSeq) && this.pendingByResp.has(responseSeq)) {
        const pendingEntry = this.pendingByResp.get(responseSeq);
        clearTimeout(pendingEntry.timeoutId);
        this._clearPendingEntry(pendingEntry);
        pendingEntry.resolve(message);
        return;
      }

      const command = message.cmd?.toLowerCase();
      if (command && this.pending.has(command)) {
        const pendingEntry = this.pending.get(command);
        clearTimeout(pendingEntry.timeoutId);
        this._clearPendingEntry(pendingEntry);
        pendingEntry.resolve(message);
      }
    } catch (error) {
      this._rejectAll(error);
    }
  }

  _clearPendingEntry(pendingEntry) {
    if (!pendingEntry) {
      return;
    }

    if (this.pendingByResp.get(pendingEntry.responseSeq) === pendingEntry) {
      this.pendingByResp.delete(pendingEntry.responseSeq);
    }

    for (const responseCommand of pendingEntry.responseCommands) {
      if (this.pending.get(responseCommand) === pendingEntry) {
        this.pending.delete(responseCommand);
      }
    }
  }

  _rejectAll(error) {
    const processedEntries = new Set();
    for (const pendingEntry of this.pending.values()) {
      if (processedEntries.has(pendingEntry)) {
        continue;
      }
      processedEntries.add(pendingEntry);
      clearTimeout(pendingEntry.timeoutId);
      pendingEntry.reject(error);
    }
    this.pending.clear();
    this.pendingByResp.clear();
  }
}
