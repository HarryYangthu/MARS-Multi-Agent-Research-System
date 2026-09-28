'use strict';
const { timingSafeEqual } = require('node:crypto');

const TOKEN_HEADER = 'x-mars-desktop-token';
function tokenMatches(value, expected) {
  if (typeof value !== 'string' || typeof expected !== 'string') return false;
  const received = Buffer.from(value);
  const wanted = Buffer.from(expected);
  return wanted.length >= 32 && received.length === wanted.length && timingSafeEqual(received, wanted);
}
function isAppURL(value, origin) {
  try {
    const url = new URL(value);
    const allowed = new URL(origin);
    return ['http:', 'ws:'].includes(url.protocol)
      && url.hostname === '127.0.0.1'
      && url.host === allowed.host && !url.username && !url.password;
  } catch { return false; }
}
function authorizedRequest(request, token, origin) {
  const allowed = new URL(origin);
  return request.headers.host === allowed.host
    && tokenMatches(request.headers[TOKEN_HEADER], token)
    && (!request.headers.origin || request.headers.origin === origin)
    && (!request.headers['sec-fetch-site'] || ['none', 'same-origin'].includes(request.headers['sec-fetch-site']));
}
function backendPath(path) {
  return path === '/health' || path === '/api' || path.startsWith('/api/')
    || path === '/ws' || path.startsWith('/ws/');
}
function gatewayTarget(value, origin) {
  // Validate the origin-form path before WHATWG URL normalization can hide
  // traversal or disagreement between the proxy, Next rewrites and ASGI.
  if (typeof value !== 'string' || !value.startsWith('/') || value.startsWith('//')
      || /[\\#\u0000-\u0020\u007f]/.test(value) || /%(?![0-9a-f]{2})/i.test(value)) {
    throw new Error('Invalid local request target.');
  }
  const pathname = value.split('?', 1)[0];
  if (pathname.includes('//')) throw new Error('Ambiguous local request path.');
  const decoded = pathname.split('/').map((segment) => {
    const text = decodeURIComponent(segment);
    if (text === '.' || text === '..' || /[\\/?#\u0000-\u001f\u007f]/.test(text)
        || /%[0-9a-f]{2}/i.test(text)) throw new Error('Ambiguous local request path.');
    return text;
  }).join('/');
  if (backendPath(pathname) !== backendPath(decoded)
      || backendPath(decoded) !== backendPath(decoded.toLowerCase())) {
    throw new Error('Ambiguous local service prefix is forbidden.');
  }
  const parsed = new URL(value, origin);
  if (parsed.origin !== origin) throw new Error('Invalid local request origin.');
  return { pathname: parsed.pathname, search: parsed.search };
}
function consumeFrontendToken(request, expected) {
  if (!tokenMatches(request.headers[TOKEN_HEADER], expected)) return false;
  // Populate Node's distinct-header cache before reducing rawHeaders: its lazy
  // getter otherwise uses the original header count against the shorter array.
  const distinct = request.headersDistinct;
  if (distinct) delete distinct[TOKEN_HEADER];
  delete request.headers[TOKEN_HEADER];
  // Next's external rewrites must never receive the desktop session credential,
  // whether a downstream handler reads normalized headers or raw header pairs.
  if (Array.isArray(request.rawHeaders)) {
    request.rawHeaders = request.rawHeaders.filter((_value, index, headers) =>
      headers[index - index % 2].toLowerCase() !== TOKEN_HEADER);
  }
  return true;
}
function minimalEnvironment(source) {
  const names = ['PATH', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'HOME', 'USERPROFILE', 'TMP', 'TEMP', 'TMPDIR', 'LANG', 'LC_ALL'];
  return Object.fromEntries(names.filter((name) => source[name]).map((name) => [name, source[name]]));
}
module.exports = { TOKEN_HEADER, tokenMatches, isAppURL, authorizedRequest, backendPath, gatewayTarget, consumeFrontendToken, minimalEnvironment };
