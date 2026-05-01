import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { parseBinPayload } from "./utils/bin.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DATA_DIR = path.resolve(__dirname, "../data");

function nowIso() {
  return new Date().toISOString();
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function stableSourceId(sourceType, binBase64) {
  return crypto
    .createHash("sha256")
    .update(`${sourceType}:${binBase64}`)
    .digest("hex")
    .slice(0, 24);
}

function decodeBase64ToArrayBuffer(binBase64) {
  const buffer = Buffer.from(binBase64, "base64");
  return buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength);
}

function normalizeBinBase64(value) {
  if (typeof value !== "string") {
    throw new Error("bin_base64 必须是字符串");
  }
  const normalized = value.replace(/^data:.*?;base64,/, "").replace(/\s+/g, "");
  if (!normalized) {
    throw new Error("bin_base64 为空");
  }
  return normalized;
}

function buildStoredSession(session) {
  return {
    uuid: String(session.uuid || "").trim(),
    qrcode_url: String(session.qrcode_url || "").trim(),
    created_at: String(session.created_at || nowIso()),
    expires_at: String(session.expires_at || ""),
    updated_at: String(session.updated_at || nowIso()),
    state: String(session.state || "pending"),
    nickname: String(session.nickname || ""),
    code: String(session.code || ""),
    source_id: String(session.source_id || ""),
    refresh_url: String(session.refresh_url || ""),
    refresh_url_template: String(session.refresh_url_template || ""),
  };
}

export class TokenSourceStore {
  constructor(options = {}) {
    this.dataDir = path.resolve(
      options.dataDir || process.env.XYZW_SIDECAR_DATA_DIR || DEFAULT_DATA_DIR,
    );
    this.stateFile = path.join(this.dataDir, "state.json");
    this.state = {
      token_sources: {},
      wechat_qrcode_sessions: {},
    };
    this._load();
  }

  _load() {
    ensureDir(this.dataDir);
    if (!fs.existsSync(this.stateFile)) {
      return;
    }
    try {
      const raw = fs.readFileSync(this.stateFile, "utf8");
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object") {
        this.state = {
          token_sources:
            parsed.token_sources && typeof parsed.token_sources === "object"
              ? parsed.token_sources
              : {},
          wechat_qrcode_sessions:
            parsed.wechat_qrcode_sessions &&
            typeof parsed.wechat_qrcode_sessions === "object"
              ? parsed.wechat_qrcode_sessions
              : {},
        };
      }
    } catch {
      this.state = {
        token_sources: {},
        wechat_qrcode_sessions: {},
      };
    }
  }

  _save() {
    ensureDir(this.dataDir);
    fs.writeFileSync(
      this.stateFile,
      JSON.stringify(this.state, null, 2),
      "utf8",
    );
  }

  registerBinSource(binBase64, sourceType = "bin", metadata = {}) {
    const normalizedBinBase64 = normalizeBinBase64(binBase64);
    const payload = parseBinPayload(decodeBase64ToArrayBuffer(normalizedBinBase64));
    const sourceId = stableSourceId(sourceType, normalizedBinBase64);
    const now = nowIso();
    const existing = this.state.token_sources[sourceId];
    const record = {
      source_id: sourceId,
      source_type: String(sourceType || "bin").trim() || "bin",
      bin_base64: normalizedBinBase64,
      requires_server_id:
        payload?.serverId === null ||
        payload?.serverId === undefined ||
        payload?.serverId === "",
      metadata:
        metadata && typeof metadata === "object" && !Array.isArray(metadata)
          ? { ...metadata }
          : {},
      created_at: existing?.created_at || now,
      updated_at: now,
    };
    this.state.token_sources[sourceId] = record;
    this._save();
    return record;
  }

  getTokenSource(sourceId) {
    const normalized = String(sourceId || "").trim();
    if (!normalized) {
      return null;
    }
    return this.state.token_sources[normalized] || null;
  }

  upsertWechatQrcodeSession(session) {
    const record = buildStoredSession(session);
    if (!record.uuid) {
      throw new Error("缺少 uuid");
    }
    record.updated_at = nowIso();
    if (!record.created_at) {
      record.created_at = record.updated_at;
    }
    this.state.wechat_qrcode_sessions[record.uuid] = {
      ...(this.state.wechat_qrcode_sessions[record.uuid] || {}),
      ...record,
    };
    this._save();
    return this.state.wechat_qrcode_sessions[record.uuid];
  }

  getWechatQrcodeSession(uuid) {
    const normalized = String(uuid || "").trim();
    if (!normalized) {
      return null;
    }
    return this.state.wechat_qrcode_sessions[normalized] || null;
  }

  updateWechatQrcodeSession(uuid, updates = {}) {
    const existing = this.getWechatQrcodeSession(uuid);
    if (!existing) {
      return null;
    }
    const next = {
      ...existing,
      ...(updates && typeof updates === "object" ? updates : {}),
      updated_at: nowIso(),
    };
    this.state.wechat_qrcode_sessions[existing.uuid] = buildStoredSession(next);
    this._save();
    return this.state.wechat_qrcode_sessions[existing.uuid];
  }
}

export function buildTokenSourceUrls(publicBaseUrl, source) {
  const normalizedBase = String(publicBaseUrl || "").replace(/\/+$/, "");
  const refreshUrl = `${normalizedBase}/v1/token/source/${source.source_id}`;
  const refreshUrlTemplate = source.requires_server_id
    ? `${refreshUrl}?server_id={serverId}`
    : refreshUrl;
  return {
    refreshUrl,
    refreshUrlTemplate,
  };
}
