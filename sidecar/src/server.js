import { createServer } from "node:http";
import process from "node:process";

import { sendJson, readJsonBody, sendNoContent } from "./utils/http.js";
import { buildCarHelperSnapshot } from "./car-helper-utils.js";
import { findCarById, summarizeCars } from "./car-utils.js";
import { runSimpleDailyPlan } from "./daily-utils.js";
import {
  buildDungeonExecution,
  executeDungeonAction,
} from "./dungeon-utils.js";
import {
  buildResourceExecution,
  executeResourceAction,
} from "./resource-utils.js";
import {
  buildRoleBin,
  buildWsReadyToken,
  decodeBase64ToArrayBuffer,
  normalizeServerRoles,
  parseBinPayload,
} from "./utils/bin.js";
import {
  buildTokenSourceUrls,
  TokenSourceStore,
} from "./token-source-store.js";
import { normalizeIncomingToken } from "./utils/token.js";
import {
  buildWechatBin,
  loginWechatCode,
  pollWechatQrcodeStatus,
  startWechatQrcode,
} from "./wechat-auth.js";
import {
  buildWebSocketUrl,
  summarizeRoleInfo,
  XyzwWsClient,
} from "./xyzw-client.js";
import { requestAuthUser, requestServerList } from "./xyzw-auth.js";

const SERVICE_NAME = "astrbot-plugin-xyzw-sidecar";
const SERVICE_VERSION = "0.15.1";
const startedAt = Date.now();
const tokenSourceStore = new TokenSourceStore();

function parsePort(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    return 8099;
  }
  return Math.trunc(value);
}

function parseHost(raw) {
  if (typeof raw !== "string") {
    return "127.0.0.1";
  }
  const value = raw.trim();
  return value || "127.0.0.1";
}

function buildCorsHeaders() {
  const allowOrigin = process.env.XYZW_SIDECAR_ALLOW_ORIGIN || "*";
  return {
    "Access-Control-Allow-Origin": allowOrigin,
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers":
      "Content-Type,Authorization,X-Requested-With",
  };
}

function success(data) {
  return {
    ok: true,
    code: "OK",
    message: "success",
    data,
  };
}

function failure(message, code = "BAD_REQUEST", statusCode = 400, detail = undefined) {
  return {
    statusCode,
    body: {
      ok: false,
      code,
      message,
      detail,
    },
  };
}

function isCommandSuccessful(result) {
  const rawCode = result?.code;
  if (rawCode === undefined || rawCode === null || rawCode === "") {
    return !result?.error;
  }
  return Number(rawCode) === 0;
}

function assertCommandSuccessful(result, label) {
  if (isCommandSuccessful(result)) {
    return;
  }
  const error = new Error(
    `${label}失败: ${result?.error || result?.code || "未知错误"}`,
  );
  error.statusCode = 409;
  throw error;
}

function resolvePublicBaseUrl(request) {
  const explicitBaseUrl = String(
    process.env.XYZW_SIDECAR_PUBLIC_BASE_URL || "",
  ).trim();
  if (explicitBaseUrl) {
    return explicitBaseUrl.replace(/\/+$/, "");
  }

  const forwardedProto = String(
    request.headers["x-forwarded-proto"] || "",
  ).trim();
  const forwardedHost = String(
    request.headers["x-forwarded-host"] || "",
  ).trim();
  const hostHeader = forwardedHost || String(request.headers.host || "").trim();
  const protocol = forwardedProto || "http";
  return `${protocol}://${hostHeader || `127.0.0.1:${port}`}`.replace(/\/+$/, "");
}

function buildTokenSourcePayload(publicBaseUrl, source) {
  const urls = buildTokenSourceUrls(publicBaseUrl, source);
  return {
    sourceId: source.source_id,
    sourceType: source.source_type,
    requiresServerId: Boolean(source.requires_server_id),
    refreshUrl: urls.refreshUrl,
    refreshUrlTemplate: urls.refreshUrlTemplate,
  };
}

function resolveSourceServerId(source, requestedServerId) {
  if (requestedServerId !== undefined && requestedServerId !== null && requestedServerId !== "") {
    return requestedServerId;
  }
  if (source?.metadata?.server_id !== undefined && source?.metadata?.server_id !== null) {
    return source.metadata.server_id;
  }
  return null;
}

async function refreshTokenFromSource(sourceId, requestedServerId) {
  const source = tokenSourceStore.getTokenSource(sourceId);
  if (!source) {
    throw Object.assign(new Error(`未找到 token source: ${sourceId}`), {
      statusCode: 404,
    });
  }

  const originalBin = decodeBase64ToArrayBuffer(source.bin_base64);
  const binPayload = parseBinPayload(originalBin);
  const effectiveServerId = resolveSourceServerId(source, requestedServerId);
  if (source.requires_server_id && (effectiveServerId === null || effectiveServerId === "")) {
    throw Object.assign(new Error("当前 token source 需要 server_id"), {
      statusCode: 400,
    });
  }

  const effectiveBin =
    effectiveServerId === null || effectiveServerId === ""
      ? originalBin
      : buildRoleBin(binPayload, effectiveServerId);
  const authUserData = await requestAuthUser(effectiveBin);
  const wsReadyToken = buildWsReadyToken(authUserData);
  return {
    source,
    wsReadyToken,
    authUserData,
  };
}

async function describeAccountByToken(token, timeoutMs) {
  return await withClientByToken(token, timeoutMs, async (client, normalized) => {
    const roleInfo = await client.fetchRoleInfo(timeoutMs);
    return {
      token: {
        tokenId: normalized.tokenId,
        maskedToken: normalized.maskedToken,
        sourceFormat: normalized.sourceFormat,
        tokenShape: normalized.tokenShape,
      },
      summary: summarizeRoleInfo(roleInfo),
      roleInfo,
    };
  });
}

async function withClientByToken(token, timeoutMs, handler) {
  const normalized = normalizeIncomingToken(token);
  const client = new XyzwWsClient({
    url: buildWebSocketUrl(normalized.actualToken),
    timeoutMs,
  });
  try {
    return await handler(client, normalized);
  } finally {
    await client.close();
  }
}

async function runCommandByToken(token, command, params, timeoutMs, responseCommand) {
  return await withClientByToken(
    token,
    timeoutMs,
    async (client, normalized) => {
      const result = await client.runCommand(command, params, {
        timeoutMs,
        responseCommand,
      });
      return {
        token: {
          tokenId: normalized.tokenId,
          maskedToken: normalized.maskedToken,
          sourceFormat: normalized.sourceFormat,
          tokenShape: normalized.tokenShape,
        },
        command: result.cmd,
        seq: result.seq,
        ack: result.ack,
        code: result.code,
        error: result.error,
        body: result.body,
      };
    },
  );
}

const server = createServer(async (request, response) => {
  const corsHeaders = buildCorsHeaders();

  try {
    const url = new URL(request.url || "/", "http://127.0.0.1");

    if (request.method === "OPTIONS") {
      sendNoContent(response, 204, corsHeaders);
      return;
    }

    if (request.method === "GET" && url.pathname === "/health") {
      sendJson(
        response,
        200,
        success({
          service: SERVICE_NAME,
          version: SERVICE_VERSION,
          uptimeSeconds: Math.floor((Date.now() - startedAt) / 1000),
          consumers: ["astrbot", "web"],
          cors: {
            allowOrigin: corsHeaders["Access-Control-Allow-Origin"],
          },
          routes: [
            "GET /health",
            "GET /v1/token/source/:source_id",
            "POST /v1/token/source/register-bin",
            "POST /v1/token/wechat-qrcode/start",
            "POST /v1/token/wechat-qrcode/status",
            "POST /v1/token/wechat-qrcode/consume",
            "POST /v1/token/server-list",
            "POST /v1/token/authuser",
            "POST /v1/token/verify",
            "POST /v1/account/describe",
            "POST /v1/command/run",
            "POST /v1/car/overview",
            "POST /v1/car/helpers",
            "POST /v1/car/send",
            "POST /v1/car/claim-ready",
            "POST /v1/task/run-daily",
            "POST /v1/dungeon/run",
            "POST /v1/resource/run",
          ],
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "GET" && url.pathname.startsWith("/v1/token/source/")) {
      const sourceId = url.pathname.split("/").filter(Boolean).pop();
      if (!sourceId) {
        const result = failure("缺少 source_id", "MISSING_SOURCE_ID", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const refreshed = await refreshTokenFromSource(
        sourceId,
        url.searchParams.get("server_id"),
      );
      sendJson(
        response,
        200,
        {
          token: refreshed.wsReadyToken,
          source_id: refreshed.source.source_id,
          source_type: refreshed.source.source_type,
          requires_server_id: Boolean(refreshed.source.requires_server_id),
          updated_at: new Date().toISOString(),
        },
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/source/register-bin") {
      const body = await readJsonBody(request);
      if (!body.bin_base64) {
        const result = failure("缺少 bin_base64", "MISSING_BIN_BASE64", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const source = tokenSourceStore.registerBinSource(
        body.bin_base64,
        body.source_type || "bin",
        body.metadata,
      );
      sendJson(
        response,
        200,
        success({
          source: buildTokenSourcePayload(resolvePublicBaseUrl(request), source),
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/wechat-qrcode/start") {
      const qrcode = await startWechatQrcode();
      tokenSourceStore.upsertWechatQrcodeSession({
        uuid: qrcode.uuid,
        qrcode_url: qrcode.qrcode_url,
        expires_at: qrcode.expires_at,
        state: "pending",
      });
      sendJson(
        response,
        200,
        success({
          ...qrcode,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/wechat-qrcode/status") {
      const body = await readJsonBody(request);
      if (!body.uuid) {
        const result = failure("缺少 uuid", "MISSING_UUID", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const session = tokenSourceStore.getWechatQrcodeSession(body.uuid);
      if (!session) {
        const result = failure("未找到扫码会话", "WECHAT_QRCODE_NOT_FOUND", 404);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      if (session.state === "success" || session.state === "expired") {
        sendJson(response, 200, success(session), corsHeaders);
        return;
      }

      const status = await pollWechatQrcodeStatus(body.uuid);
      const updated = tokenSourceStore.updateWechatQrcodeSession(body.uuid, {
        state: status.state,
        code: status.code || session.code,
        nickname: status.nickname || session.nickname,
      });
      sendJson(response, 200, success(updated || session), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/wechat-qrcode/consume") {
      const body = await readJsonBody(request);
      const uuid = String(body.uuid || "").trim();
      const providedCode = String(body.code || "").trim();
      if (!uuid && !providedCode) {
        const result = failure("缺少 uuid 或 code", "MISSING_UUID_OR_CODE", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const session = uuid ? tokenSourceStore.getWechatQrcodeSession(uuid) : null;
      let effectiveCode = providedCode || String(session?.code || "").trim();
      let nickname = String(session?.nickname || "").trim();
      if (!effectiveCode && uuid) {
        const status = await pollWechatQrcodeStatus(uuid);
        effectiveCode = status.code;
        nickname = status.nickname || nickname;
        tokenSourceStore.updateWechatQrcodeSession(uuid, {
          state: status.state,
          code: effectiveCode,
          nickname,
        });
      }

      if (!effectiveCode) {
        const result = failure("扫码尚未确认，暂时无法完成登录", "WECHAT_QRCODE_PENDING", 409);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const loginResult = await loginWechatCode(effectiveCode);
      const binArrayBuffer = buildWechatBin(loginResult.combUser);
      const binBase64 = Buffer.from(binArrayBuffer).toString("base64");
      const rawRoles = await requestServerList(binArrayBuffer);
      const source = tokenSourceStore.registerBinSource(
        binBase64,
        "wechat_qrcode",
        {
          nickname,
          source_uuid: uuid || "",
        },
      );
      const sourcePayload = buildTokenSourcePayload(
        resolvePublicBaseUrl(request),
        source,
      );
      if (uuid) {
        tokenSourceStore.updateWechatQrcodeSession(uuid, {
          state: "success",
          nickname,
          code: effectiveCode,
          source_id: source.source_id,
          refresh_url: sourcePayload.refreshUrl,
          refresh_url_template: sourcePayload.refreshUrlTemplate,
        });
      }

      sendJson(
        response,
        200,
        success({
          uuid,
          nickname,
          bin_base64: binBase64,
          roles: normalizeServerRoles(rawRoles),
          rawRoles,
          source: sourcePayload,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/server-list") {
      const body = await readJsonBody(request);
      if (!body.bin_base64) {
        const result = failure("缺少 bin_base64", "MISSING_BIN_BASE64", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const binArrayBuffer = decodeBase64ToArrayBuffer(body.bin_base64);
      const binPayload = parseBinPayload(binArrayBuffer);
      const rawRoles = await requestServerList(binArrayBuffer);
      sendJson(
        response,
        200,
        success({
          roles: normalizeServerRoles(rawRoles),
          rawRoles,
          binPayload,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/authuser") {
      const body = await readJsonBody(request);
      if (!body.bin_base64) {
        const result = failure("缺少 bin_base64", "MISSING_BIN_BASE64", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const originalBin = decodeBase64ToArrayBuffer(body.bin_base64);
      const binPayload = parseBinPayload(originalBin);
      const effectiveBin = body.server_id !== undefined && body.server_id !== null
        ? buildRoleBin(binPayload, body.server_id)
        : originalBin;
      const authUserData = await requestAuthUser(effectiveBin);
      const wsReadyToken = buildWsReadyToken(authUserData);
      const token = normalizeIncomingToken(wsReadyToken);

      sendJson(
        response,
        200,
        success({
          token: {
            tokenId: token.tokenId,
            maskedToken: token.maskedToken,
            sourceFormat: token.sourceFormat,
            tokenShape: token.tokenShape,
            wsReadyToken,
          },
          authUserData,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/token/verify") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const account = await describeAccountByToken(body.token, body.timeout_ms);
      sendJson(
        response,
        200,
        success({
          verified: true,
          token: account.token,
          summary: account.summary,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/account/describe") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const account = await describeAccountByToken(body.token, body.timeout_ms);
      sendJson(response, 200, success(account), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/command/run") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }
      if (!body.command) {
        const result = failure("缺少 command", "MISSING_COMMAND", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const result = await runCommandByToken(
        body.token,
        String(body.command).trim(),
        body.params && typeof body.params === "object" ? body.params : {},
        body.timeout_ms,
        body.response_command ? String(body.response_command).trim() : undefined,
      );
      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/car/overview") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const commandResult = await runCommandByToken(
        body.token,
        "car_getrolecar",
        {},
        body.timeout_ms,
      );
      const overview = summarizeCars(commandResult.body);
      sendJson(
        response,
        200,
        success({
          ...commandResult,
          overview,
        }),
        corsHeaders,
      );
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/car/helpers") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const roleInfo = await client.fetchRoleInfo(body.timeout_ms);
          const [legionResult, helpingCountResult] = await Promise.all([
            client.runCommand("legion_getinfo", {}, {
              timeoutMs: body.timeout_ms,
            }),
            client.runCommand(
              "car_getmemberhelpingcnt",
              {},
              {
                timeoutMs: body.timeout_ms,
              },
            ),
          ]);
          assertCommandSuccessful(legionResult, "获取俱乐部成员信息");
          assertCommandSuccessful(helpingCountResult, "获取护卫成员状态");

          const helpers = buildCarHelperSnapshot({
            roleInfo,
            legionInfo: legionResult.body,
            helpingCountInfo: helpingCountResult.body,
            memberIds:
              Array.isArray(body.member_ids) || body.member_ids !== undefined
                ? body.member_ids
                : body.member_id,
            keyword: body.keyword,
            includeSelf: Boolean(body.include_self),
          });

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            roleInfo,
            legionInfo: legionResult.body,
            helperUsage: helpingCountResult.body,
            ...helpers,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/car/send") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }
      if (!body.car_id && body.car_id !== 0) {
        const result = failure("缺少 car_id", "MISSING_CAR_ID", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const normalizedCarId = String(body.car_id).trim();
          const helperId = Number(body.helper_id || 0);
          const text = String(body.text || "");
          const isUpgrade = Boolean(body.is_upgrade);

          const initial = await client.runCommand("car_getrolecar", {}, {
            timeoutMs: body.timeout_ms,
          });
          const before = summarizeCars(initial.body);
          const targetCar = findCarById(initial.body, normalizedCarId);
          if (!targetCar) {
            throw Object.assign(
              new Error(`未找到车辆: ${normalizedCarId}`),
              { statusCode: 404 },
            );
          }
          if (Number(targetCar.sendAt || 0) !== 0) {
            throw Object.assign(
              new Error(`车辆已发车: ${normalizedCarId}`),
              { statusCode: 409 },
            );
          }

          const sendResult = await client.runCommand(
            "car_send",
            {
              carId: normalizedCarId,
              helperId,
              text,
              isUpgrade,
            },
            { timeoutMs: body.timeout_ms },
          );

          const finalState = await client.runCommand("car_getrolecar", {}, {
            timeoutMs: body.timeout_ms,
          });
          const after = summarizeCars(finalState.body);
          const sentCar = findCarById(finalState.body, normalizedCarId);

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            carId: normalizedCarId,
            helperId,
            text,
            isUpgrade,
            before,
            after,
            sentCar,
            sendResult,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/car/claim-ready") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const initial = await client.runCommand("car_getrolecar", {}, {
            timeoutMs: body.timeout_ms,
          });
          const before = summarizeCars(initial.body);
          const readyCars = before.cars.filter((car) => car.claimable);
          const claimedCars = [];
          const failures = [];

          for (const car of readyCars) {
            try {
              await client.runCommand(
                "car_claim",
                { carId: String(car.id) },
                { timeoutMs: body.timeout_ms },
              );
              claimedCars.push({
                id: car.id,
                color: car.color,
                gradeLabel: car.gradeLabel,
              });
            } catch (error) {
              failures.push({
                id: car.id,
                message: error instanceof Error ? error.message : String(error),
              });
            }
          }

          const finalState = await client.runCommand("car_getrolecar", {}, {
            timeoutMs: body.timeout_ms,
          });
          const after = summarizeCars(finalState.body);

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            claimedCount: claimedCars.length,
            skippedCount: readyCars.length - claimedCars.length,
            failures,
            claimedCars,
            before,
            after,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/task/run-daily") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const roleInfo = await client.fetchRoleInfo(body.timeout_ms);
          const execution = await runSimpleDailyPlan(
            client,
            roleInfo,
            body.options && typeof body.options === "object" ? body.options : {},
            body.timeout_ms,
          );
          const finalRoleInfo = execution.finalRoleInfo || roleInfo;

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            summary: summarizeRoleInfo(finalRoleInfo),
            ...execution,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/resource/run") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }
      if (!body.action) {
        const result = failure("缺少 action", "MISSING_ACTION", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const resourceExecution = buildResourceExecution(
        body.action,
        body.options && typeof body.options === "object" ? body.options : {},
      );

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const execution = await executeResourceAction(
            client,
            resourceExecution,
            body.timeout_ms,
          );

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            ...execution,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    if (request.method === "POST" && url.pathname === "/v1/dungeon/run") {
      const body = await readJsonBody(request);
      if (!body.token) {
        const result = failure("缺少 token", "MISSING_TOKEN", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }
      if (!body.action) {
        const result = failure("缺少 action", "MISSING_ACTION", 400);
        sendJson(response, result.statusCode, result.body, corsHeaders);
        return;
      }

      const dungeonExecution = buildDungeonExecution(
        body.action,
        body.options && typeof body.options === "object" ? body.options : {},
      );

      const result = await withClientByToken(
        body.token,
        body.timeout_ms,
        async (client, normalized) => {
          const execution = await executeDungeonAction(
            client,
            dungeonExecution,
            body.timeout_ms,
          );

          return {
            token: {
              tokenId: normalized.tokenId,
              maskedToken: normalized.maskedToken,
              sourceFormat: normalized.sourceFormat,
              tokenShape: normalized.tokenShape,
            },
            ...execution,
          };
        },
      );

      sendJson(response, 200, success(result), corsHeaders);
      return;
    }

    const result = failure("路由不存在", "NOT_FOUND", 404, {
      method: request.method,
      path: url.pathname,
    });
    sendJson(response, result.statusCode, result.body, corsHeaders);
  } catch (error) {
    const statusCode = error?.statusCode && Number.isFinite(error.statusCode)
      ? error.statusCode
      : 500;

    sendJson(
      response,
      statusCode,
      {
        ok: false,
        code: statusCode >= 500 ? "INTERNAL_ERROR" : "REQUEST_ERROR",
        message: error instanceof Error ? error.message : String(error),
      },
      corsHeaders,
    );
  }
});

const port = parsePort(process.env.XYZW_SIDECAR_PORT || process.env.PORT);
const host = parseHost(process.env.XYZW_SIDECAR_HOST);
server.listen(port, host, () => {
  console.log(
    `[${SERVICE_NAME}] listening on http://${host}:${port} (version ${SERVICE_VERSION})`,
  );
});
