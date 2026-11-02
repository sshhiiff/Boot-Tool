**Just want the app?** See [DOWNLOAD.md](DOWNLOAD.md): download it from the Releases page, no Python or Terminal needed.

# Boot Tool

Turn raster images into clean SVG, make halftones, and split artwork into screen-print plates. Runs on your own computer; nothing is uploaded.

## Install (Mac)

```bash
cd ~/Downloads/boottool          # the unzipped folder
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
brew install librsvg             # lets the tool check its own results
boottool --version
```

Next time, just `cd` into the folder and run `source .venv/bin/activate`.

**Upgrading from the old "vecforge" version:** the command is now `boottool`. Unzip into a new folder and follow the steps above. You can delete the old folder.

## Open the interface

```bash
boottool ui
```

A page opens in your browser (`--port 9000` if 8765 is taken). Drop in an image. Tabs:

### Convert
Raster to vector. **Look** goes Clean to Crunchy. **Upscale** runs 0.25× to 6×: below 1× the image is shrunk first for a chunkier look (the SVG still opens at the original size); above 1× it is enlarged first, capped at 4096 px. **Pixel** upscale keeps stair-steps. Threshold, speckle removal, smoothing, simplify and corner sharpness each have a slider. *Copy command* gives the matching `boottool run` for a whole folder.

### Batch convert (in Convert)
Open the **Batch** view, drop in many images (or **Add images** / **Add folder**), type an **output folder** on your computer, and press **Convert and save to folder**.
- Every SVG goes into that one folder, named like the original. If a name is taken a number is added (`logo-2.svg`); nothing already in the folder is ever replaced.
- **Same for all** uses the Convert settings for every image. **Different per image**: click an image to see it live and give it its own settings; a ✎ tag marks images with their own. **Copy to all** and **Reset this image** help.
- A bad image is skipped and the rest carry on (switch that off if you prefer). **Stop** ends the run. You can also download a .zip of the results.
- The tool only answers its own page on your computer, because it can now write files.

### Print
Screen-print plates on a real page. Pick a **Method**:
- **One ink**: a single plate.
- **CMYK**: four plates. Plate colours and screen angles are editable (the separation itself always uses standard CMYK).
- **Spot colour**: up to 10 inks, paper never printed. **Flat graphic** gives one solid plate per ink with trap (default 0.20 mm). **Photo** halftones (or dithers) every ink on its own plate. Pixels between two inks are shared between both plates, and if you change an ink's colour the photo is separated using your colour.

**Levels** (every method) works like Photoshop's: left handle = what prints solid, right handle = what drops out to paper, middle = midtone lightness. Drag the handles or type values. **Auto** sets them from the picture, and is applied when you open a continuous-tone image.

**Render**:
- **Screen halftone** follows Photoshop's Image → Mode → Bitmap → Halftone Screen: Frequency (LPI), Angle, Shape (round, diamond, ellipse, line, square, cross, or your own drawn pattern) and Output resolution (pixels/inch, default 300). Dots merge inside shapes and are cut off at their edges.
- **Dither**: Floyd–Steinberg, Atkinson, Stucki, Bayer, Blue noise, **Serpentine** or Random. **Dither size** is how big each grain is on the page, from 0.10 mm (fine) to 2 mm (chunky). Grains are vector squares.
- **Serpentine** makes long, maze-like strokes (Turing-style patterns). Pick a pattern (Mazes, Coral, Fingerprints, Loops, Spots) and set **Pattern size**, the width of one stroke cycle (3 to 40 grains), separately from the grain size.

### Page output
- Page: A5, A4 or A3, portrait or landscape, adjustable margin.
- Registration marks: six (three top, three bottom) or four corners, default 2 pt, pure black, on every plate.
- Ink name printed on every plate (optional).
- **Layered SVG**: one file, one layer per plate. **.zip**: one SVG per plate.
- Downloads are always pure black. The preview keeps ink colours; the single-plate tabs show the film as it will print.

## Notes and limits

- The ink name is live text (Arial Narrow). In Illustrator use Type → Create Outlines if you need shapes.
- CMYK uses a simple RGB to CMYK formula, not a printer profile. Use **Black amount** to control the black plate.
- Halftones and dithers make big files: a full A4 photo at 30 LPI and 300 px/in is roughly 3 MB per plate and takes several seconds, and CMYK is four plates. Lower the output resolution for lighter files. Above about 300,000 dots per plate the tool asks you to lower the LPI.
- I could not test opening the layered SVG in Illustrator. Layers are named from each plate; tell me if anything imports wrongly.

## Batch conversion (command line)

```bash
boottool trace in.png out.svg --upscale 3
boottool run ./images ./out --provenance provenance.csv    # resumable, writes manifest.jsonl
boottool report ./out                                      # side-by-side review sheet
boottool dataset ./out --min-score 0.92                    # clean train/val split
```

`provenance.csv` columns: `file`, `license`, `source`, `author`, `year`. `provenance_declared` records that you gave a licence label; it does not verify it.

## Tests

```bash
pytest
```


## Compose (0.6)

A page editor for building a composition from your own graphics, then printing it.

- **Library**: every Convert result (single or batch) is saved automatically in `~/BootTool/Library`
  (set `BOOTTOOL_LIBRARY` to move it). Hover the Library tab in Compose to pick graphics.
- **Artboard**: any size from 20 to 1200 mm, with A5/A4/A3 landscape and portrait presets.
- **Tools**: Select (move, resize, rotate), Nodes (points and handles), Pen, Text (any installed
  font, turned into outlines; extra fonts go in `~/BootTool/Fonts`) and Shape, plus Unite / Subtract / Intersect /
  Exclude / Split / Combine / Separate / Join ends and colour swatches.
- **Send to Print** opens the page in Print as a flat-graphic spot-colour job at real size.

Compose uses [paper.js](http://paperjs.org) (MIT licence), built from its source and shipped in
`boottool/static/vendor/` so everything works offline.

The Print tab also has a **Draft preview** tickbox (bottom right of the preview): coarser, much faster previews for
halftones. Downloads are always full quality.

**New in 0.8.3**
- **Groups** in Compose (Ctrl/Cmd+G, Ctrl/Cmd+Shift+G) and an expandable Layers list.
- **Fill tool** (F): click a hollow area to fill it with a shape placed behind the graphic. Grow and Close gaps settings.
- Simulate: rotate, crop to shirt, drag from the library, tidy number boxes, fixed library bar.

**New in 0.8**
- **Simulate** tab: designs on a T-shirt drawn to real size (XS–XXL unisex chart, short/long sleeve, front/back, any shirt colour). Several designs per side from the Convert library or the new Print library; drag, resize, type exact cm, snap to centre, Centre / Chest / Full front, Duplicate, Remove. **Save mock-up as JPG**, with an optional *Include measurements* tickbox.
- **Design size** (Print): *Fit to page* or *Set size* in mm; the design prints exactly that size, centred, and the page grows to Custom if needed.
- **Save to Print library** (Print): stores the finished design (real size, ink colours) in `BootTool/Prints` for Simulate.

**New in 0.7**
- **Eyedropper** (Print, Spot colour): set *Ink colours* to *Pick from image*, or click the ⌖ beside any ink, then click the original to choose that ink. Sample 1 px, 5×5 or 11×11; **+ Pick another ink** adds up to 10; Esc finishes. The picture is separated using exactly your inks.
- **Keep solid areas solid** (Print, Spot photo): flat areas of an ink print as clean vector instead of dots; the *Solid detection* slider sets how readily an area counts as flat.
- **Editable file name**: click the name in the header; it names downloads, the library item and Compose exports.
