# Crimson Desert Survival Tweaks (DMM) — for game version 2.03.02

A set of small JSON mods for Crimson Desert, loaded through DMM (Definitive Mod Manager), that make Normal difficulty a bit harsher without switching to Hard:

- **Food cooldown** — a longer shared cooldown between food items.
- **Full eat animation** — on Normal, eating plays out like it does on Hard (optional, bundled with the cooldown).
- **Palmar Pill limits** — cap how many revive pills you can carry.
- **Hard enemies, Normal bosses** — regular enemies fight at Hard strength, bosses stay at Normal.

All files are built for **Crimson Desert 2.03.02**. Every change also carries `entry` + `rel_offset` (the record's internal name and the offset from the end of that name), so DMM can re-anchor it by record if a future patch moves things.

## Files

### Food cooldown — pick one

| File | What it does |
|------|--------------|
| `CD_Food_Cooldown_{2,5,10,15,30}s.json` | Shared food cooldown of 2–30 seconds for 477 food items (vanilla is 1s). |
| `CD_Food_Cooldown_{2,5,10,15,30}s_Full_Eat_Animation.json` | Same cooldown, **plus** Normal difficulty uses the Hard-mode use action for all 548 consumables (food, herbs, insects, holy water), so the eating animation plays out. |

All ten patch the same cooldown bytes, so enable only one.

### Palmar Pill limits — pick one

| File | Palmar Pill | Refined Palmar Pill |
|------|------------|---------------------|
| `CD_Palmar_Pill_Limit_1_1.json` | 1 | 1 |
| `CD_Palmar_Pill_Limit_3_2.json` | 3 | 2 |
| `CD_Palmar_Pill_Limit_5_3.json` | 5 | 3 |
| `CD_Palmar_Pill_Limit_7_4.json` | 7 | 4 |
| `CD_Palmar_Pill_Limit_10_5.json` | 10 | 5 |

Vanilla is 10 of each with no total cap. These lower `max_stack_count` and turn on `apply_max_stack_cap` — the same total-carry cap the game uses for regional Refinement Tokens — so the limit applies to the total you hold, not per stack. Picking up or buying past the cap is refused.

> **Watch out:** taking pills out of private storage while you are already at the cap **destroys** the extra pill. Only withdraw when you are below the limit.

### Hard enemies, Normal bosses

| File | What it does |
|------|--------------|
| `CD_Hard_Enemies_Normal_Bosses.json` | On Normal, 5,730 characters on the regular difficulty buff use the Hard level: more HP, knockdown resistance, faster attacks and movement. Bosses and mini-bosses stay at Normal, as does the player. |

## Install

1. Copy the files you want into your DMM `mods/` folder — at most one food cooldown file and one Palmar Pill file; the enemy file can go alongside both.
2. Enable them in DMM and mount.
3. Check `mount_log.txt` — every change should show `orig matched, patched`, with **no** `[STALE]` lines and no "needs an author update" warnings. If any appear, unmount and do not play.

## Older builds

`build20260904/` holds the food cooldown files for the 2026-09-04 game build, kept for reference only. Do not use them on the current game.

## Credits

- Food cooldown is based on **CD Food Cooldown** by **Rogue69** — <https://www.nexusmods.com/crimsondesert/mods/3090>
