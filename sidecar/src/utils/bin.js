import { randomInt } from "node:crypto";

import { g_utils } from "../../vendor/xyzw/bonProtocol.js";

function badRequest(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function normalizeBase64(input) {
  if (typeof input !== "string") {
    throw badRequest("bin_base64 必须是字符串");
  }
  const normalized = input.replace(/^data:.*?;base64,/, "").replace(/\s+/g, "");
  if (!normalized) {
    throw badRequest("bin_base64 为空");
  }
  return normalized;
}

export function decodeBase64ToArrayBuffer(binBase64) {
  const normalized = normalizeBase64(binBase64);
  let buffer;
  try {
    buffer = Buffer.from(normalized, "base64");
  } catch {
    throw badRequest("bin_base64 不是合法的 base64");
  }
  if (!buffer.length) {
    throw badRequest("bin_base64 解码后为空");
  }
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

export function parseBinPayload(binArrayBuffer) {
  try {
    const message = g_utils.parse(binArrayBuffer, "auto");
    const body = message?.getData?.();
    if (body && typeof body === "object") {
      return body;
    }
    if (message?._raw && typeof message._raw === "object") {
      return { ...message._raw };
    }
  } catch (error) {
    const wrapped = badRequest("bin 数据解析失败");
    wrapped.detail = String(error);
    throw wrapped;
  }
  throw badRequest("bin 数据解析失败");
}

export function buildRoleBin(binPayload, serverId) {
  if (!binPayload || typeof binPayload !== "object") {
    throw badRequest("bin 原始数据无效");
  }
  if (serverId === undefined || serverId === null || serverId === "") {
    throw badRequest("缺少 server_id");
  }

  const numericServerId = Number(serverId);
  if (!Number.isFinite(numericServerId)) {
    throw badRequest("server_id 无效");
  }

  const patched = {
    ...binPayload,
    serverId: numericServerId,
  };
  return g_utils.encode(patched, "x");
}

export function normalizeServerRoles(rawRoles) {
  const source = rawRoles && typeof rawRoles === "object" ? rawRoles : {};
  return Object.values(source)
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      roleId: item.roleId ?? item.id ?? null,
      name: item.name ?? item.roleName ?? null,
      serverId: item.serverId ?? null,
      power: item.power ?? null,
      level: item.level ?? null,
      raw: item,
    }))
    .sort((left, right) => Number(right.power ?? 0) - Number(left.power ?? 0));
}

export function buildWsReadyToken(authUserData) {
  const currentTime = Date.now();
  return JSON.stringify({
    ...authUserData,
    sessId: currentTime * 100 + randomInt(0, 99),
    connId: currentTime + randomInt(0, 9),
    isRestore: 0,
  });
}
