# CycleTrack

A Mac app for PIP-FUCCI time-lapse experiments from the Incucyte (it was called PIP-FUCCI until
version 0.1.7). It segments nuclei, tracks them through divisions, calls G1 / S / G2-M from the
red and green reporters, measures confluence and colonies, and produces population, single-cell
and publication-ready results. Everything runs on the Mac: no account, no sign-in, no internet
needed (except to download updates), and images never leave the computer.

## Install

1. Download the DMG for your Mac (Apple menu > About This Mac shows *Apple M…* or *Intel*):
   - Apple silicon: [CycleTrack-AppleSilicon.dmg](https://github.com/Abed97/cell-analysis/releases/latest/download/CycleTrack-AppleSilicon.dmg)
   - Intel: [CycleTrack-Intel.dmg](https://github.com/Abed97/cell-analysis/releases/latest/download/CycleTrack-Intel.dmg)
2. Open it and drag **CycleTrack** onto **Applications**.
3. First time only: double-click CycleTrack and click **Done** on the warning. Then open
   **System Settings › Privacy & Security**, scroll down, click **Open Anyway**, and confirm.
   The DMG includes a one-page guide, *How to open CycleTrack the first time.pdf*.

Copies installed under the old name keep updating themselves and show the new name in the window,
menu bar and notifications; the icon in Applications keeps the old name unless it is renamed in
Finder (safe to do). Internally the app bundle, update files and
`~/Library/Application Support/PIP-FUCCI` keep the old name so those copies keep updating.

The app isn't registered with Apple (that needs a paid developer account), which is why macOS asks
once. Needs macOS 12 or later.

### Updates

The current version and update status are always shown at the top right of the window. New
versions download in the background (the app checks when it opens and every 15 minutes). When
one is ready, a blue **Update now** button appears there: the window stays open, shows
"Updating…" for a few seconds, and comes back on the same experiment and screen, running the new
version. The button is disabled while an analysis is running.

The settings (gear icon, top right) has **Check for updates automatically** and **Install
updates automatically** (both on by default). With automatic installs on, the app switches to a
downloaded version the next time it opens, or after a minute without use when no analysis is
running. **Check now** looks for a new version immediately. The same window sets how many
processor cores the analysis uses (Auto = all but one, about 0.3 GB each with 3 GB kept free for
macOS and other apps, so an 8 GB Mac uses 7 of its 8 cores; Settings shows the number).

No password, no "Open Anyway" again: the copy in Applications is never touched. New versions
live in `~/Library/Application Support/PIP-FUCCI/versions/`; the installed app hands over to the
newest one at launch, and **Update now** starts the new version in the background and moves the
window to it. A version that fails to start twice is skipped automatically. Results are never
affected; they live next to the images.

## Using it

1. **Experiment** – open the Incucyte export folder (or try the demo data). Check the channels,
   enter frame interval and pixel size, assign wells to conditions on the plate map, then
   **Run analysis**. Frames are processed in parallel on several cores.
2. **Segment & gate** – step through each field frame by frame (buttons, frame number, or the
   arrow keys; Shift + arrow jumps five frames, Space plays). Layers: background removed,
   nucleus outlines coloured by phase, **cell numbers** (the track number of every cell), and
   the confluence area. Click a cell to select it and follow it through the movie, then open it
   in Single cells or in its family tree. Set the two gates on the red-vs-green scatter.
3. **Population** – phase fractions over time (G1, S, G2/M and Colourless), cell number and
   confluence (mean ± SEM across wells), and red-vs-green plots at selected timepoints.
4. **Single cells** – track heatmap, per-cell image montage and traces, phase durations, a
   **family tree** of each cell's lineage (parent to daughters, coloured by phase), and a dot plot
   of durations by condition. Tracks can be excluded or marked as reviewed.
5. **Figures** – publication-style figures on a white background: cell-cycle composition over
   time per condition, time spent in each phase (box plots with every cell), and phase fractions
   over time. Choose order, colour and dashed lines per condition; **Save PNG + SVG** writes them to
   `PIP-FUCCI results/figures/`. **Add analysed experiment…** brings other analysed folders into
   the Figures and Population pages; conditions with the same name are pooled, wells stay the
   replicates.

**Graphs are interactive** (Population, Single cells, Figures): hover for values, drag across a
time range to zoom (pinch zooms too, a sideways swipe pans), double-click or *Reset zoom* to go
back. On the Population page all charts zoom together and clicking a condition in the legend hides
or shows it. On box and dot plots, hover a box for median, quartiles and n; click a dot to open
that cell in Single cells.

**Leaving out blurry images:** in Segment & gate, *Use in results* → *Not this image* leaves out
just the image on screen (that field at that time point; the key **X** does the same while stepping
through frames), *Not this field* leaves out the field at every time point, *Not well B2* the whole
well. On the plate map, *Exclude wells (blurry)* does whole wells. Nothing is re-analysed: left-out
images become gaps (or, if a well has other fields at that time, the count is scaled to the fields
that remain) in every chart, figure and Prism table, and are marked `excluded` in the per-cell and
per-track tables.

**Axes** on every figure can be set by hand (from, to, tick step; empty = automatic), saved with
the experiment and used by *Export all*. **Figures** can be zoomed with − / + or a pinch (scroll to move around), and the time axis of
time-course figures narrowed by dragging across a range. The red-vs-green figure takes the time
points you type (up to 8; the Population page uses the same ones). *Cell-cycle composition of all
cells* is the stacked version for every cell in the images at each time point; *Cell-cycle
composition over time* follows only the founder cells and their daughters.

**Export all graphs & figures** (Figures page or the Export card) writes every figure as PNG (3×)
and SVG into one dated folder in `PIP-FUCCI results/figures/`: composition, phase durations, phase
fractions (followed cells and all cells), growth and confluence, colonies (lines and box plots) and
red-vs-green plots.

**Confluence and colonies** come from a phase-contrast mask of every cell, coloured or not. Each
image is compared with its own empty background (local texture of the log image, so brightness and
exposure changes between images don't matter); an image with no background left uses the field's
first image. In Segment & gate, *Confluence mask* has **Sensitivity**, **Join gaps up to** (µm) and
**Grow / shrink edge** (µm); the overlay follows them at once, and **Apply to all images** redraws
every mask (nuclei and tracks are kept, no re-analysis).
Objects smaller than *Smallest object counted* (step 2, default 50 µm²) are debris: drawn with an
orange outline and left out. Touching cells form one colony; the Population and Figures pages
plot colony number per image and average colony area over time, as lines (mean ± SEM across
wells) or as box plots at regular time points (colonies per image, and the area of every colony,
optionally on a log scale). Changing the debris size only
re-measures from the saved masks (seconds), it doesn't re-run the analysis.

**While it runs** the top bar and the Dock icon show the progress, the Mac doesn't nap or sleep, and
a notification plus a Dock bounce say when it's done (turn off in Settings). Frames are processed
on several cores (per-nucleus measurements use the nucleus pixels only, about a third faster per
image than before v0.1.14); if one worker process dies (for example memory runs out), its frames are retried
with fewer workers instead of stopping.

Everything is saved automatically (settings, plate map, gates, track flags, results) in
`PIP-FUCCI results` next to the images; the top bar shows when it was last saved.

**Export all tables** writes Prism tables (one column per well, grouped by condition), a
per-cell-per-frame table, a per-track table with durations and censoring, the numbers behind the
figures, and a JSON record of every setting and software version, into
`PIP-FUCCI results/exports/` next to the images.

### Expected export format

Raw single-channel TIFFs (16-bit, unscaled), one per channel per timepoint per well and field,
named the Incucyte way: `Prefix_B2_1_02d04h30m.tif` or `Prefix_B2_1_2026y11m19d_14h30m.tif`.
The channel can be in a folder name (`Phase/`, `Green/`, `Red/`) or in the prefix; the app
groups the files and lets you confirm which group is which channel.

## How the analysis works

- **Settings are inputs, not constants.** Frame interval, pixel size, nucleus diameter, max speed
  and max gap are entered per experiment; search radius, gap frames, smoothing window and the
  daughter search radius are derived from them, so 30-min and 60-min data use the same code.
- **Background**: green autofluorescence and uneven medium are removed with a top-hat filter
  (grey opening with a disk wider than any nucleus), so broad haze goes and cells stay.
- **Confluence** is the area covered by all cells in phase contrast, whatever their colour.
- **Segmentation** on the combined red + green image after background subtraction. The threshold
  comes from the background noise rather than Otsu so dim cells at G1/S and S/G2 are kept;
  touching nuclei are split by watershed seeded from each channel separately.
- **Tracking** by optimal frame-to-frame assignment (distance plus a colour-change penalty),
  gap closing, and division detection that uses PIP-FUCCI logic: the parent is red (G2/M) the
  frame before, the daughters' red drops and they look alike. Both daughters are tracked; doubtful
  parent assignments are flagged for review rather than forced. Cells with neither reporter on
  (just after division, early G1) are followed on phase contrast for up to a set time and shown
  as "Colourless", so tracks are not broken there.
- **Phase calls**: raw calls from the two gates, then smoothing per track that enforces
  G1 → S → G2/M, so cells don't flicker between phases.
- **Censoring**: phases that start before the movie, end after it, or touch the field edge are
  left out of durations. Every duration carries ± one frame interval. Statistics belong at the
  well / biological-replicate level; single-cell distributions are descriptive.
- **Reproducibility**: settings, gates, intensity scales and software versions are saved with
  every run and every export.

On synthetic test movies with known ground truth (`tests/`), nuclei are found with about 98%
recall and precision, frame-to-frame links are about 97% correct, and smoothed phase calls about
94% correct. Real data will differ: validate on 50–100 hand-annotated cells before trusting
the numbers.

## Publishing a new version

Push to `main`. GitHub Actions (`.github/workflows/build-mac.yml`) runs the tests, builds the app
for Apple silicon and Intel on GitHub's Macs, signs it ad hoc, self-tests the built app and the
update handover, and publishes a release numbered `<major>.<minor>.<build>` (for example
`0.1.14`). Open apps show the **Update now** banner within about 30 minutes. Pushes that
only change `.md` files don't make a release. The 10 newest releases are kept.

To start a new series (for example 0.2.x), change `__version__` in `pipfucci/__init__.py`.

## Running from source

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .            # add [cellpose] for the optional Cellpose segmentation
python -m pipfucci          # opens the app window (use --browser for a browser tab)
python -m pytest tests      # tests on synthetic data with ground truth
```
