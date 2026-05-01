import { g_utils } from "../vendor/xyzw/bonProtocol.js";

const AUTH_USER_URL = "https://xxz-xyzw.hortorgames.com/login/authuser?_seq=1";
const SERVER_LIST_URL = "https://xxz-xyzw.hortorgames.com/login/serverlist?_seq=3";

async function postBinary(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      referrerPolicy: "no-referrer",
    },
    body: Buffer.from(payload),
  });

  if (!response.ok) {
    const error = new Error(`上游请求失败: ${response.status} ${response.statusText}`);
    error.statusCode = 502;
    throw error;
  }

  return await response.arrayBuffer();
}

export async function requestAuthUser(binArrayBuffer) {
  const responseBuffer = await postBinary(AUTH_USER_URL, binArrayBuffer);
  const message = g_utils.parse(responseBuffer, "auto");
  return message.getData();
}

export async function requestServerList(binArrayBuffer) {
  const responseBuffer = await postBinary(SERVER_LIST_URL, binArrayBuffer);
  const message = g_utils.parse(responseBuffer, "auto");
  const data = message.getData();
  return data?.roles ?? {};
}
