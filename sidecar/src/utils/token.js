import crypto from "node:crypto";

function badRequest(message) {
  const error = new Error(message);
  error.statusCode = 400;
  return error;
}

function maskToken(token) {
  if (token.length <= 16) {
    return `${token.slice(0, 4)}***${token.slice(-4)}`;
  }
  return `${token.slice(0, 12)}...${token.slice(-8)}`;
}

function tryDecodeBase64(raw) {
  const candidate = raw.replace(/^data:.*?;base64,/, "").replace(/\s+/g, "");
  if (!candidate || candidate.startsWith("{") || candidate.startsWith("[")) {
    return null;
  }
  if (!/^[A-Za-z0-9+/=]+$/.test(candidate) || candidate.length < 16) {
    return null;
  }

  try {
    const decoded = Buffer.from(candidate, "base64").toString("utf8");
    if (!decoded.trim()) {
      return null;
    }

    const normalizedInput = candidate.replace(/=+$/, "");
    const normalizedOutput = Buffer.from(decoded, "utf8")
      .toString("base64")
      .replace(/=+$/, "");

    if (normalizedInput !== normalizedOutput) {
      return null;
    }

    return decoded.trim();
  } catch {
    return null;
  }
}

function extractActualToken(candidate, parsedObject) {
  if (
    parsedObject &&
    typeof parsedObject === "object" &&
    !Array.isArray(parsedObject)
  ) {
    if (typeof parsedObject.token === "string" && parsedObject.token.trim()) {
      return {
        actualToken: parsedObject.token.trim(),
        tokenShape: "wrapped_token",
      };
    }
    if (
      typeof parsedObject.gameToken === "string" &&
      parsedObject.gameToken.trim()
    ) {
      return {
        actualToken: parsedObject.gameToken.trim(),
        tokenShape: "wrapped_game_token",
      };
    }
    return {
      actualToken: candidate,
      tokenShape: "json_payload",
    };
  }

  return {
    actualToken: candidate,
    tokenShape: "plain",
  };
}

export function normalizeIncomingToken(input) {
  if (input === undefined || input === null) {
    throw badRequest("缺少 token");
  }

  let raw;
  if (typeof input === "string") {
    raw = input.trim();
  } else if (typeof input === "object") {
    raw = JSON.stringify(input);
  } else {
    throw badRequest("token 类型无效");
  }

  if (!raw) {
    throw badRequest("token 为空");
  }

  let sourceFormat = "plain";
  let candidate = raw;

  const decoded = tryDecodeBase64(raw);
  if (decoded) {
    candidate = decoded;
    sourceFormat = "base64";
  } else if (raw.startsWith("{") || raw.startsWith("[")) {
    sourceFormat = "json";
  }

  let parsedObject = null;
  try {
    parsedObject = JSON.parse(candidate);
  } catch {
    parsedObject = null;
  }

  const { actualToken, tokenShape } = extractActualToken(candidate, parsedObject);
  if (!actualToken || actualToken.length < 20) {
    throw badRequest("token 长度过短，当前 sidecar 需要 WebSocket-ready token");
  }

  return {
    rawInput: raw,
    actualToken,
    sourceFormat,
    tokenShape,
    tokenId: crypto.createHash("md5").update(actualToken).digest("hex"),
    maskedToken: maskToken(actualToken),
  };
}
