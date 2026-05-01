import { Buffer } from "node:buffer";

export async function readJsonBody(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  if (chunks.length === 0) {
    return {};
  }

  const rawBody = Buffer.concat(chunks).toString("utf8").trim();
  if (!rawBody) {
    return {};
  }

  try {
    return JSON.parse(rawBody);
  } catch (error) {
    const parsingError = new Error("请求体不是有效的 JSON");
    parsingError.cause = error;
    parsingError.statusCode = 400;
    throw parsingError;
  }
}

export function sendJson(response, statusCode, payload, extraHeaders = {}) {
  const body = JSON.stringify(payload, null, 2);
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    ...extraHeaders,
  });
  response.end(body);
}

export function sendNoContent(response, statusCode = 204, extraHeaders = {}) {
  response.writeHead(statusCode, {
    "Content-Length": "0",
    ...extraHeaders,
  });
  response.end();
}
