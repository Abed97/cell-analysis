# PIP-FUCCI

A Mac app for PIP-FUCCI time-lapse experiments from the Incucyte. It segments nuclei, tracks
them through divisions, calls G1 / S / G2-M from the red and green reporters, and produces
population and single-cell results ready for Prism. Everything runs on the Mac: no account,
no sign-in, no internet needed, and images never leave the computer.

## Install

1. Download the DMG for your Mac (Apple menu > About This Mac shows *Apple M…* or *Intel*):
   - Apple silicon: [PIP-FUCCI-AppleSilicon.dmg](https://github.com/Abed97/cell-analysis/releases/latest/download/PIP-FUCCI-AppleSilicon.dmg)
   - Intel: [PIP-FUCCI-Intel.dmg](https://github.com/Abed97/cell-analysis/releases/latest/download/PIP-FUCCI-Intel.dmg)
2. Open it and drag **PIP-FUCCI** onto **Applications**.
3. First time only: double-click PIP-FUCCI and click **Done** on the warning. Then open
   **System Settings › Privacy & Security**, scroll down, click **Open Anyway**, and confirm.
   The DMG includes a one-page guide, *How to open PIP-FUCCI the first time.pdf*.

The app isn't registered with Apple (that needs a paid developer account), which is why macOS asks
once. Needs macOS 12 or later. To update, download the new DMG and replace the app; results are
kept because they live next to the images.

## Using it

1. **Experiment** – open the Incucyte export folder (or try the demo data). Check the channels,
   enter frame interval and pixel size, assign wells to conditions on the plate map, then
   **Run analysis**.
2. **Segment & gate** – scrub through each field with masks coloured by phase, and set the two
   gates on the red-vs-green scatter. Every cell is reclassified when a gate moves.
3. **Population** – phase fractions over time, cell number and confluence (mean ± SEM across
   wells), and red-vs-green plots at selected timepoints.
4. **Single cells** – track heatmap, per-cell image montage and traces, phase durations, lineage,
   and a dot plot of durations by condition. Tracks can be excluded or marked as reviewed.

**Export all tables** writes Prism tables (one column per well, grouped by condition), a
per-cell-per-frame table, a per-track table with durations and censoring, and a JSON record of
every setting and software version, into `PIP-FUCCI results/exports/` next to the images.

### Expected export format

Raw single-channel TIFFs (16-bit, unscaled), one per channel per timepoint per well and field,
named the Incucyte way: `Prefix_B2_1_02d04h30m.tif` or `Prefix_B2_1_2026y11m19d_14h30m.tif`.
The channel can be in a folder name (`Phase/`, `Green/`, `Red/`) or in the prefix; the app
groups the files and lets you confirm which group is which channel.

## How the analysis works

- **Settings are inputs, not constants.** Frame interval, pixel size, nucleus diameter, max speed
  and max gap are entered per experiment; search radius, gap frames, smoothing window and the
  daughter search radius are derived from them, so 30-min and 60-min data use the same code.
- **Segmentation** on the combined red + green image after background subtraction. The threshold
  comes from the background noise rather than Otsu so dim cells at G1/S and S/G2 are kept;
  touching nuclei are split by watershed seeded from each channel separately.
- **Tracking** by optimal frame-to-frame assignment (distance plus a colour-change penalty),
  gap closing, and division detection that uses PIP-FUCCI logic: the parent is red (G2/M) the
  frame before, the daughters' red drops and they look alike. Both daughters are tracked; doubtful
  parent assignments are flagged for review rather than forced.
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

The DMGs are built by GitHub Actions on GitHub's Mac machines (`.github/workflows/build-mac.yml`):
it runs the tests, builds the app for Apple silicon and Intel, signs it ad hoc, runs a self-test
of the built app, and makes the DMGs.

To publish: change `__version__` in `pipfucci/__init__.py` (for example to `0.1.1`) and push to
`main`. When that version has no release yet, the workflow builds both DMGs and publishes a
GitHub Release tagged `v0.1.1`, so the download links above always point at the newest version.
Other pushes only run the tests. The app checks for newer releases and offers a download.

## Running from source

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e .            # add [cellpose] for the optional Cellpose segmentation
python -m pipfucci          # opens the app window (use --browser for a browser tab)
python -m pytest tests      # tests on synthetic data with ground truth
```
