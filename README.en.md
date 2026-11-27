# @dsh-gis/dsh-video-studio-skill

![version](https://img.shields.io/badge/version-2.10.0-blue) ![license](https://img.shields.io/badge/license-MIT-green)

**English | [中文](README.md)**

> This is the English entry page. The **Chinese [README.md](README.md) is authoritative** — it is
> more detailed and is updated first. When the two disagree, the Chinese version wins.

**A DeepSeek Harness skill plugin: two production lines for Chinese-language video. If you have
footage, cut it (footage mode). If you do not, draw every frame in code.**

Script -> edge-tts narration -> timeline built from **measured** durations -> a **cue system** drives
music and SFX -> per-frame rendering with PIL or a browser -> ffmpeg encode -> sample-accurate mixing ->
**four layers of QA** (container / signal / time / page) plus **five newer gates**
(`G0` pre-render resource check / `G10` word-level timestamps / `G11` full-film VMAF /
`G12` ASR readback / `G13` layout text collision).
Zero stock footage, reproducible, resumable, scriptable to verify; once installed as a plugin, the
whole production pipeline comes along with the skill into every session.

## What problem this solves

| Pain | How it is solved |
|---|---|
| Audio and video drift apart | The timeline is derived from **measured** TTS durations, never estimated from character counts |
| Text looks soft or mushy | PIL frames go through `rawvideo` straight into libx264 — no cv2 intermediate file re-compressed twice |
| The second half goes silent | numpy sample-level mixing instead of one ffmpeg command fed with hundreds of inputs |
| A missing fade or a subtitle that silently vanishes | Gates assert both "must be present" and "must not be present" |
| The result looks like a draft | -16 LUFS / true peak <= -1 dBTP / encode sharpness ratio >= 0.85 |
| You discover the wrong visual mode only after finishing | Intake question 2 fixes the **production mode** first: self-drawn / licensed-footage cut / both; recommended per topic, `--recommend` queries it alone |
| Sound does not line up with picture | **Cue system**: the picture registers a cue -> SFX are synthesized from cues; changing rhythm does not mean re-aligning by hand |
| Thin sound, just ding-ding-dong-dong | `sfxkit` physically-modelled SFX + `musicgen` parameterised scoring (there is no style preset list) |
| A one-frame white flash or a two-second blank nobody noticed | `qa_frames` deep inspection: single-frame flicker, blank windows, A/V sync, static frames |
| "I checked it" cannot be reproduced | `out/QC-REPORT.md` + `work/gates.json`: a three-state gate matrix where "not run" is its own `skipped` |
| "I signed off" turns out to be an empty promise | Sign-off is bound to a content fingerprint: editing the script / `scenes.py` / engine invalidates old sign-offs automatically |
| One line changed, no idea what to re-run | `work/manifest.json` records per-stage input/output fingerprints; `--stage explain` prints the minimal re-run command |
| A misspelled `holds` silently does nothing | `filmcheck.py` enforces the film.json contract (wired into `ci_check` check 12); `schemas/film.schema.json` documents fields for humans and editors |
| The machine picks the style and you never took part | **Proposal first**: `propose.py` shows N candidates (each with a still contact sheet + design distance); only `--confirm` freezes `design.json` |
| Every video looks the same | The design DSL samples live (fresh nonce each time) + candidates differ in >=2 dimensions + base-hue divergence + a **cross-film dedupe ledger** |
| Appearance limited to a few templates | No built-in style table: continuous hue space, and motif / transition / easing / layout / subtitle are all open fields; candidates must pass a `theme_lint` contrast check |

## Same brief, two runs, two different films (reproducible)

Propose twice from the same brief: palette, transitions, score, layout rotation and subtitle style all
differ (motif and voice default to plain + single narrator; you must name them to change).

```bash
# Round 1: see 3 candidates side by side (each = a full design + still contact sheet + design distance)
python <SKILL_DIR>/scripts/propose.py --brief "Three years of LLMs, 30s explainer, deep blue" --script film.json --work work
#   candidate A  dark h201 - corner brackets   3 frames  0.9s  -> work/proposals/rev1/A/_sheet.png
#   candidate B  dark h220 - hairline header    3 frames  0.6s  -> work/proposals/rev1/B/_sheet.png
#   candidate C  dark h213 - minimal whitespace 3 frames  0.6s  -> work/proposals/rev1/C/_sheet.png
#   contact sheet (side by side): work/proposals/rev1/_compare.png

# Run the same brief into a different work directory — you get another set of designs
# (without --seed every run uses a fresh nonce)
python <SKILL_DIR>/scripts/propose.py --brief "Three years of LLMs, 30s explainer, deep blue" --script film.json --work work2
```

> Measured on the author machine: three consecutive runs of the same brief produced 9 candidates
> whose `design_fingerprint` values were **all pairwise distinct**; each batch used 2-3 layouts in
> `layout_cycle`, with different subtitle size / alignment / plate alpha / radius / family.

What the picture looks like is decided by the **design**, not by which template: 10 composition
types (hero / statement / list / stat / compare / process / quote / chapter / chart / outro) x
9 decorative motifs (plain by default, no hairline decoration) x 6 transitions x continuous-hue
palettes x subtitle styling x per-chapter layout rotation — and every candidate must pass `theme_lint`.

## Install

Desktop: plugin manager -> enter `@dsh-gis/dsh-video-studio-skill` (or your local checkout path).

```bash
# CLI
dsh plugin --profile desktop add "@dsh-gis/dsh-video-studio-skill"
# local debugging (works without publishing):
dsh plugin --profile desktop add "D:/path/to/dsh-video-studio-skill"
```

After installing, enable the `video-studio` row on the Plugins page and open a new session.

### Relationship to a hand-installed skill directory

Skills resolve by rank: `~/.dsh/skills` (rank 400) **wins over** the plugin bundled skill root
(rank 600). If you previously hand-installed `~/.dsh/skills/video-studio`, it shadows the plugin
version. Move the old directory **out of** `~/.dsh/skills` after installing (renaming is not enough —
scanning looks for `<root>/<name>/SKILL.md`, so a `.bak` directory is still picked up):

```bash
mkdir -p ~/.dsh/backup
mv ~/.dsh/skills/video-studio ~/.dsh/backup/video-studio.bak-$(date +%Y%m%d)
```

## Usage (zero-copy: no scripts or templates to copy)

Trigger phrases: make a video / generate a promo / turn a report into a video / animate this /
narrate with subtitles / produce a final cut / fix A/V sync / the video looks blurry — or mention
ffmpeg, edge-tts, per-frame rendering, subtitles, storyboard, score, mixing.

```bash
mkdir -p myfilm && cd myfilm
# 1) Write one film.json — the only file you hand-write in your project (see SKILL.md section 1)

# 2) Environment self-check (optional, 108 assertions, offline)
python <SKILL_DIR>/scripts/selfcheck.py

# 3) Intake: eight fixed questions at once; production mode is recommended per topic
python <SKILL_DIR>/scripts/intake.py --work work --questions --json --topic "Three years of LLMs"
python <SKILL_DIR>/scripts/intake.py --work work --recommend "Ten years of Chinese high-speed rail, documentary"
python <SKILL_DIR>/scripts/intake.py --work work --finalize     # -> work/intake.brief.txt + next commands

# 4) Propose: 3 candidates, each with a still contact sheet; ~2 seconds, fully offline
python <SKILL_DIR>/scripts/propose.py --script film.json --work work

# 5) Confirm a candidate and freeze the design -> work/design.json (rendering only reads this)
python <SKILL_DIR>/scripts/propose.py --confirm --candidate A --work work

# 6) Look at stills first, then render the full film
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage stills --stills "l01:3,l02:5"
python <SKILL_DIR>/scripts/render.py --script film.json --work work --out out/film.mp4 --stage all

# 7) Delivery evidence + human sign-off
cat out/QC-REPORT.md
python <SKILL_DIR>/scripts/render.py --script film.json --work work --stage accept --gate release
```

`<SKILL_DIR>` is the resourceBase directory reported by the `skill` tool
(`.../skills/video-studio/`); `<REPO>` is the package root, i.e. two levels up — `tools/` and
`schemas/` live under `<REPO>`.

**Per-stage** (`--stage` does only its own job and can be re-run alone):
`style | tts | layout | beats | cues | score | sfx | audio | frames | mux | qa | stills | review |
editlist | jianying | accept | explain | all`.

The `--stage all` order is `style -> tts -> layout -> beats -> cues -> score -> sfx -> audio ->`
`frames -> mux -> qa -> editlist`. `review` and `jianying` are deliberately **not** in `all`
(independent final review is a delivery-time action; JianYing export needs the `--no-subs` plate).

**Common switches**:

| Want | How |
|---|---|
| Deliver 720p / change frame rate | `"video": {"width":1280,"height":720,"fps":24}` in `film.json` (portrait: 1080x1920 + `--layout portrait`) |
| Batch production | `python <SKILL_DIR>/scripts/render.py --batch films.json` (up to 100 entries, full preflight first, one failure does not block) |
| Render only the first N seconds | `--preview 15` (writes `out/preview/*.mp4` + `.gif`, no delivery ledger) |
| Make QA faster | `--vmaf sample` (one in five frames) or `--vmaf off` (G11 recorded as skipped) |
| Treat "not run" as failure before delivery | `--strict-gates` |
| No idea what to re-run after an edit | `--stage explain` |
| Re-propose in another direction | `propose.py --revise "colder, less decoration, female voice"` |
| Reproduce one proposal | add `--seed 42` on the first proposal |
| Clean picture for an external editor | `--no-subs` (no subtitles burned in; `sub.srt` / `sub.ass` still written) |

Long films: `--stage frames --jobs 4` (parallel chunks by absolute frame number) + `--resume`
(only missing chunks are re-rendered).

## Repository layout (abridged)

```
index.js                      Plugin entry: mounts skills/ as a bundled skill root
cordis.patch.yml              Composition patch (insert: video-studio)
icon.svg / locale/*.json      Plugin page metadata (en / zh)
skills/video-studio/          SKILL.md + references/ (25 docs) + scripts/ + assets/
schemas/*.json                design / proposal / film / footage contracts
tools/                        Repo-level checks: ci_check, estimate, golden_frames, refscan, asset_license
docs/compatibility.md         Compatibility evidence report (generated)
```

> The full, always-current layout block lives in the Chinese [README.md](README.md) — `ci_check`
> asserts that it lists **every** script, tool, schema and reference file, so that is the copy to trust.

## Requirements

**Plugin runtime**: Node.js >= 20 (`engines` in `package.json`; this is what DSH uses to load the
plugin, nothing extra to install).
**Required**: ffmpeg + Python with numpy / Pillow / edge-tts.
**Optional**: cv2 (encode sharpness ratio), scipy (faster SFX filtering; FFT convolution otherwise),
playwright + Chromium/Edge (only for `--engine web`), faster-whisper (ASR readback gate G12; falls
back to the ffmpeg whisper filter, then records `skipped`).
G11 (full-film VMAF) needs no new dependency but requires an ffmpeg build **with libvmaf**.
No moviepy / pydub / soundfile / librosa / manim — this skill does not import them at all.

```bash
pip install -r skills/video-studio/requirements.txt   # required + strongly recommended; optional lines are commented
```

On Windows, `FILMKIT_FFMPEG` overrides the ffmpeg path; font candidates live in
`filmkit.FONT_CANDIDATES`. Chinese font files are required for rendering.

## Compatibility

The criterion is `engines.dsh` in `package.json`: `>=0.1.0-rc.6 <0.3.0-0` (prereleases included).
The DSH 0.2.x loader reads `engines.dsh` and ignores `peerDependencies`.

Verified on DSH Desktop **0.2.2-rc.5**: `node tools/plugin_entry_check.mjs` asserts that `apply()`
registers one provider and `list()` returns a candidate with `rank=600 / source=bundled`.
The upper bound is pinned at 0.3.0 because a changed `SkillProvider` contract would make the plugin
degrade silently — run `plugin_entry_check.mjs` before bumping it.

If the skill does not show up after installing, run one diagnostic instead of reading logs:

```bash
npm run diagnose              # JSON to stdout; exit code 1 when healthy=false
npm run diagnose -- --human   # human-readable
```

## Self-checks (contributors and CI, not end users)

```bash
python tools/ci_check.py -v     # 18 repo-level checks; needs no ffmpeg, fonts or network
```

It does **not** replace `skills/video-studio/scripts/selfcheck.py`, which needs ffmpeg + Chinese
fonts, actually exercises the encoders, and checks whether the skill can still produce a film
(**108** assertions).

## Changelog & license

See [CHANGELOG.md](CHANGELOG.md). MIT — the algorithm ideas behind four modules (sfxkit / scorekit /
webengine / qa_frames) are adapted from [ai-history-film](https://github.com/Re-Summer75/ai-history-film)
(MIT) without copying files or pulling in its dependencies; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

