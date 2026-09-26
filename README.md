# Crimson Desert Survival Tweaks (DMM) — for game version 2.03.02

A set of small JSON mods for Crimson Desert, loaded through DMM (Definitive Mod Manager), that make Normal difficulty a bit harsher without switching to Hard:

- **Food cooldown** — a longer cooldown on food and on medicine-type consumables (herbs, insects, holy water, elixirs).
- **Full eat animation** — on Normal, eating plays out like it does on Hard (with any food cooldown, or on its own).
- **Palmar Pill limits** — cap how many revive pills you can carry.
- **Hard enemies, Normal bosses** — regular enemies fight at Hard strength, bosses stay at Normal.
- **Hard damage taken** — you take Hard-mode damage (+30%) on Normal.

All files are built for **Crimson Desert 2.03.02** and use DMM's **field-name (V3) format** (`.field.json`): each change names the item and the field it sets (for example `max_stack_count` on `RevivalItem`), and DMM locates the data itself. That means they survive game updates that move or resize records. Requires **DMM 3.x**.

## Quick start: cdmods (no mod manager needed)

`cdmods.py` applies these mods straight to the game — no DMM required. It needs only Python 3 (already installed on practically every Linux distro) and finds your Crimson Desert install through Steam automatically.

```
./cdmods.py            # menu: pick a food cooldown, a Palmar Pill limit, Hard Enemies — then A to apply
./cdmods.py status     # game version, backup, what is applied
./cdmods.py restore    # back to the original game files
```

Keys in the menu: ↑/↓ (or j/k) move, Space select, **A** apply, **R** restore original, **Q** quit. The first run (and the first run after a game update) checks the game files for a few seconds; after that it starts instantly.

How it works:

- It never edits the game's archives. The modified tables go into an extra archive folder, `cdmods/`, in the game directory, registered in the game's archive index `meta/0.papgt` — the only original file that changes.
- Before the first change, `meta/0.papgt` is backed up to `~/.local/share/cd-mods/<game version>/`. A backup only counts for the exact game version it was taken from.
- Every apply starts from the originals (restore, then rebuild from the untouched game tables), so switching options never stacks changes.
- If the game files aren't original — a game update, another mod manager with mods mounted, manual edits — it refuses and tells you to run Steam "Verify integrity of game files" (or `restore`, if it holds a backup for your version).
- Close the game before applying or restoring. After a game update, just run it again.

If you use DMM instead, don't use both at once: unmount in DMM before using cdmods, and restore with cdmods before mounting in DMM.

## Files

### Food cooldown — pick one

| File | What it does |
|------|--------------|
| `CD_Food_Cooldown_{2,5,10,15,30}s.field.json` | Cooldown of 2–30 seconds for 610 consumables (vanilla is 1s): the 477 items on the game's food timer, plus the 133 on its medicine timer — herbs, insects, holy water, elixirs and horse tonics. |
| `CD_Food_Cooldown_{2,5,10,15,30}s_Full_Eat_Animation.field.json` | Same cooldown, **plus** the Hard-mode use action is enabled for all 548 consumables (food, herbs, insects, holy water), so the eating animation plays out. |
| `CD_Food_Cooldown_Vanilla_Full_Eat_Animation.field.json` | **Only** the full eat animation (Hard-mode use action for all 548 consumables); the food cooldown stays at vanilla 1s. |

Pick only one of these eleven: they set the same fields.

Food and medicine are two separate timers in the game: eating food doesn't lock out herbs or holy water, and vice versa. Every item on a timer must share the same cooldown — the game refuses to load ("static data invalid, InfoManagerType: Item") if only part of a timer group is changed — so these files always change whole groups.

### Palmar Pill limits — pick one

| File | Palmar Pill | Refined Palmar Pill |
|------|------------|---------------------|
| `CD_Palmar_Pill_Limit_1_1.field.json` | 1 | 1 |
| `CD_Palmar_Pill_Limit_3_2.field.json` | 3 | 2 |
| `CD_Palmar_Pill_Limit_5_3.field.json` | 5 | 3 |
| `CD_Palmar_Pill_Limit_7_4.field.json` | 7 | 4 |
| `CD_Palmar_Pill_Limit_10_5.field.json` | 10 | 5 |

Vanilla is 10 of each with no total cap. These lower `max_stack_count` and turn on `apply_max_stack_cap` — the same total-carry cap the game uses for regional Refinement Tokens — so the limit applies to the total you hold, not per stack. Picking up or buying past the cap is refused.

> **Watch out:** taking pills out of private storage while you are already at the cap **destroys** the extra pill. Only withdraw when you are below the limit.

### Difficulty

| File | What it does |
|------|--------------|
| `CD_Hard_Enemies_Normal_Bosses.field.json` | On Normal, 5,730 characters on the regular difficulty buff use the Hard level: more HP, knockdown resistance, faster attacks and movement. Bosses and mini-bosses stay at Normal, as does the player. |
| `CD_Hard_Damage_Taken.field.json` | On Normal, you take Hard-mode damage: +30% damage taken. Applies to the playable characters (Kliff, Damian, Oongka, Yann, Nairah, Witch); enemies and bosses are unchanged. |

## Install with DMM (alternative)

1. Copy the files you want into your DMM `mods/` folder — at most one food cooldown file and one Palmar Pill file; the enemy file can go alongside both.
2. Enable them in DMM and mount.
3. Check the mount log (`backups/mount_log.txt` in DMM's data folder, e.g. `~/.local/share/DMM/` on Linux) — each table should report `0 unresolved, 0 mismatch`, and the summary should show `Errors/warnings (0)`. If anything is unresolved or skipped, unmount and do not play.

## Credits

- Food cooldown is based on **CD Food Cooldown** by **Rogue69** — <https://www.nexusmods.com/crimsondesert/mods/3090>
