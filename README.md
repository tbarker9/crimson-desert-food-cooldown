# CD Food Cooldown — rebuilt for game build 2026-09-23

Rebuilt offsets for **[CD Food Cooldown](https://www.nexusmods.com/crimsondesert/mods/3090)** by Rogue69 (Nexus mod 3090), plus a small companion mod that limits Palmar Pills.

Every game update shifts the byte offsets in `iteminfo.pabgb`. Offsets that no longer line up are either skipped by DMM or, on older DMM versions, **crash the game**. The `_build20260923` files are re-derived against the 2026-09-23 build (`0.paver` `020003000200e3cc19cb`). The previous build's files are kept in `build20260904/` for reference only — do not use them on the current game.

Each change in the `20260923` files also carries `entry` + `rel_offset` (the item's internal name and the offset from the end of that name), so DMM can re-anchor it by record if a future patch moves things again.

> **Food cooldown is not my mod.** All credit for the original goes to Rogue69. This repository contains only regenerated offset tables for it.

## Files

| File | What it does |
|------|--------------|
| `CD_Food_Cooldown_{2,5,10,15,30}s_build20260923.json` | Sets the shared food cooldown (477 items) to that many seconds. Vanilla is 1s. |
| `CD_Palmar_Pill_Limit_3_2_build20260923.json` | Caps carried Palmar Pills at **3** and Refined Palmar Pills at **2** (vanilla 10 each). |

The Palmar Pill limit lowers `max_stack_count` on `RevivalItemPartialRecovery` (Palmar Pill) and `RevivalItem` (Refined Palmar Pill), and turns on `apply_max_stack_cap` — the same total-carry cap the game uses for regional Refinement Tokens — so the limit applies to the total held, not just one stack.

## Install

1. Copy **one** food cooldown file (and optionally the Palmar Pill file) into your DMM `mods/` folder.
2. Enable them in DMM and mount.
3. Check `mount_log.txt` — every change should show `orig matched, patched`, with **no** `[STALE]` lines and no "needs an author update" warnings. If any appear, unmount and do not play.

Only enable one food cooldown file at a time — all five patch the same 1431 byte positions. The Palmar Pill mod touches different bytes and can run alongside any of them.

## Credits

- Original food cooldown mod: **Rogue69** — <https://www.nexusmods.com/crimsondesert/mods/3090>
- Offsets regenerated for build 2026-09-23.
