---
name: opencli-site-adapter
description: 创建或修复 opencli 站点适配器。给 opencli 添加新网站的爬虫规则，或调试现适配器。
agent_created: true
---

# OpenCLI Site Adapter

## Overview

A workflow for creating opencli site adapters. These adapters scrape web pages using a controlled browser (Chrome Bridge extension) and return structured tabular data. Covers: adapter skeleton, DOM scraping with CSS Modules, CSR waiting, and debugging.

## Prerequisites

- opencli installed and daemon running (`opencli daemon status`)
- Chrome Browser Bridge extension installed and connected (`Extension: connected` in daemon status)
- Existing adapter for reference: `opencli adapter eject <existing-site>` (e.g. `smzdm`)

## Workflow

### 1. Research the target site

Determine:
- Is the page server-side rendered (SSR) or client-side rendered (CSR)?
  - SSR: page HTML contains product data directly → simple HTTP fetch may work
  - CSR: data loaded by JavaScript → must use browser (`Strategy.COOKIE`)
- What is the search URL pattern? (e.g. `?keyword=xxx&c=discount`)
- Does the site have an API? If yes, token generation may be needed (expensive to reverse)

### 2. Create the adapter skeleton

Create the directory and file:
```
mkdir -p ~/.opencli/clis/<site-name>
```

The skeleton follows this pattern (based on ejected `smzdm` adapter):

```javascript
import { cli, Strategy } from '@jackwener/opencli/registry';
cli({
    site: '<site-name>',
    name: 'search',
    access: 'read',
    description: '<site-name> 搜索好价',
    domain: '<domain>',
    strategy: Strategy.COOKIE,
    args: [
        { name: 'query', required: true, positional: true, help: 'Search keyword' },
        { name: 'limit', type: 'int', default: 20, help: 'Number of results' },
    ],
    columns: ['rank', 'title', 'price', 'seller', 'time', 'url'],
    func: async (page, kwargs) => {
        const q = encodeURIComponent(kwargs.query);
        const limit = kwargs.limit || 20;
        await page.goto(`https://<site-url>/search?keyword=${q}`);
        // --- scraping logic here ---
    },
});
```

### 3. Handle CSR pages (Next.js / React)

CSR pages don't have data in initial HTML. After `page.goto()`, wait for the React components to render:

```javascript
// Poll for a known DOM element, with 15s fallback timeout
await page.evaluate(() => new Promise(resolve => {
    const check = () => {
        const el = document.querySelector('div[class*="discount"]');
        if (el) return resolve();
        setTimeout(check, 500);
    };
    check();
    setTimeout(resolve, 15000);
}));
```

### 4. Debug the DOM structure

Run the adapter with a verbose flag to dump the actual HTML of a product item. Add a temporary debug block before the `page.evaluate()` call:

```javascript
const debug = await page.evaluate(() => {
    // Adjust selector as needed based on CSS Modules class names
    const item = document.querySelector('div[class*="box"] > div[class*="item"]');
    if (!item) return 'no items found';
    return item.innerHTML.substring(0, 5000);
});
// Output to stderr (appears in verbose mode)
console.error('DEBUG:', debug.substring(0, 3000));
```

**Key insight for CSS Modules sites** (Next.js, etc.): Class names are dynamic hashes (e.g. `DiscountItemPC_itemSubTitle__rWgWK`). Use `[class*="partial-name"]` attribute selectors to match them, **not** full class names.

### 5. Extract data with page.evaluate()

Write browser-side JavaScript that:
1. Queries the DOM for product items
2. Extracts title, price, seller, time, URL from each item
3. Deduplicates by URL

**Example pattern** (correct CSS Modules selectors for manmanbuy):

```javascript
const data = await page.evaluate(`
    (() => {
        const limit = ${limit};
        const items = document.querySelectorAll('div[class*="DiscountItemPC_box"] > div[class*="discountItem__"]');
        const results = [];
        items.forEach((item) => {
            if (results.length >= limit) return;
            // Title
            const titleEl = item.querySelector('div[class*="itemTitle__"] a');
            if (!titleEl) return;
            const title = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
            if (!title || title.length < 3) return;
            // URL
            const linkEl = item.querySelector('a[href*="cu.manmanbuy.com"]');
            const url = linkEl ? (linkEl.getAttribute('href') || '') : '';
            // Deduplicate
            if (url && results.some(r => r.url === url)) return;
            // Price
            const subTitleEl = item.querySelector('div[class*="itemSubTitle__"] a') || item.querySelector('div[class*="itemSubTitle__"]');
            const price = subTitleEl ? subTitleEl.textContent.trim() : '';
            // Platform/seller
            const mallEl = item.querySelector('span[class*="itemMall__"]');
            const seller = mallEl ? mallEl.textContent.trim() : '';
            // Timestamp
            const timeEl = item.querySelector('span[class*="itemTime__"]');
            const time = timeEl ? timeEl.textContent.trim() : '';
            results.push({ rank: results.length + 1, title, price, seller, time, url });
        });
        return results;
    })()
`);
```

### 6. Run and iterate

```bash
# Basic run
opencli <site-name> search <keyword>

# With format for readability
opencli <site-name> search <keyword> -f md

# With verbose debug output
opencli <site-name> search <keyword> -v
```

If results look wrong:
- Empty → selectors need adjustment, use debug dump (step 4)
- Missing fields → check field-specific selectors
- Duplicates → add URL deduplication

### 7. Common pitfalls

- **`page.waitForSelector is not a function`**: The opencli page object uses a limited API. Use `page.evaluate()` with polling instead.
- **Empty results on CSR sites**: The React components haven't rendered yet — add polling wait before scraping.
- **Class names change**: CSS Modules class names contain content hashes. They change when the site's CSS changes. Always use `[class*="keyword"]` and keep them broad enough to survive minor changes.
- **Site requires authentication**: Some sites show a login wall for price comparison pages. The discount/deals page may be publicly accessible while the full comparison page requires login.
- **Token-protected APIs**: Don't spend time reverse-engineering obfuscated token algorithms unless there's no alternative. Browser DOM scraping is simpler.
