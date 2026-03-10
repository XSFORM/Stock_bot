# Changelog

All notable changes to **Stock_bot** are recorded here.

## How to add an entry

After each merged PR, add a line under the matching date section (create the section if it doesn't exist yet):

```
## YYYY-MM-DD

- <short description> ([#PR](https://github.com/XSFORM/Stock_bot/pull/PR))
```

### Useful git commands

```bash
# List the last 20 merge commits with date and title
git log --merges --oneline --format="%as  %s" -20

# List all merged PRs since a specific date
git log --merges --oneline --after="2026-02-01" --format="%as  %s"

# Show which files changed in a specific merge commit
git show --stat <commit-sha>
```

---

## 2026-03-10

- Fix 500 error on `/stock`: `get_stock()` now includes `sale_price` (wh_price × 1.25) in every row so `stock.html` renders without `UndefinedError`
- Fix 500 error on `/history`: `list_history()` and `list_history_by_product()` rebuilt to pull from invoice tables (cart_items/receive_items/return_items) and return all fields expected by templates (`dt`, `type`, `ref`, `counterparty`, `warehouse`, `unit_price`, `total`, `view_url`, `download_url`), resolving `UndefinedError` for `unit_price` and `total`
- Add Sonifer branding: replace PWA app icons (192×192, 512×512) and Apple touch icon (180×180) with Sonifer "S" logo on blue gradient; add `price-bg.jpg` background image to Pocket Price page with dark overlay for readability; update `theme_color`/`background_color` in manifest to Sonifer blue palette (#0d47a1 / #0a0f28)
- Update Pocket Price background: reduce dark overlay opacity from 0.55 to 0.35 so the background image is more visible; add `?v=2` cache-busting query string to `/static/price-bg.jpg` reference in `price.html` to bypass cached versions

## 2026-03-09

- Fix Pocket Price token parsing in `price.html`: JavaScript now reads `token` from URL query param on first load, stores it in `localStorage` (`pocket_price_token`), and falls back to `localStorage` on subsequent visits; `history.replaceState` strips the token from the URL after storing; fixes "Not paired" always showing even when a valid token is provided (#PR)
- Fix `/admin/price-tokens` 500 error caused by mismatched Jinja2 block in `admin_price_tokens.html`: replaced stray `{% endif %}` inside `{% for %}` loop (line ~56) with correct `{{ tok.created_at }}` cell content, resolving `TemplateSyntaxError` (#PR)
- Fix Pocket Price token creation: `POST /admin/price-tokens/create` no longer throws 500; token is now created and the plain token is shown on `/admin/price-tokens` via `new_token` query param (#PR)
- Fix Pocket Price feature: reconstruct complete `app/db/sqlite.py` with all required DB functions; fix broken `search_products_for_price` (was connecting to placeholder `your_database.db`) and `set_price_token_mode` (was a no-op stub) ([#15](https://github.com/XSFORM/Stock_bot/pull/15))

## 2026-03-02

- Document nginx Basic Auth setup for securing the web UI (README + docs/nginx-basic-auth.conf)

## 2026-02-24

- Clients: balance tracking, ⋯ action menu, and per-client history page ([#14](https://github.com/XSFORM/Stock_bot/pull/14))
- Add archive/unarchive for Products and Clients (soft-delete) ([#13](https://github.com/XSFORM/Stock_bot/pull/13))
- Move section: product typeahead, available stock display, and qty guard ([#12](https://github.com/XSFORM/Stock_bot/pull/12))

## 2026-02-23

- Enhance /history search: non-strict multi-field, multi-token matching ([#11](https://github.com/XSFORM/Stock_bot/pull/11))
- Add /history page: unified RECEIVE + SALE inventory movement log ([#10](https://github.com/XSFORM/Stock_bot/pull/10))
- Fix carts/invoices created_at to use server localtime instead of UTC ([#9](https://github.com/XSFORM/Stock_bot/pull/9))
- Add /invoices page with tabbed DONE invoice listings ([#8](https://github.com/XSFORM/Stock_bot/pull/8))
- Replace supplier datalist with select dropdown on /receive page ([#7](https://github.com/XSFORM/Stock_bot/pull/7))
- Rework Receive into ERP-style Purchase Invoice workflow ([#6](https://github.com/XSFORM/Stock_bot/pull/6))
- Add typeahead product search to Receive page ([#5](https://github.com/XSFORM/Stock_bot/pull/5))

## 2026-02-22

- Sale UI: edit client on open invoice, items qty display in table and XLSX ([#4](https://github.com/XSFORM/Stock_bot/pull/4))
- Sale page: cancel invoice, inline line-item editing, XLSX export, restructured columns ([#3](https://github.com/XSFORM/Stock_bot/pull/3))

## 2026-02-20

- Redesign Sale module UI to ERP-style with stock search API and structured cart data ([#2](https://github.com/XSFORM/Stock_bot/pull/2))
- Sale module: single active invoice enforcement, conditional UI, hide cart_id ([#1](https://github.com/XSFORM/Stock_bot/pull/1))
