// worker-proxy.js — Worker 反代全球并优选（来自 2x.nz/cf-fastip 文章）
//
// 原理：Worker 路由把「域名前缀 -> 源站」的流量反代到源站，入口节点的接入 IP 由你的
// DNS 解析层控制（CNAME 到优选节点），从而实现"规则层在 CF、解析层自己管"的优选。
// 与直连优选（改客户端 server 为优选 IP）互补：本方案客户端什么都不用改，改的是 DNS。
//
// 用法：
//   1. 创建 Worker，粘贴本代码
//   2. 修改 domain_mappings：'源站域名': '最终访问头前缀'
//      （示例：'gitea.072103.xyz': 'gitea.'  => 设置路由 gitea.* 都会反代到 gitea.072103.xyz）
//   3. 添加路由：你的域名 + /*（如 gitea.afo.im/*）
//   4. 添加 DNS 解析：CNAME gitea.afo.im -> 你的优选域名（不开 CF 代理）
//
// 注意：
//   - 源站接收到的 Host 头仍是"最终访问头"对应的源站域名，不是接入域名
//   - 只适用于 HTTP(S) 服务；隧道(cloudflared)的 ws/grpc 代理流量请用直连优选方案
//   - 免费额度：请求量大的站建议自建域名 + 遵守 CF 免费计划限制

// 域名前缀映射配置
const domain_mappings = {
  '源站.com': '最终访问头.',
  // 例如：
  // 'gitea.072103.xyz': 'gitea.',
  // 则你设置 Worker 路由为 gitea.* 都将会反代到 gitea.072103.xyz
};

addEventListener('fetch', event => {
  event.respondWith(handleRequest(event.request));
});

async function handleRequest(request) {
  const url = new URL(request.url);
  const current_host = url.host;

  // 强制使用 HTTPS
  if (url.protocol === 'http:') {
    url.protocol = 'https:';
    return Response.redirect(url.href, 301);
  }

  const host_prefix = getProxyPrefix(current_host);
  if (!host_prefix) {
    return new Response('Proxy prefix not matched', { status: 404 });
  }

  // 查找对应目标域名
  let target_host = null;
  for (const [origin_domain, prefix] of Object.entries(domain_mappings)) {
    if (host_prefix === prefix) {
      target_host = origin_domain;
      break;
    }
  }

  if (!target_host) {
    return new Response('No matching target host for prefix', { status: 404 });
  }

  // 构造目标 URL
  const new_url = new URL(request.url);
  new_url.protocol = 'https:';
  new_url.host = target_host;

  // 创建新请求
  const new_headers = new Headers(request.headers);
  new_headers.set('Host', target_host);
  new_headers.set('Referer', new_url.href);

  try {
    const response = await fetch(new_url.href, {
      method: request.method,
      headers: new_headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
      redirect: 'manual'
    });

    // 复制响应头并添加 CORS
    const response_headers = new Headers(response.headers);
    response_headers.set('access-control-allow-origin', '*');
    response_headers.set('access-control-allow-credentials', 'true');
    response_headers.set('cache-control', 'public, max-age=600');
    response_headers.delete('content-security-policy');
    response_headers.delete('content-security-policy-report-only');

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response_headers
    });
  } catch (err) {
    return new Response(`Proxy Error: ${err.message}`, { status: 502 });
  }
}

function getProxyPrefix(hostname) {
  for (const prefix of Object.values(domain_mappings)) {
    if (hostname.startsWith(prefix)) {
      return prefix;
    }
  }
  return null;
}
