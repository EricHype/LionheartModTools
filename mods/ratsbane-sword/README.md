# Ratsbane

Adds a unique short sword, **Ratsbane**, that drops from Lucia when she's killed in
her Wererat form during the Test Pocket quest.

## Mechanics

- **Bonus damage vs. wererats**: an extra 8 disease damage on a successful hit,
  following the exact pattern of the vanilla `Goblin Slayer` weapon addition (a
  `CPlugInBehaviorStrikeAction` that checks the target on a successful hit and adds
  damage). Wererats are identified by model (`Characters/Monsters/Wererat`, `...
  variant`, `... boss`, `... PRIME`) since, unlike goblins, they don't have their own
  distinct `Category=` value on their character templates -- only the generic
  `Enemy`.
- **Genuinely easier to hit wererats, not an all-or-nothing override**: the vanilla
  game already has a mechanism for "guaranteed hit/miss vs. a monster race"
  (`Damage Types/Damage Hit Or Miss/Special conditions/HitGoblinsOnly`, used by exactly
  one quest item, `Bounty Hunter Sword Everlasting`) but it's all-or-nothing --
  always hits that race, always misses everything else. Ratsbane instead replicates
  the real normal one-handed-melee hit formula
  (`Damage Types/Damage Hit Or Miss/OneHandedMelee`) verbatim for the attacker's roll
  and for the defender's side against anything that isn't a wererat, and only halves
  the *defender's effective Armor Class* specifically when the target is a wererat --
  a real, moderate bonus, with normal combat completely unaffected against everything
  else. New resource: `WereratBane.DamageHitOrMiss`.
- **Base damage unchanged**: the addition's `CPlugInBehaviorDamage` replaces the base
  `ShortSword`'s damage behavior wholesale (`Replaces matching plugins=1`, required to
  swap in the custom hit-or-miss formula), so it carries an exact copy of the base
  sword's own damage dice (1d5 + skill-tier bonus + Weapon Specialization bonus) --
  only the hit-or-miss formula changes, not the weapon's baseline damage.
- **World pickup, not instant inventory add**: uses the game's real loot-spawn
  mechanism (`CGenerateInventoryItemAction`, the same one vanilla enemies use for
  gold/spirit drops) added as a fourth action alongside Lucia's existing XP/gold/
  spirit drops in her `Destroyed Effect Action`. Ratsbane physically appears near her
  body after the fight, same as any other loot drop.
- **Custom inventory icon**: a sickly-green recolor of the vanilla `ShortSwordSpecial`
  icon (`mdl16_format.recolor_icon_in_place`), distinct from the plain Short Sword.
  The source icon has a small disconnected band of pixels at the very top of buffer 1
  that turned out to interact with buffer 4/5 (the format's secondary highlight plane)
  in an unpredictable way -- three different recolor treatments for that band each
  produced a different visible defect in-game (rainbow noise, a black bar, then a wider
  bar). The working fix is to leave that band byte-identical to the source and recolor
  everything else. Full writeup in `docs/mdl16-icon-format.md`.

## Requires

`test-pocket` must also be installed and enabled, and must load **before** this mod
(this mod ships its own copy of `Lucia Wererat.can` with one more drop action added,
so it needs to load after and win the "last mod wins" conflict).

## Testing

1. `python modmanager.py enable <game-dir> ratsbane-sword` (after test-pocket is
   already enabled)
2. `python modmanager.py build <game-dir>`
3. In-game: reach the Test Pocket, trigger Lucia's transformation (give her the
   necklace), and kill Lucia Wererat. A short sword should drop near her body.
4. Pick it up and check: bonus damage and noticeably easier hits specifically against
   wererats (try it on a different enemy type too, to confirm normal combat is
   unaffected there).
