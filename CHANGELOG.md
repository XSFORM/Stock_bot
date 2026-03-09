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

## 2026-03-09

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
