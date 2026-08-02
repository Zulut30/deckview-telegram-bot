/*
 * HSGuru trusted-browser bridge.
 *
 * Usage:
 * 1. Open https://www.hsguru.com/streamer-decks in a browser that already passes Cloudflare.
 * 2. Paste this script into DevTools Console.
 * 3. Run it. The script asks for API_TOKEN once and stores it in localStorage.
 *    With API_TOKEN it asks for confirmation before publishing.
 *
 * This does not solve or bypass Cloudflare. It sends data from an already
 * authorized browser session to Deckview, where the normal parser/filter/publish
 * pipeline runs.
 */
(async () => {
  const INGEST_URL = "https://api.blizzcore.ru/v1/hsguru/ingest";
  const STAGE_URL = "https://api.blizzcore.ru/v1/hsguru/stage";
  const LIMIT = 0;
  const TOKEN_STORAGE_KEY = "manacostDeckviewApiToken";

  let apiKey = localStorage.getItem(TOKEN_STORAGE_KEY) || "";
  if (!apiKey || apiKey === "PASTE_DECKVIEW_API_TOKEN_HERE") {
    apiKey = prompt("Deckview API_TOKEN (optional; Cancel = staging only):") || "";
    if (apiKey) localStorage.setItem(TOKEN_STORAGE_KEY, apiKey.trim());
  }
  apiKey = (apiKey || "").trim();
  const stageOnly = !apiKey;
  const apiUrl = stageOnly ? STAGE_URL : INGEST_URL;
  const dryRun = stageOnly
    ? true
    : !confirm("Опубликовать найденные новые колоды в WordPress/Telegram? Отмена = только проверка без публикации.");

  const text = (node) => (node ? node.textContent.replace(/\s+/g, " ").trim() : "");
  const cleanDeckName = (value) => String(value || "")
    .replace(/\bAAE[A-Za-z0-9+/]{40,}={0,3}/g, "")
    .replace(/^#+\s*/, "")
    .replace(/\s+/g, " ")
    .trim();
  const parseWinLoss = (value) => {
    const match = String(value || "").match(/\b(\d+)\s*-\s*(\d+)\b/);
    return match ? [Number(match[1]), Number(match[2])] : [0, 0];
  };
  const cleanRank = (value) => {
    const match = String(value || "").replace(/,/g, "").match(/\d+/);
    return match ? match[0] : "";
  };
  const findDeckCode = (row) => {
    const clip = row.querySelector("[data-clipboard-text]");
    if (clip?.dataset?.clipboardText) return clip.dataset.clipboardText.trim();
    const candidates = [
      row.getAttribute("data-clipboard-text"),
      row.getAttribute("data-deckcode"),
      ...[...row.querySelectorAll("[href], [title], [aria-label], [value]")]
        .flatMap((node) => [
          node.getAttribute("href"),
          node.getAttribute("title"),
          node.getAttribute("aria-label"),
          node.getAttribute("value"),
          node.textContent,
        ]),
      row.textContent,
    ].filter(Boolean).join(" ");
    const match = candidates.match(/\bAAE[A-Za-z0-9+/]{40,}={0,3}/);
    return match ? match[0] : "";
  };

  const rows = [...document.querySelectorAll("table tbody tr")];
  const decks = rows.map((row) => {
    const cells = [...row.querySelectorAll("td")];
    const deckCell = cells[0] || row;
    const deckLink = deckCell.querySelector('a[href^="/deck/"], a[href*="/deck/"]')
      || row.querySelector('a[href^="/deck/"], a[href*="/deck/"]');
    const [wins, losses] = parseWinLoss(text(cells[6]) || text(row));
    return {
      deck_code: findDeckCode(row),
      deck_name: cleanDeckName(text(deckLink) || text(deckCell)) || "Deck",
      streamer: text(cells[1]),
      format: text(cells[2]),
      wins,
      losses,
      total_games: wins + losses,
      peak: cleanRank(text(cells[3])),
      latest: cleanRank(text(cells[4])),
      worst: cleanRank(text(cells[5])),
      source_url: location.href,
    };
  }).filter((deck, index, all) =>
    deck.deck_code && all.findIndex((item) => item.deck_code === deck.deck_code) === index
  );

  const body = decks.length
    ? { decks, dry_run: dryRun, limit: LIMIT }
    : { html: document.documentElement.outerHTML, dry_run: dryRun, limit: LIMIT };

  const headers = {"Content-Type": "application/json"};
  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
    headers["X-API-Key"] = apiKey;
  }

  const response = await fetch(apiUrl, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const payload = await response.json();
  console.log("HSGuru bridge result:", payload);
})();
