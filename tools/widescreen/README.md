# Widescreen research

Analysis of KoriTama's 2014 *Lionheart Widescreen Patch* (`LionheartWidescreenPatch.exe`, wxWidgets 2.9.2) against the
GOG `Lionheart.exe`, made 2026-11-17. This is the starting point for Phase 1 of the widescreen plan in LionheartFixt's
`docs/after-1.0.md`, which also records the findings. None of KoriTama's code or art is reused; these scripts only read
his patcher to learn what it changes.

The paths to the patcher and the game are hard-coded at the top of `ws_dis.py`. Run the scripts from this folder.

| File | What it is |
|---|---|
| `ws_dis.py` | PE section parser plus a capstone disassembler, `python -I ws_dis.py <start> <end>` for a patcher VA range |
| `ws_table.py` | Decodes the patcher's 73-row value table at `0x8cc000` (game VA, value type in W/H, write mode) and shows the game's original instruction at each site |
| `ws_caves.py` | Emulates the patcher's hand-written writes (a 30-byte buffer, seek, write) and prints each one with the game's original and patched disassembly |
| `ws_caves.txt` | Output of `ws_caves.py`: 67 writes, mostly jumps chained through int3 padding |
| `ws_rem.py`, `ws_rem.txt` | Every 800 (0x320) and 600 (0x258) immediate in `Lionheart.exe`, to sort into patched, missed and not screen related |
| `ws_patched.txt` | The patch sites in write order |
| `ws_pd.txt` | Disassembly of the patcher's own patch routine |

W and H in the outputs are the chosen screen width and height. Game file offset = VA - 0x400000 in `.text`.
