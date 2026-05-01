import { g_utils } from "../vendor/xyzw/bonProtocol.js";

const WECHAT_QRCODE_URL =
  "https://open.weixin.qq.com/connect/app/qrconnect" +
  "?appid=wxfb0d5667e5cb1c44" +
  "&bundleid=com.hortor.games.xyzw" +
  "&scope=snsapi_base,snsapi_userinfo,snsapi_friend,snsapi_message" +
  "&state=weixin";

const WECHAT_LOGIN_URL =
  "https://comb-platform.hortorgames.com/comb-login-server/api/v1/login";

const WECHAT_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Linux; Android 7.0; Mi-4c Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/53.0.2785.49 Mobile MQQBrowser/6.2 TBS/043632 Safari/537.36 MicroMessenger/6.6.1.1220(0x26060135) NetType/WIFI Language/zh_CN",
  Accept:
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
  Referer: "https://open.weixin.qq.com/",
};

const WECHAT_STATUS_HEADERS = {
  "User-Agent": WECHAT_HEADERS["User-Agent"],
  Accept: "*/*",
  Referer: "https://open.weixin.qq.com/",
};

const HORTOR_HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Linux; Android 12; 23117RK66C Build/V417IR; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/95.0.4638.74 Mobile Safari/537.36",
  Accept: "*/*",
  Host: "comb-platform.hortorgames.com",
  Connection: "keep-alive",
  "Content-Type": "text/plain; charset=utf-8",
  Origin: "https://open.weixin.qq.com",
  Referer: "https://open.weixin.qq.com/",
};

function badGateway(message) {
  const error = new Error(message);
  error.statusCode = 502;
  return error;
}

function encodeBase64(text) {
  if (!text) {
    return null;
  }
  return Buffer.from(String(text), "utf8").toString("base64");
}

function rightSide(str) {
  return str.substring(Math.floor(str.length / 2));
}

function leftSide(str) {
  return str.substring(0, Math.floor(str.length / 2));
}

function transCode(str, times) {
  if (times <= 0) {
    return str;
  }
  if (str.length % 2 !== 0) {
    return null;
  }
  const right = rightSide(str);
  const left = leftSide(str);
  return transCode(right, times - 1) + transCode(left, times - 1);
}

function getCodeKey(str, step) {
  const chars = str.split("");
  const result = [];
  const count = Math.floor(str.length / step);
  for (let index = 0; index < count; index += 1) {
    result.push(chars[index * step]);
  }
  return result.join("");
}

function dealWithString(src, key, shift) {
  if (!src || !key) {
    return null;
  }

  const sourceChars = src.split("");
  const keyChars = key.split("");
  const output = new Array(sourceChars.length);

  let index = keyChars.length >> shift;
  for (let cursor = 0; cursor < sourceChars.length; cursor += 1) {
    if (index >= keyChars.length) {
      index = 0;
    }
    output[cursor] = String.fromCharCode(
      sourceChars[cursor].charCodeAt(0) ^ keyChars[index].charCodeAt(0),
    );
    index += 1;
  }
  return output.join("");
}

function codeBase64(text, cipherTable, shuffleTimes, step, xorShift) {
  const base64Text = encodeBase64(text);
  if (!cipherTable) {
    return null;
  }
  const shuffled = transCode(cipherTable, shuffleTimes);
  const key = getCodeKey(shuffled, step);
  return dealWithString(base64Text, key, xorShift);
}

const CIPHER_TABLE =
  "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";

export function encodePayload(text) {
  const xorShift = 1;
  const shuffleTimes = 6;
  const step = 3;
  const mid = codeBase64(text, CIPHER_TABLE, shuffleTimes, step, xorShift);
  return Buffer.from(String(mid || ""), "utf8").toString("base64");
}

export async function startWechatQrcode() {
  const response = await fetch(WECHAT_QRCODE_URL, {
    method: "GET",
    headers: WECHAT_HEADERS,
    redirect: "follow",
  });
  if (!response.ok) {
    throw badGateway(
      `微信二维码获取失败: ${response.status} ${response.statusText}`,
    );
  }

  const html = await response.text();
  const qrMatch =
    html.match(/<img[^>]*class="auth_qrcode"[^>]*src="([^"]+)"/i) ||
    html.match(/https:\/\/[^"'\\s]*qrcode[^"'\\s]*/i);
  const qrUrl = qrMatch?.[1] || qrMatch?.[0];
  if (!qrUrl) {
    throw badGateway("未从微信登录页解析到二维码地址");
  }

  const normalizedQrUrl = new URL(qrUrl, "https://open.weixin.qq.com").toString();
  const pathname = new URL(normalizedQrUrl).pathname;
  const uuid = pathname.split("/").filter(Boolean).pop();
  if (!uuid) {
    throw badGateway("未从二维码地址解析到 uuid");
  }

  return {
    uuid,
    qrcode_url: normalizedQrUrl,
    expires_at: new Date(Date.now() + 120000).toISOString(),
  };
}

export async function pollWechatQrcodeStatus(uuid) {
  const statusUrl = new URL("https://open.weixin.qq.com/connect/l/qrconnect");
  statusUrl.searchParams.set("uuid", String(uuid || "").trim());
  statusUrl.searchParams.set("f", "url");
  statusUrl.searchParams.set("_", String(Date.now()));

  const response = await fetch(statusUrl, {
    method: "GET",
    headers: WECHAT_STATUS_HEADERS,
    redirect: "follow",
  });
  if (!response.ok) {
    throw badGateway(
      `微信扫码状态查询失败: ${response.status} ${response.statusText}`,
    );
  }

  const text = await response.text();
  if (text.includes("window.wx_errcode=405")) {
    const codeMatch = text.match(/wx_redirecturl='[^']*code=([a-zA-Z0-9_-]+)/);
    const nicknameMatch = text.match(/window\.wx_nickname\s*=\s*['"]([^'"]+)['"]/);
    return {
      state: "confirmed",
      code: codeMatch?.[1] || "",
      nickname: nicknameMatch?.[1] || "",
      raw_text: text,
    };
  }
  if (text.includes("window.wx_errcode=404")) {
    return {
      state: "scanned",
      code: "",
      nickname: "",
      raw_text: text,
    };
  }
  if (text.includes("window.wx_errcode=408")) {
    return {
      state: "expired",
      code: "",
      nickname: "",
      raw_text: text,
    };
  }
  return {
    state: "pending",
    code: "",
    nickname: "",
    raw_text: text,
  };
}

export async function loginWechatCode(code) {
  const payload = {
    gameId: "xyzwapp",
    code,
    gameTp: "app",
    sysInfo:
      '{"system":"Android","hortorSDKVersion":"4.0.6-cn","model":"22081212C","brand":"Redmi"}',
    channel: "android",
    appFrom: "com.tencent.mm",
    noLogin: "2",
    distinctId: "DID-a38175b7-14ce-4b36-aa89-3e092ea03ea6",
    state: "hortor",
    packageName: "com.hortor.games.xyzw",
    tp: "app-we",
    signPrint: "E6:F7:FE:A9:EC:8E:24:D0:4F:2A:32:50:28:78:E1:C5:5E:70:81:13",
  };

  const loginUrl = new URL(WECHAT_LOGIN_URL);
  loginUrl.searchParams.set("gameId", "xyzwapp");
  loginUrl.searchParams.set("timestamp", String(Date.now()));
  loginUrl.searchParams.set("version", "android-4.2.1-cn-release");
  loginUrl.searchParams.set("cryptVersion", "1.1.0");
  loginUrl.searchParams.set("gameTp", "app");
  loginUrl.searchParams.set("system", "android");
  loginUrl.searchParams.set(
    "deviceUniqueId",
    "DID-0e782e88-2f3b-4f5b-9020-47f5e5a5a026",
  );
  loginUrl.searchParams.set("packageName", "com.hortorgames.xyzw");

  const response = await fetch(loginUrl, {
    method: "POST",
    headers: HORTOR_HEADERS,
    body: encodePayload(JSON.stringify(payload)),
    redirect: "follow",
  });
  if (!response.ok) {
    throw badGateway(
      `Hortor 登录失败: ${response.status} ${response.statusText}`,
    );
  }

  const json = await response.json();
  if (json?.meta?.errCode !== 0) {
    throw badGateway(`Hortor 登录失败: ${json?.meta?.errMsg || "未知错误"}`);
  }
  const combUser = json?.data?.combUser;
  if (!combUser || typeof combUser !== "object") {
    throw badGateway("Hortor 登录响应缺少 combUser");
  }

  return {
    combUser,
    raw: json,
  };
}

export function buildWechatBin(combUser) {
  return g_utils.encode({
    platform: "hortor",
    platformExt: "mix",
    info: combUser,
    serverId: null,
    scene: 0,
    referrerInfo: "",
  });
}
