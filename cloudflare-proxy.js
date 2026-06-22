/**
 * Binance API Proxy — Cloudflare Worker
 * يحل مشكلة HTTP 418 على Render
 * 
 * للنشر:
 * 1. روح لـ https://dash.cloudflare.com
 * 2. Workers & Pages → Create Worker
 * 3. الصق هذا الكود → Deploy
 * 4. انسخ الرابط (مثلاً binance-proxy.اسمك.workers.dev)
 */

export default {
  async fetch(request) {
    // نمرر الطلب لـ Binance API
    const url = new URL(request.url);
    const binanceUrl = "https://api.binance.com" + url.pathname + url.search;

    const response = await fetch(binanceUrl, {
      headers: {
        "Accept": "application/json",
      },
    });

    // نرجع نفس الاستجابة
    return new Response(response.body, {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=5",
      },
    });
  },
};
