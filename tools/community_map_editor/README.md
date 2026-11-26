# TRIBES 3 Map Editor

Open the **TRIBES 3 Map Editor** launcher to enter the local Unreal authoring project. The first launch creates a blank CTF draft with flat ground, lighting, three spawns per side, and one flag marker per side. If you install the Damnation template, use **TRIBES Map Editor → Copy Damnation (2026)** to create a separate draft using that terrain, those bases and materials.

- **Place geometry.** Use **Browse Structures and Props**, then drag a mesh from the Content Browser into the viewport. Use Unreal's move, rotate, and scale tools to position it.
- **Apply textures.** Use **Browse Grass, Rock and Forest Materials** and drag a material onto a mesh. **Browse Existing Materials and Textures** shows the other surfaces in the project.
- **Set up CTF.** Move the existing **Blue Spawn**, **Red Spawn**, **Blue Flag**, and **Red Flag** markers in the World Outliner. The TRIBES menu can add spawn and flag markers. Each side needs at least one spawn and exactly one flag. A **Route Start** and **Route End** pair lets you check a straight player-sized path through an entrance.
- **Try the draft.** Leave Steam open, close any running TRIBES game, and choose **Build & Playtest**. Unreal saves your changes, closes to free memory, and cooks the map. The launcher opens a private offline TRIBES game, then reopens the same draft when you quit it. Keep the launcher console open throughout the build and test.

### Work in order: game first

The **TRIBES Map Editor** menu follows the workflow:

- **0 · Start a draft.**
- **1 · Convert from…**, with one submenu per source game (Tribes 1, Tribes 2, Tribes:
  Vengeance, Tribes: Ascend) listing that game's tools as numbered steps.
- **Shared draft tools, in order:**
  - 2 · Review conversion;
  - 3 · Textures, models and sky;
  - 4 · Lighting;
  - 5 · Gameplay (markers, equipment, shields);
  - 6 · Build and publish.

The conversion window starts the same way. Choose **Converting from** first. The window
then lists that game's steps, each with what it needs and what it produces, and the open
dialog offers only that game's source files plus existing intakes. **Any game** keeps the
previous open-anything behaviour. The layout is data in `game_workflows.py`, so a new game
or step is a data change.

- **Pick or add steps.** Edit `GAMES` and `MENU` in `game_workflows.py`.
- **Keep them consistent.** `test_game_workflows.py` checks that every editor action is
  reachable and valid.

Drafts live in the Community maps folder inside the authoring project. Put newly imported assets under Content/Community so the draft cooker can package them. Each build stores its diagnostics under the local Runs folder.

Windows map cooks embed shader code in their material assets so those
materials can render when loaded by Starter. Rebuild older exports from
their saved drafts if the game shows default materials or missing weather.

## Local setup

In the Windows ZIP, extract the entire archive and open **Start TRIBES 3
Map Editor.bat**. It creates the workspace in `%LOCALAPPDATA%\Tribes3MapTool`
on the first run and reopens it on later runs. Install Python 3.13 with its
Windows launcher, Pillow, and Unreal Engine 5.3 before opening it. If your
Unreal installation is unregistered, run the BAT with `--engine "D:\UE_5.3"`
on the first run. Use `--workspace` to select a different workspace parent.

For a fresh workspace on Windows, use Python 3.13 and Unreal Engine 5.3.
Run these commands from the source checkout with your own workspace parent.
Setup finds Unreal Engine 5.3 through Epic Games Launcher and opens the
new workspace.

```powershell
py -3.13 -m pip install Pillow
py -3.13 tools/community_map_editor/bootstrap.py "D:\Maps" --launch
```

For an unregistered engine, pass its installation or Win64 binaries
folder as the second argument. To reopen the workspace, run
`py -3.13 tools/community_map_editor/launch.py "D:\Maps\CommunityMapEditor\local-settings.json"`.

The setup creates an isolated `CommunityMapEditor` folder and generates a
starter Grass, Rock and Forest palette. It refuses to replace an existing
workspace. You can create blank CTF drafts, import missions or intakes, and
review assets without Steam or a host bundle.
With the bundled Direct Host exporter, this workspace can also package
and stage imported CTF drafts against a host bundle that supports standard
Unreal World Settings.

### Game-compatible setup

For offline playtesting, use the game-compatible UE 5.3 build and a private
`CommunityMapEditor/project/Stonehenge53.uproject` with the native
world-settings plugin. Leave `authoring_only` unset or set it to false.
Use this setup before creating drafts that you intend to play offline.
Set `project`, `engine_binaries` and `ddc` in the private launcher JSON.

To enable **Build & Playtest**, also set `preview_base` to an inventoried
private playtest bundle, `game_source` to the verified game folder, and
`game_workspace` to a separate NTFS staging folder. To enable **Copy
Damnation (2026)**, install the editable Damnation template in the project
and set `seed_descriptor` to the corresponding map descriptor. Direct Host
export uses its own baseline and works without the offline playtest paths.

## Build for Direct Host

The standalone checkout includes the exporter. To use a separate copy,
set `direct_host_exporter` in the launcher config to its full path. With an imported
draft open, choose **TRIBES Map Editor → Build for Direct Host…**. The editor
checks the CTF markers, saves the draft, and closes for cooking.

In the export window, enter the map's display label, select the Direct Host
match bundle that will be used as the baseline, and choose an export folder.
You can also select a minimap export made for this draft's ID. **Cook and
export** creates a staging package and reopens the draft. This path does not
start a TRIBES game or need Steam. The launcher prints the export path and
saves `host-export.log` with the build diagnostics.

For a new map, open **Map Releases…**, select the export, and choose **Stage
release…**. Select the baseline bundle used for the export and a parent folder
for the separate staging copy. The tool verifies the baseline against the
export receipt, builds the staging copy, and verifies its catalog and files.
Re-export if the baseline has changed. The same operation is available from
the source distribution root.

```powershell
py -3.13 -B tools/t2_map_release.py stage "<export folder>" "<baseline bundle>" "<new staging folder>"
```

Run the host's normal preflight, then use the export's
`playtest-checklist.txt` in a private match. It lists the spawn and flag
coordinates, approved equipment and power circuits, and minimap checks when
present. Keep the checklist with the export receipt. **Stage rollback…**
can use the staged bundle to produce a separate copy with the catalog
restored to its state before the new map was added.

### Check map releases

Open **TRIBES Map Editor → Map Releases…** for the staged build history.
Individual and batch exports record their package and baseline checksums.
Use **Add existing export…** to verify and register an older portable folder,
or **Verify staged files** to recheck a recorded export.

Enter a running host's HTTPS address and port, then choose **Read live
catalog**. The Live catalog tab displays the host's current content and
manifest hashes. To compare, select a staged build for that map and choose
**Compare with live**. The comparison checks map-owned cooked files, objectives,
and minimap settings and artwork against the host manifest. The content hash
shown by the host also reflects shared dependencies. Live status is read from
the selected host when you connect. Applying a staged package uses the normal
Direct Host staging and deployment workflow.

### Update or roll back a map

Open the existing draft, choose **Build for Direct Host…**, and select
**Update this map ID**. Use the Direct Host bundle containing that draft’s
current release as the baseline. The export keeps the map ID and includes a
verified backup of files it replaces or retires.

In **Map Releases…**, select that export and use **Stage release…** for the
update or **Stage rollback…** to restore the previous revision. Choose the
appropriate input host bundle and a folder for the new staging copy. Both
actions verify the input and output packages. The optional same-drive
hardlink setting reuses unchanged cooked files for bundles that keep them
immutable. These actions require `t2_map_release.py` beside the configured
Direct Host exporter.

## Repair textures in batches

Open a map draft and select up to 32 Community `Texture2D` assets in the
Content Browser. Choose **TRIBES Map Editor → Export Selected Textures for
Repair…**. Each private batch under `Saved/T3TextureBatches/<draft>` has
numbered originals and a manifest with their roles, hashes, target dimensions,
and edit notes. The default target doubles the size up to 8192 pixels on the
longest side. Put edited PNGs in `replacements/` with the same file names.

Choose **Review and Import Texture Batch…** to compare each replacement
with its original. The reviewer checks PNG format, size, and alpha. **Tile
preview** repeats both images so you can inspect the joins. Picks are saved
as you review. **Retry unresolved…** makes a fresh batch for rejected,
missing, invalid, or changed replacements.

Use **External texture queue…** in the reviewer to run an installed
command-line texture processor against the missing images. Select its
executable and enter its arguments as a JSON array, using `{source}` and
`{output}` for the PNG paths. Use `{width}` and `{height}` if your processor
accepts explicit target dimensions. The confirmation shows the remaining
image count and output megapixels. When you enter minutes and USD per
image, it also shows the estimated total time and cost before starting.

The queue saves its settings in the private batch and keeps every existing
replacement. **Stop after current** lets the current image finish. Run the
same batch again to continue missing images. The queue checks the output
PNG before adding it to `replacements/`. Generated images still require
approval before the editor can import them.

**Import approved** imports the exact reviewed bytes into the draft's
`TextureRevisions` folder and opens those textures in the Content Browser.
Turn on **Apply to draft mesh materials** to use the approved textures in
matching material parameters on the current map's mesh slots. Newly imported
T2 terrain has parameters for color, normals, and roughness. Older materials
with fixed texture samples can use the imported assets through the Material
Editor.

The editor creates new material instances before changing the mesh slots.
Earlier revisions stay available. Use Unreal's **Undo** for the current
apply, or reopen an earlier approved batch and apply it again. Save the
draft when you are happy with the result. The review uses Pillow in the same
Python that runs the launcher. For this project's Python 3.13 setup, install
it with `py -3.13 -m pip install Pillow==12.1.0`.

For RGB or RGBA color textures, pick **Adjust color** in the batch reviewer.
Choose a tint color or match the average color of a reference PNG, then enter
a strength from 1 to 100 percent. The tool uses the current replacement if one
is available. Otherwise it resizes the original to the batch target size.
Fully transparent pixels do not affect the color match, and the adjustment
preserves alpha. It opens the result in a new, unapproved batch so you can
compare it with the original and decide whether to import it. Earlier batches
and their approvals remain available.

### Merge surface detail

Select a normal or mask texture in the batch reviewer and choose **Merge
surface detail…**. Supply an 8-bit detail PNG and set its strength and the
number of repeats per axis. Use RGB or RGBA tangent-space normals with the
same axis convention. The merge reconstructs the positive Z component from
the red and green channels and reorients the detail against the base normal.
[Reoriented normal mapping](https://blog.selfshadow.com/publications/blending-in-detail/index.html)

For masks, choose roughness or ambient occlusion and the material's target
channel. Read a grayscale detail image directly or select a channel from a
packed detail PNG. Middle gray leaves roughness unchanged. White leaves
ambient occlusion unchanged. The other target channels keep their values.
The reviewer opens the unapproved revision with the edited channel selected.

The preview reports the mean and peak differences between opposite edges on
the 0–255 channel scale. Use **Tile preview** to see the same texture repeat
in both directions. Check the revision before approving it for import.

### Assemble a sky cubemap

Export a batch with six sky textures and put the edited faces in
`replacements/`. Open **Sky faces…** in the review window. Choose the texture
for each positive and negative axis and its clockwise rotation. Names that
end in suffixes like `_PX` and `_NZ` are picked up automatically.
**Check seams** saves the layout and compares all 12 shared edges. The report
shows original and edited mean differences and the edited peak per edge.
Large differences are marked for review.

Approve all six replacements in the main review window. The six replacements
must be equal square power-of-two textures. Choose **Build DDS** to save the
file or **Build and import cubemap** to also import it into the active draft.
The build saves an uncompressed `SkyCube.dds`, a `SkyFaces.png` contact sheet
with the original and approved images in their assigned orientations, and a
report containing face directions, rotations, hashes, and seam measurements. It preserves
earlier builds for rollback. The import rechecks the six approvals and saves
a versioned TextureCube under the draft's TextureRevisions folder, then shows
it in the Content Browser. [DDS cube layout](https://learn.microsoft.com/en-us/windows/win32/direct3ddds/dds-file-layout-for-cubic-environment-maps) · [Cubemap export settings](https://dev.epicgames.com/documentation/en-us/unreal-engine/exporting-cubemaps?application_version=4.27)

In a Tribes 2 import or Blank CTF draft, select that TextureCube and choose
**TRIBES Map Editor → Apply Selected Cubemap as Draft Sky**. This places a
background around the playing area. Applying an older revision changes the
same backdrop. Undo or delete the **Reviewed Sky Backdrop** actor to restore
the original sky. Save the map before cooking. For copies of existing maps
or drafts with authored sky meshes, use the cubemap in the existing sky
material. [Unreal sky materials](https://dev.epicgames.com/documentation/en-us/unreal-engine/sky-atmosphere-component-in-unreal-engine)

When a Tribes 2 draft creates a source sky batch, open it from **Review
and Import Texture Batch…**. In **Sky faces…**, assign the six faces and check their
seams. Source names ending in `_PX` or `_NZ` can fill the matching axes
automatically. Choose **Use original images** to build with the existing
artwork, or approve replacements for the usual edited build.

To qualify the sky path on a Windows machine with Unreal 5.3 and Pillow,
run the following from the repository root. Use a new output directory.

```powershell
py -3.13 tools/community_map_editor/qualify_sky.py "E:\UE53-src\Engine\Binaries\Win64" "E:\T3-SkyQualification"
```

The test takes the shared native resource lock and creates a scratch project.
It checks rendered colors on all six world directions, an asymmetric face
detail, and exclusion from scene captures. A successful test writes
`qualification.json` with the sampled colors and hashes of the cooked map,
mesh, material, and cubemap.
To stop a qualification, create `cancel.json` in its output directory with
`{"cancel": true}`. The supervisor cancels that run's editor or cook job.

## Inspect a native Unreal map package

Choose **Open source or intake…** and select a Vengeance `.tvm` or `.pkg`,
an Ascend `.fmap`, or a compatible UE3 version 805 `.udk`. Search by object
path or class. **Save full inventory…** writes all import and export
records to JSON, including dependency paths, export payload ranges and
the source SHA-256. The command-line reader can select a game explicitly.

```powershell
py -3.13 tools/community_map_editor/unreal_package_reader.py "D:\Maps\MyMap.fmap" "D:\Maps\PackageInventory.json" --game ascend
```

Package inventory supports uncompressed files and UE3 zlib or LZO
chunks. Tribes: Ascend client packages use LZO, which the tool decodes
itself with no extra install. Choose the scale in the import dialog with
**Scale preset**, or type your own units-per-metre value. For Ascend the
default is 1 unit = 2.52 cm (39.6825 units per metre). That is the exact
player match of 1.92 cm (TRIBES 3 player height over Ascend's Medium
armor) with the same x1.3125 margin Tribes 2 conversions use (105 cm per
T2 metre against an exact match of 80), because exact matches feel too
tight in game. The exact match is the second preset. The default is still
to be confirmed in game. Select **Import as draft…** to read the level's actor roster and
recover serialized transforms, links, and mesh references. If the package
has no adjacent `MyMap.import.json`, the editor opens a setup dialog. Select
the native terrain, review or change each team's flag stand and spawns, and
select optional companion files beside the source map. The dialog favors
Vengeance's saved team links when available and otherwise suggests spawns
by distance. **Use settings file…** opens an existing recipe, including a
recipe that supplies its own heightmap. Settings entered in the dialog are
recorded in the private intake. The actor review retains object and property
offsets.

Tribes: Ascend cooks each map as separate levels: a gameplay level such as
`TrCTF-Tartarus.fmap` (flags, spawns, base equipment), a world level
`Tartarus_Ter.fmap` (terrain, meshes, lighting, fog) and an optional sound
level `Tartarus_Sound.fmap`. Open the gameplay level. The tool imports from
the matching world level and merges the gameplay and sound levels into one
draft. A variant's own levels are preferred, so `TrTeamRabbit-Tartarus.fmap`
uses `TrTeamRabbit-Tartarus_Ter.fmap` when it exists. The dialog lists the
merged levels. Their actors are named after their level, for example
`TrCTF-Tartarus.UTTeamPlayerStart_3`. A settings file lists them under
`"levels"`. Meshes, BSP and component materials are still read from the
world level only.

**Out-of-bounds box.** Ascend maps fence their play area with long, thin
`BlockingVolume` walls, usually behind a visible force field, and cap it
with a flat volume high above. When all four sides enclose every flag and
spawn, the import uses that box for the playable bounds and the minimap.
It also presets **Boundaries…** to solid walls on the box, with the
ceiling at the cap volume. Object Review marks the volumes used and any
mesh standing on the box edge, such as Tartarus's force-field panels; keep
those as decoration or remove them. `KillZ` and `StallZ` are listed but not
applied. Applying them, along with flag rules and grace-time damage, needs
the runtime out-of-bounds layer (T3BackEndServer#111). An explicit
`"bounds_m"` setting still takes priority. On Tartarus the box is
2,477 × 2,064 m at the 2.52 scale, with the ceiling at 1,858 m.

Bind an existing project mesh to a recovered reference through the draft's
mapping controls.

In native Ascend maps, an authored `UTGame.UTTeamPlayerStart` can use the
imported terrain as its `Base`. Assign it in the appropriate team's
`spawns` list to use its serialized world position and yaw. Starts attached
to other actors require a manual placement review.

[Intake version 53](intake-v53.schema.json) adds authored global lighting
and fog to native imports. The manifest retains the earlier geometry
contract in `scene_format`. Ascend imports saved sun direction, tint,
brightness, skylight brightness, and exponential fog inputs. Vengeance
maps saved sunlight and common zone ambient brightness and approximates
matching enabled distance fog with UE height fog. Distinct zone fog
settings stay in Object Review. Saved inputs appear in the source
properties, and conversion-rule overrides still take priority.

[Intake version 54](intake-v54.schema.json) adds private, editable Ascend
point lights and spotlights with saved positions, color, radius, and
spotlight cones. Explicit falloff, brightness, and shadow inputs are
carried into the draft when provided. The Unreal light uses the saved
brightness as a multiplier of the destination light's default intensity.

[Intake version 58](intake-v58.schema.json) adds editable Vengeance lights
to native imports. Independently placed steady point lights and spotlights
with saved inputs become Unreal light actors. The draft radius, cone, and
tint use legacy Unreal approximations. The intensity multiplier is the
saved brightness divided by 255. The original values remain in Object
Review. Modulation, coronas, filtered lights, baked lighting, and inputs
that depend on missing source defaults still require review. Public
layouts omit individual light placements.

For Vengeance version 130/27, the importer reads the compiled level BSP
into an editable static mesh alongside the terrain. The mesh retains
material slots and the original texture coordinates in texel units. The
collision mesh keeps solid surfaces, including invisible blockers, and
allows play through non-solid surfaces and portals. Drafts with source
surface artwork use [intake version 26](intake-v26.schema.json). The editor
converts opaque and masked surfaces from RGBA8 or DXT textures and simple
`Shader` diffuse inputs. It uses the source texture dimensions to reconstruct
UVs and carries over two-sided settings. List companion `.pkg` files beside
the map in `asset_packages`. Missing packages and unsupported shader inputs
appear in source review alongside the original brush actors. Sky effects
still need conversion. Public layout exports
omit the BSP mesh and artwork files.

[Intake version 31](intake-v31.schema.json) restores embedded Model geometry
for Vengeance Movers and elevator volumes as nonblocking editor previews.
Mover keyframe offsets, playback timings, state and event names are in
`source-scene.json`. Object Review shows the saved state, key count and
move time. Nonidentity brush scale and shear need author review. Door
triggers and elevator forces still need gameplay conversion. Playtest and
Direct Host packages omit the authoring preview meshes and materials.

[Intake version 32](intake-v32.schema.json) adds source elevator gravity,
zone velocity, friction, and trigger links. Independent `ConstantLoop`
movers that use the ignore-obstruction rule are placed with movable meshes
and an Unreal Level Sequence. The path keeps the source pivot and key timing
and can play forward around the loop or reverse at its endpoints. The
launcher enables Level Sequence Editor and Sequencer Scripting for authoring.
The cooked asset uses the engine's sequence player.

[Intake version 33](intake-v33.schema.json) adds independent
`BumpOpenTimed` doors with solid brush geometry and the ignore-obstruction
rule. Build the updated [Community Map Motion editor plugin](Plugins/CommunityMapMotion)
for the editor version in use. It authors a standard Unreal level Blueprint
to start the timed sequence when a qualifying actor hits the door. The cook
contains the compiled level script and sequence. Linked triggers, doors
with other obstruction rules, and elevator movement stay in Object Review.

[Intake version 34](intake-v34.schema.json) builds always-active Vengeance
zero-gravity elevator fields from closed, convex source brushes. The updated
Community Map Motion plugin creates hidden, pawn-only query meshes that keep
the source brush shape and transform. The cooked level Blueprint saves the
character's gravity on entry and restores it after leaving the last
overlapping field. Fields with trigger links, extra forces, nondefault
terminal speed or projectile effects stay in Object Review.

[Intake version 35](intake-v35.schema.json) records the source actor behind
each saved event addressed to an elevator's tag. A single source event may
name several elevator brushes. Object Review shows both ends of the link.
Standard proximity triggers retain their position, collision cylinder,
activation mode, startup state, one-shot setting, and repeat and cooldown
values in the [private source scene](source-scene-v35.schema.json). Custom
event senders carry a review reason.

The updated Community Map Motion plugin converts eligible zero-gravity
fields linked to a normal pawn, player, or human proximity trigger. It
preserves each field's starting state, the trigger's one-shot behavior,
repeat interval and retrigger cooldown, and the grouping of its target fields.
Each repeat checks the current occupants. Eligible occupants keep the timer
running during cooldown, and the timer stops once none remain. The collision
query approximates the source cylinder with a flat-ended convex hull. The
server changes the state, and the cooked Blueprint sends it to present and
late-joining clients. Each client updates gravity for characters already
inside. Chained activation, custom trigger classes, unsupported forces
and incomplete groups stay in Object Review.

[Intake version 36](intake-v36.schema.json) extends saved event links to
Vengeance `TriggerOpenTimed` doors. A normal proximity switch can start
several doors and linked elevator fields under one source tag. A door
ignores activation throughout its opening delay, motion, dwell and closing
cycle. Other doors addressed by the same switch can still start. The cooked
level uses the engine sequence player and the original mover keyframes.
Groups that include unsupported receivers stay in Object Review. The
[private source scene](source-scene-v36.schema.json) retains the links and
review reasons. Other obstruction rules and linked sound still need
conversion.

[Intake version 37](intake-v37.schema.json) preserves doors that are
authored to stay open after their first activation. Eligible `BumpOpenTimed`
and `TriggerOpenTimed` doors retain their opening delay and keyframes.
The updated Community Map Motion plugin pauses the replicated sequence at
the open endpoint. Every input checks the same sequence state, so another
hit or switch activation leaves the door there. Object Review and the
[private source scene](source-scene-v37.schema.json) expose this setting.

[Intake version 38](intake-v38.schema.json) preserves standard proximity
trigger chains that lead to a supported door or elevator field. A
receiver can turn on, turn off or toggle when signaled. Enabling a trigger
checks pawns already inside its volume. A spent one-shot cannot be
reactivated. The updated Community Map Motion plugin builds engine
Blueprints for these receivers. The
[private source scene](source-scene-v38.schema.json) and Object Review
show the authored links. Cycles and groups with unsupported receivers or
senders remain in review, so a draft never installs only part of a chain.

Vengeance version 130/27 drafts with placed static meshes use
[intake version 27](intake-v27.schema.json). The importer matches the full
object path in the map or a listed companion `.pkg`. It preserves the
placement's position, rotation and scale, and keeps the material slot names
and authored triangle collision. [Intake version 55](intake-v55.schema.json)
adds saved vertex normals and secondary UV channels to the private geometry.
The editor still opens older intakes. Actors explicitly
marked hidden or nonblocking carry those settings into the draft. The
review lists missing companion packages. When the importer resolves any
static-mesh textures, it writes [intake version 28](intake-v28.schema.json)
and binds supported opaque, masked and two-sided materials in the draft.
List any separate texture `.pkg` files in `asset_packages` along with the
mesh package. Review missing or unsupported inputs and assign replacement
materials in Unreal. Public layout exports omit mesh geometry, texture
bindings, artwork and package receipts.

[Intake version 29](intake-v29.schema.json) and its
[model texture index](model-textures-v29.schema.json) keep separate `Shader` opacity
textures and simple `TexPanner` motion on the first UV channel. The editor
uses the source alpha for cutout or translucent surfaces. When the shader
specifies direct glow color and mask textures, the editor uses the masked
color for emissive output and maps the source brightness from 0–255 to
0–1. Panning starts from Unreal's time base. Other coordinate sources,
triggered motion and complex material graphs appear in Object Review.

[Intake version 30](intake-v30.schema.json) and its
[model texture index](model-textures-v30.schema.json) retain `NormalMap`
and `SpecularityMask` textures. The reader decodes RGBA8, CxV8U8, DXT5N
and 3Dc/BC5 normals. The editor uses tangent-space normals and maps the
specular mask's alpha to Unreal's scalar Specular input. Vengeance's
environment reflection materials still appear in Object Review.

For Ascend version 805 PC packages, the draft imports placed static meshes
from the map and any companion packages you list in its import settings.
Keep the companion files beside the map and add a setting such as
`"asset_packages": ["MyMapArt.upk"]`. The draft keeps the highest-detail
geometry, material slot names, actor transforms, and serialized triangle
collision. [Intake version 56](intake-v56.schema.json) also preserves the
mesh's saved normals, vertex RGBA colors, and up to eight UV channels in
private intakes. It can carry these meshes alongside local lights and
global environment settings. Public layouts omit the mesh assets.

[Intake version 39](intake-v39.schema.json) reads a direct RGB
`DiffuseColor` texture sample that uses the mesh's first UV channel.
For a named sampler it follows the material instance parents to find the
nearest override, or uses the sampler's saved default. When a source
package lacks the diffuse graph, an unambiguous base-color override can
still supply the texture. The reader resolves materials and textures by
object path, including listed companion packages. It reads inline BGRA8,
DXT1, DXT3 and DXT5 mips and uses the largest available supported mip.
The draft supplies an opaque base-color material for supported slots.
Object Review reports unresolved assets, smaller fallback mips, and
shader inputs requiring author review.

[Intake version 57](intake-v57.schema.json) also reconstructs explicit
`TextureCoordinate` channels and U/V tiling in a base-color sample. It
supports that sample multiplied by `VertexColor` RGB in either order.
The editor uses the imported mesh's UV data and vertex colors. Missing
UV channels, unmirror settings and other graph operations stay in Object
Review. The material is still opaque, and the public layout omits it.

Most Tribes: Ascend shaders tint or blend their base color, for example
`Multiply(texture, color)` or a `LinearInterpolate` between two layers.
For these the reader follows the color path from `DiffuseColor` through
multiply, add, lerp, desaturate, clamp and power nodes. It picks the single
RGB base-color sample, preferring diffuse-named textures and parameters
(`_DIF`, `Dif`, `DIF1`) over detail, overlay and mask layers, and a lerp's
A input over its B input. Masks, reflections and cube samples are skipped.
If two samples rank equally, Object Review names both. The draft binds
that texture without the tint, blend or custom tiling, and Object Review
says so. On Tartarus this binds 463 of 482 mesh material slots.

**Remastered textures.** In the import settings dialog, choose
**Remastered textures…** and pick a folder of upscaled PNGs. A file is
used in place of a source base color when its name is the texture's object
path with each `.` replaced by `__`, for example
`Arch_Drydock__Tex__T_DryDock_Crane_01_DIF.png`. Files from the texture
extraction and remaster tools already use this name. A remaster must keep
the original's aspect ratio, be at least as large, fit 4096 pixels a side
and 16 MB as an RGBA PNG, and fit the intake's texture budget (384 MB in
total, with 96 MB kept for originals). Otherwise the original texture is
used and Object Review says why. The private intake records each
remastered file's name and checksum. The import settings store the folder
as `"remastered_textures"`.

[Intake version 40](intake-v40.schema.json) also reads saved material
overrides on an unambiguously associated static mesh component. Copies of
the same mesh share one geometry asset and keep their own materials in the
draft and asset showcase. Empty override entries inherit the mesh's
material for that slot. Unsupported overrides appear in Object Review and
can be assigned a destination material in Unreal.

To recover cached textures, place `texture.cache.hdr.rtc` and
`texture.cache.data.rtc` beside the Ascend map. The importer matches the
full texture object path and uses the cached zlib mip when it is at least
as large as the available inline mip. If a cache entry cannot be read,
Object Review shows the reason and the draft uses available inline
artwork. The private intake records both cache checksums when it uses a
cached texture.

Most Ascend client textures keep their mips in a shared texture file
cache, such as `Textures.tfc`, named by each texture. The importer looks
for that file beside the map and then in up to three parent folders, so
leave the map in the client's `CookedPC\Maps\<Map>\` folder or copy the
`.tfc` beside it. It reads zlib or LZO mips from the cache and records the
cache file's checksum in the private intake. Mips the client never cooked
are skipped, and Object Review notes when a smaller mip was used.
[Intake version 41](intake-v41.schema.json) preserves tagged sphere, box,
and capsule collision from a placed mesh's BodySetup. Simple shapes keep
their centers, orientation, and dimensions. When the source uses simple
movement collision and per-polygon line collision, the draft preserves
both. A mesh's private geometry file records this under
`collision.simple`, with a `mode` of `simple` or `both` and a list of shapes
in metres. Capsules store radius and cylinder length.

[Intake version 42](intake-v42.schema.json) also preserves convex hulls.
Each hull uses its own saved vertices in metres, so gaps between hulls
stay open. Unreal rebuilds the derived physics data from those vertices.
Unusable hulls, mixed collision flags, lower-detail models, and ambiguous
component placements appear in Object Review. Public layout exports
omit the mesh files and source package receipts.

[Intake version 52](intake-v52.schema.json) keeps the selected Ascend
terrain's painted layers in their original order and resolution. It keeps
hidden layers for inspection. When slot zero is visible, it supplies the
base surface even when its saved paint mask is empty. Use **Terrain
layers…** to select replacements from the project palette. When a supplied
companion resolves a supported source color, the draft can use that color
with the project texture repeat. Missing material packages, procedural
filters and original shader inputs needing a manual assignment appear in
**Object Review**. Public layout exports omit the original paint and
artwork.

[Intake version 51](intake-v51.schema.json) imports a version 805 Ascend
level's compiled BSP as editable static geometry. It keeps the saved
texture coordinates and material slots. The collision mesh includes
invisible solid surfaces and skips portals and nonblocking surfaces.
Unsupported model layouts appear in Object Review. Public layouts omit
the BSP mesh files.

### Import terrain from a native map package

Omit `heightmap`, `origin_m`, `step_m`, `height_range_m`, and `raster_y` to
read the terrain directly from an Ascend `.udk` or compatible `.fmap`, or
from a Vengeance version 130/27 map with a G16 height texture. If the
Vengeance map references a texture in another package, put that `.pkg`
beside the map and list it in `asset_packages`, for example
`"asset_packages": ["MapTerrain.pkg"]`. Use the same setting for companion
paint masks and surface textures. The reader matches the full object path,
including its group, and records the companion's checksum.
Keep `format`, `game`, `units_per_metre`, and `teams`. When the package has
several terrains, set `terrain_actor` to the selected object path, such as
`TheWorld.PersistentLevel.Terrain_3`.

The reader keeps the source elevation samples, matching tile edges, and
serialized visibility holes. It uses Ascend's serialized actor placement
or Vengeance's cached terrain coordinates and generates a height preview.
Vengeance imports also preserve the source triangle diagonals. Supported
terrain has aligned, positive and equal XY grid spacing. Use the supplied
heightmap workflow for other layouts.

Vengeance terrain with RGBA8 paint masks uses [intake version 24](intake-v24.schema.json).
The private intake keeps the original alpha pixels, layer order, named
surface references, and serialized scale and rotation parameters.
Drafts with source color textures use [intake version 25](intake-v25.schema.json).
The editor imports supported RGBA8 and DXT textures as editable color
parameters, blends the layers in source order, and uses their cached
world-space projection when present. The projection stays fixed in world
space if you move a terrain tile. **Terrain layers** lets you substitute a
project palette surface for any source layer. The palette supplies normals
and roughness as well as fallback colors for missing textures. Unsupported
paint slots, source materials without direct color textures, and missing
projections appear in object review. Decoration layers still need
conversion. Shared layouts omit the original images and material names.

## Build sound packs (Tribes: Ascend)

Sounds resolve in layers, as the backend sound packs define them: first
the map's override, then the map's selected game pack, then the default
pack. Tribes: Ascend is split the same way, so you build two things.

1. **Game pack, once per game.** It reads every shared `.u` and `.upk`
   package in the client's `CookedPC` folder: weapons, armor, jetpack, flag,
   announcer and shared music.

   ```powershell
   py -3.13 tools/community_map_editor/sound_packs.py pack "D:\TA\TribesGame\CookedPC" "D:\SoundPacks" --set TA --ffmpeg "C:\Tools\ffmpeg.exe"
   ```

2. **Map override, once per map.** It reads the map's levels and keeps
   only sounds the game pack lacks, such as environment groups and map
   music, plus every placed ambient sound. Each placement is given in
   TRIBES 3 centimetres. It refers either to an event id or to its own
   inline wave slots and radii.

   ```powershell
   py -3.13 tools/community_map_editor/sound_packs.py map "D:\SoundPacks\TA\pack.json" "D:\SoundPacks" TartarusTA2026 "...\Maps\Tartarus\TrCTF-Tartarus.fmap" "...\Maps\Tartarus\Tartarus_Ter.fmap" "...\Maps\Tartarus\Tartarus_Sound.fmap"
   ```

   Use `--units-per-metre` to match the draft's import scale. It defaults
   to the Ascend import preset.

Each wave keeps its original `.ogg` and gets a 16-bit PCM `.wav` for
Unreal import (`SW_<Name>`). Folders follow the source
`Package\Group\Name`. Event ids are sound cue paths, and each event's
category comes from its `AUD_<CATEGORY>_` package prefix. `pack.json` or
`override.json` records the sources, the decoder, every event's node graph
and anything that could not be read. `SHA256SUMS.txt` covers every file.

Decoding needs `ffmpeg` with Vorbis support. Set it with `--ffmpeg` or
`T3_MAP_TOOL_FFMPEG`, put it on `PATH`, or install the optional `soundfile`
package. Keep the client files and the extracted audio out of Git.

## Inspect a legacy Unreal map export

Use the text reader to inventory an authored Tribes: Vengeance or Tribes:
Ascend `.t3d` export. Select the game and supply the source map's world-unit
scale in units per metre.

```powershell
py -3.13 tools/community_map_editor/unreal_legacy_reader.py "D:\Maps\MyMap.t3d" "D:\Maps\SceneReview.json" --game vengeance --units-per-metre 50
```

Use `--game ascend` for an Ascend export and replace the example scale
with your map's scale. The `legacy-unreal-scene` report stores the source
file hash, actor and subobject hierarchy, transforms in metres and
quaternions, model references, and review issues. Source properties and
brush polygon statements retain their line numbers. Ambiguous
references, malformed placements, and classes without conversion rules
are marked for review.

### Create a draft from an Unreal map or text export

Put a 257 × 257, 16-bit grayscale PNG terrain export next to the import
settings. Save the settings as `MyMap.import.json` beside `MyMap.t3d`.
Use the authored world scale, terrain origin and elevation range for your map.

```json
{
  "format": 1,
  "game": "vengeance",
  "units_per_metre": 50,
  "heightmap": "MyTerrain.png",
  "origin_m": [-128, 128, 0],
  "step_m": 1,
  "height_range_m": [0, 20],
  "raster_y": -1,
  "teams": [
    {"flag": "BlueFlag", "spawns": ["BlueSpawn"]},
    {"flag": "RedFlag", "spawns": ["RedSpawn"]}
  ]
}
```

Choose **Import Mission or Intake…** and select the source file. Use
`game: "ascend"` for an Ascend map. Set `raster_y` to 1 if PNG rows
move toward positive world Y, or -1 if they move toward negative Y.
The first team becomes blue and the second becomes red. Use
`LevelName.ActorName` when the same actor name appears in multiple levels.
An optional `bounds_m` pair of metric XY corners frames a smaller playable
rectangle inside the heightfield.

The [version 22 intake](intake-v22.schema.json) preserves the editable
terrain, explicit CTF markers, minimap, and original scene review. Ascend
drafts containing native static meshes use [version 23](intake-v23.schema.json).
Select a static mesh actor in the review and use **Map selected mesh…**
to bind an existing project model. For Direct Host exports use a model
inside a named folder under `Content/Community`. The public layout keeps the CTF
placements and anonymous model assignments with generated flat terrain.
Original models, binary geometry payloads, and additional source behaviors
still require conversion.

## Import a Tribes 1 draft

Choose **TRIBES Map Editor → Import Mission or Intake…** and open the `.mis`
or a `.vol` containing legacy missions. Choose a mission from the list when
the volume contains several. The tool can read the terrain from the same
volume, a companion `.ted` or `.vol`, or loose `.dtf` and `.dtb` files. For loose
terrain keep the `.dtf` and the referenced `.dtb` next to each other. The CLI
accepts `--terrain` for an explicit terrain file and `--mission-member` for
an archive with several missions.

Binary import includes every tile intersecting the playable rectangle and CTF
markers. It supports uncompressed and LZH-compressed height and material grids
and keeps the original material flags and indices for review. Repeated terrain
blocks share one stored heightfield. For collision and preview, shared edge
heights come from the tile with the larger grid index. The original heights
remain available in the intake. Cells with a nonzero source empty level become
holes in the preview and in the draft's visible and collision meshes.

The reader looks for the terrain's `.dml` material list in the mission or
terrain archive, beside the selected terrain, or in a companion archive such
as `RidgeTerrain.vol`. Open **Terrain layers…** to map each source paint slot
to Grass, Rock, or Forest. The draft assigns those PBR materials per source
cell. Slots without a recognizable name start with a Rock fallback. The
intake keeps the original paint indices and orientation flags for reference.

To use artwork you have rights to include, open **Source artwork…** and choose
its folder. The tool reads opaque PNG, Windows BMP and eight-bit Tribes PBMP
files, including files in VOL archives in that folder. PBMPs can use their own
RIFF palette or a matching keyed PL98 `.ppl` bank. If day and night archives
use the same palette key, place the chosen palette next to the archives as a
loose file. **Terrain layers…** shows unmatched artwork and keeps the PBR
choices available. Imported artwork follows each cell's stored rotation and
flips. The CLI accepts the same folder via `--artwork`.

For an authored heightfield, use a 257 × 257 grayscale 16-bit PNG with its
metric JSON file. For example, place `Ridge.height.png` and `Ridge.height.json`
beside `Ridge.mis`. The JSON maps PNG values to metres.

```json
{"origin_m": [-1024, -1024, 0], "step_m": 8, "height_range_m": [0, 128]}
```

The height range is relative to the supplied vertical origin. For a detached
heightmap elsewhere, use the file picker and keep its metric JSON beside it.
Use the same Python installation with Pillow as the texture tools.

The [version 11 intake](intake-v11.schema.json) handles single heightfields.
The [version 12 intake](intake-v12.schema.json) adds a [tile grid and reusable
height blocks](intake-v12.terrain.schema.json). Binary imports use the
[version 13 intake](intake-v13.schema.json) for [paint and collision cutouts](intake-v13.terrain.schema.json).
The [version 14 intake](intake-v14.schema.json) adds private terrain artwork
and source checksums. Public layouts omit that artwork. These versions keep
raw object properties and hierarchy, metric heights, both teams' flags and eligible drop points,
fog distances, and a minimap from a valid mission rectangle. The draft gives
explicit start groups priority when a team has more than 24 drop points and
lists the remaining points in the source review. Mission script statements stay
visible as source data.

For Tribes 1 interiors, choose **Interiors and props…** and select the folder with the
map's `.dis` indexes, their `.dig` geometry and `.dml` material lists. The
folder can contain loose files or VOL archives. The tool also looks inside an
opened mission VOL. It builds the highest-detail mesh for the default state,
keeps the source material slot names and pixel UVs, and leaves link surfaces
open. Unresolved interiors appear in the review. A local mesh assignment can
override the imported mesh. The CLI accepts the folder via `--interiors`.

Include the referenced PNG, BMP or PBMP bitmaps and any keyed PAL or PPL
palettes in the same source folder or its volumes. The material list's
companion resources take priority. The draft uses each bitmap's dimensions
to normalize the source pixel UVs and imports opaque or cutout materials.
Object Review shows unresolved slots, including unsupported blend effects.
Those slots use the default Unreal material.

The [version 16 intake](intake-v16.schema.json) records bitmap and palette
checksums and includes the [material slot coverage](intake-v16.textures.schema.json)
and converted PNGs in the private package. It uses the same
[static geometry](intake-v15.interior.schema.json) as
[version 15](intake-v15.schema.json), which still opens. Public layouts omit
both the geometry and the artwork.

Place the interior's `.dil` lighting files beside the selected source
resources. The [version 17 intake](intake-v17.schema.json) reads the default
and named alarm or emergency states from the DIS index and checks that each
DIL belongs to the same DIG build. Mission lighting overlays use the same
source index to replace baked samples while retaining the base light tracks.
The importer bakes the first frame of those tracks and keeps separate texture
coordinates for the two states. Use the draft's lighting cameras to inspect
them in Unreal. The
[lighting index](intake-v17.lighting.schema.json) lists unresolved states
and source checksums. Timed light changes, alternate geometry, and moving
components still need conversion.

The same picker also accepts Darkstar DTS props. The [version 18 intake](intake-v18.schema.json)
reads a direct `shapeName` or `shapeFile`, or a literal `shapeFile` from
a supplied `StaticShapeData` definition. Files can be loose or in a VOL.
Scripts are read as data. The [shape geometry](intake-v18.shape.schema.json)
uses the highest visible detail, the default node transforms and the first
mesh frame. Supported embedded material slots use the existing bitmap
import and record their coverage in the [texture index](intake-v18.textures.schema.json).
Missing props and textures stay in Object Review. The
[version 19 intake](intake-v19.schema.json) keeps a named Darkstar
`collision` detail as separate [collision geometry](intake-v19.shape.schema.json).
Unreal uses it for the prop's static mesh collision. Props without that
detail use visible-mesh collision.

The [version 20 intake](intake-v20.schema.json) also stores the source
[animation](intake-v20.animation.schema.json) for the imported prop detail.
The [version 21 intake](intake-v21.schema.json) also binds materials used
only by a later animation frame. Reimport the source to pick up these materials
on a draft made with version 20.
It records clip durations, looping, parent-relative poses, mesh and UV
frames, object visibility, material swaps, triggers, and transitions. Key
positions are fractions of the clip duration. Public layouts omit the
model and its clips. Select an animated prop in the World Outliner and
choose **TRIBES Map Editor → Preview Selected DTS Animation…**. Pick a clip
and choose **Play clip**, or set the position and choose **Show frame**.
**Stop DTS Preview** restores the original prop. Preview geometry is
editor-only.

Install the [Community Map Motion editor plugin](Plugins/CommunityMapMotion)
built for the same Unreal Editor build to bake a clip for a cook. Run
Unreal's `RunUAT.bat BuildPlugin` with `-Plugin` pointing to
`CommunityMapMotion.uplugin`, `-Package` pointing to a separate output
folder, and `-NoTargetPlatforms` for the editor build. Copy the output to
the authoring project's `Plugins/CommunityMapMotion` directory.
With an imported prop selected, choose **Bake Selected DTS Animation…** and pick
the clip. The tool adds a stock Geometry Cache actor and keeps the
original prop as collision. Cache materials use the prop's material
assignments. The bake enables Geometry Cache shader usage on project
materials that need it. Baking applies the clip's loop setting and
samples it at 30 frames per second, plus its authored keys. Use
**Remove Baked DTS Animation** to restore the original visual. Props use
the default pose when no bake is assigned. Gameplay-triggered transitions
and animated collision still need conversion.

Build a Windows playtest cook or use **Build for Direct Host…** for the CTF
staging package. Original artwork and legacy gameplay assets still require
manual assignment or conversion.

## Import a Tribes 2 draft

Choose **TRIBES Map Editor → Import Mission or Intake…**. Open a local `.mis` or `.vl2`, pick the mission if the archive contains several, and select the referenced `.ter` when prompted. You can also reopen a verified intake folder through its `manifest.json` or open a zipped intake. Review the object list and double-click a row to inspect its original properties. **Create Unreal draft** creates a separate `DraftT2###` level. The existing marker checks and **Build & Playtest** operate on that draft.

The first T2 CTF profile converts a single terrain tile, including its holes, and imports team flag and spawn markers. The review list retains source properties and hierarchy for every object. The draft uses one capsule-clear centre per spawn sphere where a suitable nearby surface is available.

### Share a public layout

Open a verified intake in the import window and choose **Export public layout…**.
The new ZIP keeps placement coordinates, CTF teams and markers, equipment
and power relationships, minimap framing, and normalized sun and fog settings.
Object names and model references use generated labels. The exporter generates
flat placeholder terrain and fresh minimap artwork from the original footprint.

The ZIP also carries the walls, player warning, and flag limit selected in
**Boundaries…**. Opening the ZIP restores those settings. The floor and ceiling
retain their original heights using the saved source elevation range. To share
a layout without these settings, turn the boundaries off before exporting.

The ZIP contains a layout from the intake. It omits original terrain, game and
texture files, source names and locations, free-text properties, and edits made
later in the Unreal draft. Use **Open source or intake…** to reopen the ZIP, then
assign local meshes and check terrain and marker heights before building.

### Export private GLB mesh assets

In **Import Mission or Intake…**, open an intake with decoded models and
choose **Export GLB meshes…**. The ZIP contains glTF 2.0 mesh assets in
metres with a right-handed, Y-up coordinate system. It includes available
static detail levels, material slot names, saved normals, vertex colors,
UV channels, and embedded base-color PNGs for resolved slots. Compiled
triangle collision is exported as a separate mesh where available.
Base-color PNGs with embedded profiles are converted to standard sRGB.
[`mesh-export.json`](mesh-export-v1.schema.json) records the corresponding
source geometry hashes and the checksum of each GLB.

Keep this export private. It can contain original game meshes and
textures. Use **Export public layout…** for sharing layouts. The GLB ZIP
is a mesh export. Map placement, terrain, objectives, animation, and the
original shader and lighting data stay in the verified intake.

### Export and reopen a private intake

Choose **Export private intake…** to save the complete intake in one ZIP
with full-resolution PNG heightmaps and portable GLBs for decoded meshes.
Use **Open source or intake…** to reopen it. The editor reads placement,
terrain, objectives, and lighting from the original intake members.

The ZIP keeps the original `manifest.json` and its inventoried files.
When GLBs are present, `mesh-export.json` also contains the SHA-256 of
the original manifest under `intake_sha256`. The
[`terrain-export.json` schema](terrain-export-v1.schema.json) describes the
metric frame and the PNGs in `assets/terrain/`. Import verifies the manifest
links, file inventory, hashes, and heightmap frame before adding the intake
to the source library. Keep this ZIP private because it can include original
artwork and audio.

Each PNG is 257 × 257, unsigned 16-bit grayscale. Adjacent tiles share a row
or column of vertices. For a pixel at column `u`, row `v` in tile column
`c`, row `r`, use the following coordinates in target-world metres.

```text
x = origin_m[0] + (256*c + u) * step_m
y = origin_m[1] + (256*r + v) * step_m * raster_y
z = origin_m[2] + height_offset_m + pixel * height_step_m
```

The raster retains the draft grid, including edge padding for native
terrain. `terrain.json` records the hidden cells and exact heights.
PNG height rounding is at most half of `height_step_m`. A flat surface
has a zero height step. Earlier intake ZIPs still open.

### Inspect source objects and feature support

The **All** tab pins blocked objects first, followed by rows that need
review. Search by object, class, or reason, or filter by feature. Select a
row to see typed source properties, inherited defaults, and its parent,
team, power group, or asset reference when available. Double-click a row
for its complete source and decoder records.

**Feature support** lists how the Tribes 2 profile handles each feature in
the Unreal draft and on Direct Host. Invalid source placements and
unclassified objects stay in the intake and coverage report. The draft
build skips them and can place other valid instances that use the same
asset. The private `Saved/T3MapIntakes/Coverage` folder keeps an object
report for each inspected intake.

The [Tribes 2 object profile](tribes2_catalog.json) defines class aliases,
field types, defaults, relationship labels, support statuses, tabs,
columns, and filters. To customize them, copy it to
`Saved/T3MapIntakes/tribes2_catalog.json`. New draft rule receipts pin the
object profile alongside the conversion pipeline.

### Dock Object Review inside Unreal

After opening an imported draft, choose **TRIBES Map Editor → Docked
Object Review**. Drag its tab beside the viewport or the World Outliner.
The **All** and **Feature support** views stay available while you edit the
level. Use search, the feature filter, or **Needs review** to narrow the
list. Selecting an object shows its source properties, defaults, and
relationships in the pane beside the list.

New draft imports save their review under
`Saved/T3MapIntakes/DraftReviews`. The menu can regenerate it for older
drafts with a verified intake. The tab follows the active level and has
a **Refresh** button.

This panel uses the [Community Map Review editor plugin](Plugins/CommunityMapReview).
On first use, **Docked Object Review** offers to install the plugin.
After you approve it, the editor saves your work and closes while the
launcher installs the plugin and reopens the draft and dock. Building
the plugin uses the selected UE 5.3 installation, Windows C++ build
tools, and the .NET Framework SDK required by UnrealBuildTool.

Set `review_plugin_package` in your workspace settings to a trusted,
extracted package folder if you want the editor to install a prebuilt
copy. To install from the command line, close the map editor and run:

```powershell
py -3.13 tools/community_map_editor/install_review.py "D:\Maps\CommunityMapEditor\local-settings.json"
```

To install a trusted prebuilt package, extract it and pass
`--package "D:\Downloads\CommunityMapReview"` after the settings path.
The package must match the editor's exact build ID. The installer copies
the runtime files into the project and keeps a replaced plugin under
`Saved/T3PluginBackups`. Reopen the map editor to use the dock.

### Set conversion rules

Open **Conversion rules…** to edit fog distances and density, direct and
ambient sunlight multipliers, or terrain texture repeat. Visibility distance
sets the span used to calculate the height fog density. An explicit density
overrides that calculation.

Choose **This map** for a map override or **Tribes 2 defaults** for a shared
override. Select a single Sky, Sun, or TerrainBlock in the import list to
enable **Selected object** for the corresponding settings. The closest
override wins. Empty fields inherit, and **Load revision** fills in values
from an earlier revision or clears the scope back to inheritance.

The profile records its conversion formulas, units, and bounds in
`tribes2_conversion.json`. Imported drafts retain the exact rule revision,
source checksum, resolved values, and import selections. **Create New Draft
with Recorded Rules** reuses that revision and those selections. **Create New
Draft with Latest Rules** reuses the selections and resolves current rules.
Both commands use the verified intake to generate a separate draft. Manual
changes are kept in the original draft. The launcher build folder includes a
private `conversion-rules.json` for each build from a rule-tracked draft.

### Share a game profile

Open **TRIBES Map Editor → Game Profiles…** to export the current profile for
Tribes 1, Tribes 2, Vengeance, or Ascend as one JSON file. It contains the
conversion definitions, object fields and classes, review layout, feature
support, and pipeline for that source game. The exporter checks for file paths
and connection details. Project-specific rule overrides and map assets stay
in their authoring workspace.

Choose **Import profile…** to activate a shared profile. It applies to new
drafts and rebuilds using the latest rules after its checksum, configuration,
and compatibility with existing rule overrides pass validation. Earlier
imports remain available under `Saved/T3MapIntakes/GameProfiles/History`.
Select the earlier JSON file to activate it again. A rebuild with recorded
rules uses the original draft snapshot.

### Review or configure the conversion pipeline

Open **Conversion Pipeline…** from the editor menu to see which decoder,
draft import, and Direct Host steps passed, failed, skipped, or need
review. Each run records the profile checksum, elapsed time by step, and
details for failed or skipped work. The import window also has a
**Pipeline status…** button for decoder results.

Direct Host preflight checks the selected bundle before cooking single
drafts and batch entries. The check covers authored World Settings,
generator-linked equipment, powered shields and their power-loss
policies, inventory stations and their portable launcher adapter, and
authored flag-only heights and return timers. The bundle must inventory
the runtime files with matching checksums.

When a runtime capability is missing or cannot be verified, the build
saves `host-compatibility-issue.md` with the draft ID, baseline inventory
hash, and required support. Select that run in **Pipeline status…** and
use **Copy host issue draft**. The copied text omits local paths, map
titles, host addresses, and logs.

To customize the sequence, copy the bundled
[Tribes 2 pipeline profile](tribes2_pipeline.json) to
`Saved/T3MapIntakes/tribes2_pipeline.json` in the authoring project.
Preserve each operation's prerequisites, and give added checks unique
IDs. Weather, lighting stills, the showroom, and contact sheets are
optional. Add `check-source-classes` after intake verification and
before planning with `"options": {"classes": ["Sun"]}` to require a
source class. Add `check-ctf` after objective placement and before save
to run the native marker and collision checks. Add `check-minimap` after
packaging and before staging to require minimap artwork for Direct Host.

New drafts save the exact pipeline with their rule revision. Recorded
rebuilds reuse that sequence. Latest-rules rebuilds read the current
profile. The batch list also detects changes to the executable steps.

### Rebuild affected drafts together

Open **TRIBES Map Editor → Batch Rebuilds…** after editing a shared rule.
The **Drafts** tab lists which saved drafts get different rule values
and which values change. Select the affected drafts to rebuild. The
time estimate uses completed rebuilds from this project when available.

The batch pins the verified intake and exact resolved rule revision for
each selection. It creates separate editable drafts and saves its progress
after each one. Cancel in Unreal to stop after the current draft. Use
**Saved batches → Resume batch** to continue with the pinned rules, even
if you have since made more edits. A draft interrupted before its completion
checkpoint is rebuilt under a fresh ID. **Release remaining drafts** frees
them for a new batch with current rules.

### Cook and stage a rebuilt batch

In **Saved batches**, choose a completed batch and select **Cook & stage…**.
Choose a Direct Host baseline and an output folder. The launcher closes
the editor, cooks the approved drafts in order, and runs the normal
Direct Host compatibility checks on each. The output contains a portable
folder for each map, with its receipt, minimap when available, and
playtest checklist. The outer `results.json` lists the verified packages.

To put the whole batch on one host, open **Map Releases… → Stage batch
bundle…**. Select the completed export folder containing `results.json`,
the same Direct Host baseline, and a parent folder for the new bundle.
The tool keeps matching shared files once and rejects conflicting copies.
It checks that the combined catalog contains the batch and preserves the
existing maps. Keep the original baseline for rollback.

The standalone checkout also provides a command-line entry point.

```powershell
py -3.13 -B tools/t2_map_batch_release.py "<completed batch folder>" "<baseline bundle>" "<new staging folder>"
```

**Resume cook & stage…** uses the same saved inputs and baseline. Already
exported packages are verified and reused. Use **New staging run…** with
a new output location if you changed a draft or the baseline. The earlier
run and its exported files remain available. Staging prepares local
packages for a host operator to apply.

### Assign terrain materials

Open **Terrain layers…** to see the original layer names and choose a Grass,
Rock, or Forest surface from the project's PBR palette. Automatic assignments
use recognized names and show a Rock fallback for unrecognized names.
**Create Unreal draft** remembers the assignments for that terrain intake.

The draft uses the source weights to blend base color, normals, and roughness
on the terrain mesh. Palette textures repeat every 8 Unreal metres by default. Locations
with no weight use a known source material index when one is available, then
fall back to Rock. The generated `M_Terrain` lives beside the mesh in the draft
asset folder and is editable in Unreal. Original T2 texture images remain
outside this conversion step.

### Map buildings and props to project meshes

Select a building or static prop in the object list and choose **Map selected mesh…**. Filter the Unreal project's existing meshes and select the replacement. All instances with the same source class and file reference use that mesh. The editor uses each object's source position, full rotation, and scale. It places the meshes before checking spawn clearance and lists them under **TRIBES Import → Mapped meshes** in the World Outliner.

Mappings are saved when you choose **Create Unreal draft**. Reopening the same intake restores them from `Saved/T3MapIntakes/MeshMappings`. **Clear mapping** puts those instances back into review. If a project mesh is renamed or removed, select a replacement or clear the mapping before creating the draft.

To bring in a source building, choose **TRIBES Map Editor → Import Tribes 2
Interior…** and open its loose `.dif` file. Select an installed `hxDif.py`
reader when prompted, or set `dif_decoder` to that local path in the launcher
configuration. The import creates a reusable mesh in **Community/T2Interiors**
with source UVs, named material slots, and primary-detail triangle collision.
Assign the material instances you want in Unreal. Use **Map source object to
mesh** and filter by the source filename stem to place the asset at the
mission's authored positions. [Compatible hxDif reader](https://github.com/RandomityGuy/io_dif)

For a whole mission, configure `dif_decoder` before opening the map, or use
**DIF assets…** in the mission import window. The reader first looks in the
mission archive and beside the mission. Select the Tribes 2 content folder
to also search its loose files and asset VL2s. The optional `dif_assets`
launcher setting can point to that folder or a specific asset VL2.
Each decoded interior is reused across its authored placements. Existing
manual mesh mappings take priority. Missing or ambiguous sources stay in
the review list. Version 4 intakes carry the decoded meshes and their hashes
so they can be reopened without the original DIFs or decoder.

Version 43 intakes preserve secondary DIF geometry, UVs, material references,
and the source's pixel thresholds. Unreal uses up to eight visible detail
levels per mesh, including the primary one, and keeps triangle collision on
the primary model. The importer converts the source pixel thresholds to
Unreal screen fractions using a 720-pixel reference height. Secondary
geometry and base-color textures are preserved.

Version 44 intakes also carry secondary normal and alarm lightmaps. In
`interior-lighting.json`, the primary detail uses its model filename
and later records append the one-based secondary position, such as
`interior-0001.json#1`. Each record keeps its own surface indices,
projection, atlases, and decoder-exposed animation tables. Unreal uses the
corresponding material and lightmap UVs at each visible detail level.
An authored generator circuit switches every material on that building.

For mission entries that explicitly name a static `.dts`, choose **DTS assets…**
and select an installed `io_scene_dts` checkout or its `dtslib` folder. The
launcher can supply the same path with `dts_decoder`. DTS uses the mission and
archive search described above. To use a distinct content source, set
`dts_assets` to its local folder or VL2. The command-line equivalents are
`--dts-decoder` and `--dts-assets`. The codec runs in Python outside Unreal.

The converter takes the highest visible mesh detail, resolves the authored
node transforms, and keeps source material slot names and per-corner UVs.
The draft places reusable meshes from **Community/T2Shapes** at the original
mission transforms. Manual mappings still take priority. Animated shapes,
missing assets, and ambiguous sources stay visible in review. The v5 intake
carries the hashed neutral static meshes alongside any decoded interiors.
[Compatible DTS codec](https://github.com/henrikhasell/io_scene_dts)

When a decoded building or static shape references source artwork, the
importer searches its model folder, the mission archive, and the selected
T2 content. It verifies and packages the image as a PNG, then applies it as
sRGB base color to that actor's material slot. Common PNG, JPEG, BMP, TGA,
and DDS sources are supported. Missing and ambiguous matches stay in the
review list. The portable v6 intake records the source and converted hashes.
Manual project mesh mappings keep their project materials.

Automatic normal maps, transparency rules, datablock-only shapes, DIF doors,
and some gameplay equipment wiring still need conversion.
Confirm collision and objective clearance before cooking a mapped draft.

### Inspect imported models

A new Tribes 2 draft also gets an editable model showcase. Choose **Open
Model Showcase** from the **TRIBES Map Editor** menu while the draft is
open. **Return from Showcase to Draft** opens the linked CTF level again.

The showcase groups buildings, props, objectives, and equipment on a flat
lit stage. It shows unique imported models with their draft materials and
fits each model to its display base. Objectives and equipment represented
by source markers use labeled display shapes. The CTF build uses the
playable draft and its dependencies.

### Review map contact sheets

A new import in a rendering-enabled editor writes PNG contact sheets for
its buildings, props, objectives, equipment, and textures. Use **Refresh
Map Contact Sheets** while the CTF draft is open to capture its material
textures and the saved showcase again. The editor returns to the draft
after capture.

Texture cards distinguish source artwork from Unreal draft materials.
The comparison sheet pairs valid replacements from the draft's texture
batches with their originals and shows the review decision. Each revision
has an `index.json` with image hashes, dimensions, and export notices. The
sheets are kept in the project's private `Saved/T3ContactSheets` folder.

### Reuse models in another draft

New imports register their models in the project's **Shared Model Library**.
In an existing draft, use **Add Selected Models to Shared Library** to
register selected static mesh actors with their current scale, rotation, and
materials.

Open the draft that will receive the models. Select a placement marker or
aim the viewport at a surface, then choose **Open Shared Model Library**.
Select the model exhibits and choose **Place Selected Library Models in
Draft**. Unreal returns to the draft and places editable actors at that
point, with multiple models laid out side by side. **Return from Shared
Library to Draft** closes the library without placing anything.

Placed buildings keep their baked materials. To use a generator from the
destination draft, link the placed building to that draft's generator marker.

### Review baked interior lighting

Version 10 intakes preserve color and direction lightmap PNGs from the
primary DIF interior, with hashes, per-surface normal and alarm atlas
indices, and projections onto the source vertices. The review lists
the surfaces in each lighting state, missing bindings, and animation
data. The intake keeps the animation tables and buffers exposed by the
configured decoder.

Reimport the original mission to create a [version 45 intake](intake-v45.schema.json)
with [animated mask placements](intake-v45.lighting.schema.json). The
editor uses the source rectangles, intensity masks, and keyframe colors
to play ambient loops and flickers on the authored detail levels. Alarm
tracks appear only in the alarm state. Older intakes still open with their
baked lighting. Flicker updates select a weighted state at each authored
interval, with a 67 ms minimum update interval. Each actor and track uses
a stable pseudorandom stream so surfaces of one light stay in sync.

Named triggerable loops, ramps and flickers use a scalar on each affected
material instance. A ramp holds its final color and mask after it finishes.
The conversion review lists the supported names under **Source interior
lighting**. The scalar name is `T2 Light Start ` followed by the lower-case
source name. It defaults to `-1` (off). Map logic can set it to the current
game time in seconds to start the light, then set it back to `-1` to turn
it off. Set the scalar on all affected slots of that interior instance.
For inspection, set the start to `0` and use `T2 Light Preview Time` as
elapsed seconds.

The Tribes 2 reader also preserves literal light calls after the mission's
object section. Calls such as `Bunker.activateLight("Room Alarm");` resolve
only when the receiver is a unique, earlier interior with that named
track. The editor stores the resulting startup state on that placement's
materials. This keeps identical buildings independent. A start followed
by a stop leaves the first key displayed, as in Tribes 2. Calls inside
functions, conditional calls, computed names, and unresolved targets
remain in Object Review. Shared layouts omit the private source commands.

The imported material multiplies the source base color by the baked
color atlas on a second UV channel. The draft starts in its normal
state. When an import has baked interiors, the tool saves inspection
cameras for its linked circuits and one for any unlinked interiors.
An import with rendering enabled also writes an initial set of stills.

To add your own view, aim the perspective viewport and choose **TRIBES
Map Editor → Add Lighting Camera from Viewport**. Rename or adjust it in
the **TRIBES Lighting Previews** folder in the World Outliner. Select a
camera and choose **Frame Selected Lighting Camera** to recall its view.
**Render Saved Lighting Cameras…** renders the selected cameras or all
saved cameras when none are selected. **Capture Normal and Alarm
Lighting…** still captures a quick pair from the current viewport.

Saved camera renders show normal lighting, the complete alarm look, and
each linked circuit offline on its own. The private revisions in
`Saved/T3LightingPreviews` include the camera poses, field of view,
circuit IDs, and PNG hashes. The preview simulates the selected power
state in the editor. Direction maps are retained in the intake for
future rendering support.

When an imported interior has an alarm lightmap and an authored generator
circuit, the editor records the link automatically. [Version 46](intake-v46.schema.json)
also recognizes a same-team generator's parent group as a circuit and
includes its descendants. Multiple generators in the same group are
redundant. The alarm starts after the last provider goes offline.
To change a link, select the
baked interiors and one generator marker, then choose **Link Interior Lighting
to Selected Generator**. Select the interiors and choose **Clear Selected
Interior Lighting Links** to remove their links. Build for Direct Host includes
the link when the circuit has an approved generator. The offline preview
starts with normal lighting.

### Import sun and fog

When the mission supplies a usable global sun or sky, the import window shows which
settings will be applied. The draft places an Unreal directional light using the
source direction and RGB color, and uses the source ambient color to tint the
skylight. The importer uses the source fog start and color and estimates Unreal's
height-fog density from the distance between fog start and the visibility limit.
It skips the fog actor when that limit is at or before the start.

The conversion retains the original fields in the review list. Fog volumes
and visibility culling still need authoring. Multiple global suns or skies
of the same type stay in review.

### Add a collision perimeter

**Presets from the source game.** **Boundaries…** opens with settings that
match how the source game treats its play area. Object Review's playable
bounds row says which source fields were used and what was left out. The
behaviour below comes from the stock Tribes 2 scripts (`base/scripts.vl2`),
the Tribes 1.40 scripts (`base/scripts/server/game`), the Tribes: Vengeance
scripts (`Gameplay.u`, `Movement.u`) and the Ascend map data.

| Source | Play area | What the original does | Preset here |
|---|---|---|---|
| Tribes 2 | `MissionArea` rectangle, plus a soft `flightCeiling` faded over `flightCeilingRange` | Nothing is solid: players, vehicles and shots can cross. Every mode shows "You have left the mission area" with a beep. **CTF:** a carrier who leaves loses the flag at once; it is thrown toward its home stand and the 45 s return timer starts. A player who takes the flag while outside loses it after 3 s. No damage. **Deathmatch, Rabbit (rabbit only), Bounty, Defend and Destroy:** alerts at 1, 2 and 3 s, then 0.05 damage per second from 4 s until the player returns or dies (about 17 s for full-health Light, 25 s Medium, 30 s Heavy). **Hunters:** a player carrying more than one flag loses them every 3 s while outside. | Warn players on crossing, plus a flag-only limit 1 m inside the area that drops a carried flag. No solid walls. |
| Tribes 1 | `MissionCenterPos` rectangle; no flight ceiling | Nothing is solid. Leaving shows "You have left the mission area" and plays an alert at 0, 1.5 and 3 s. **CTF and other objective missions:** if the carrier is still outside at 4.5 s, the flag returns straight to its base. A flag can't be picked up from outside. **Deathmatch:** the same alerts, then 0.05 damage per second from 4 s until the player returns or dies. | Warn players on crossing, plus a flag-only limit 1 m inside the area with a 5 s return timer (4.5 s rounded up). No solid walls. |
| Tribes: Ascend | Four thin `BlockingVolume` walls, usually behind a force field, a cap volume above, `WorldInfo` `KillZ` and `StallZ` | The walls and cap block players. Jetpacks stall above `StallZ`. Falling below `KillZ` kills. | Solid walls on the box with the ceiling at the cap |
| Tribes: Vengeance | One `Movement.BoundaryVolume` per map (a 3D volume, so its top and bottom are the ceiling and floor). It can be switched on or off, and has `DampingScale` and `FrictionScale` for native physics damping at the edge. | There's no warning or grace time. The server kills any player outside the volume at once, destroys any vehicle outside it, and returns any flag lying outside it to base. A carried flag returns when its carrier dies outside. | None yet: the import doesn't read the volume (Tribes3MapTool#17). Add walls or a flag limit by hand in **Boundaries…**. |

The flight ceiling, `KillZ`, and grace-time damage are listed but not
applied. They need the runtime boundary layer (T3BackEndServer#111), as do
flag rules beyond the native drop. Change any preset in **Boundaries…**
before **Create Unreal draft**.

When the intake has one valid mission area, open **Boundaries…** before creating the Unreal draft. Enable **Add solid side walls** to enclose the playable rectangle. Set the optional ceiling in metres above the highest imported terrain and the optional floor in metres below the lowest. The default puts the ceiling 2,000 metres up and leaves the bottom open. With either cap disabled, the side walls still extend to the corresponding default height or depth.

For a custom shape, enable **Use a solid polygon** and select **Draw solid polygon…**. Draw the corners in order. The minimap shows the solid outline in cyan. Any enabled floor and ceiling follow the polygon, including concave corners. If the walls block Pawns, keep the whole flag-only limit inside them.

Use **Walls block** to choose the default `BlockAll` profile, Pawn, Vehicle, or both of those object channels. The selected response applies to the side walls and any caps. When the walls allow Pawns through, flag stands, spawns, and the flag-only limit can sit outside the solid perimeter. These choices use Unreal collision channels. Individual projectile and deployable classes still need their own rules.

Use **Wall appearance** to add a cyan or amber surface to the solid boundary. Set the distance at which the walls begin to appear as you approach them from inside. They stay visible from outside. The saved draft keeps the style and distance, and the surface follows the side walls and any enabled caps without adding collision. The default appearance is invisible.

Under **Player warning and sound**, enable **Warn players on crossing** for a local HUD warning and a short tone. The default uses the solid walls and their active altitude caps, or the source MissionArea with open altitude when walls are off. **Draw a separate outline** sets an independent warning polygon with open altitude. **Set warning height limits** can add a warning ceiling or floor relative to the terrain, or leave altitude open. The minimap preview shows the warning outline in purple. The warning does not add collision or change the flag-only rule. A quick pass through a concave notch still produces the warning.

**Warn** can target players on foot, in vehicles, or both. Vehicle-only warnings sample the occupied vehicle, including when the player character is attached to it. A filtered warning requires the updated client runtime. The default uses the original warning marker format.

The generated actors appear under **TRIBES CTF → Mission perimeter** in the World Outliner, and their collision is part of the cooked level. **Check CTF Markers** and **Build & Playtest** check wall and cap alignment and probe the selected collision responses. Walls that block Pawns also require every flag and spawn to be at least one metre inside. Draft rebuilds reuse the saved boundary settings.

Enter a **Flag-only inset** to set a second boundary inside the mission area. The flag-only boundary also works independently of the solid walls. The minimap previews it in pink. Players can cross this boundary. The game uses its native drop action and places a carried flag at the carrier's last recorded in-bounds position. When there is no valid position for that carrier, the native return brings the flag home. Both original flag stands must be inside the limit.

Either flag outline can have an optional floor below the lowest terrain and a ceiling above the highest terrain. Leave a height blank to keep that direction open. The flag-only height limits use the same drop rule as the sides. Both flag stands must also clear the height limits. Direct Host export and staging check the selected host's verified runtime for support, and the export checklist lists each height to test.

Set **Return flag after time outside** to give a carrier 1–60 seconds to reenter the flag area. The updated client shows a private HUD warning while the timer is active. Reentry clears it, and the flag returns home if the carrier stays outside. Leaving the field blank uses the immediate drop rule. Export and staging check for timer support in the host bundle.

The carrier receives a private message after the host processes a boundary drop or return. When the character is attached to a vehicle pawn, the boundary follows the vehicle's position. A verified switch between the carrier and the vehicle preserves the same player's last in-bounds sample.

For an irregular flag limit, choose **Flag-only polygon → Draw on minimap…**. Click 3–32 corners in order, then choose **Use polygon** and **Apply**. Keep the outline inside the gold mission area and each flag stand at least a metre from its edges. Concave outlines work. Use **Undo corner** or **Clear** to revise the shape.

The draft saves non-colliding TargetPoints for the inset corners or numbered polygon vertices. The offline adapter and the updated Direct Host boundary module read those actors. For a hosted polygon, use a Direct Host build with polygon support in `runtime-fixes/mission_flag_limit.lua`, a registered CTF layout, and a verified map package.

### Preview and export the minimap

After importing a mission with one valid `MissionArea`, choose **Preview minimap**. The preview shows terrain relief, the playable boundary, flags and team spawns. The editor uses the play area's centre and adds a 5% margin on each side of the wider dimension at default scale and zoom. Edit rotation, scale or zoom limits, then choose **Update preview**. Terrain artwork covers the imported tile. Dark areas show holes or space beyond that tile. See the [MissionArea field reference](https://4-0.reference.torque3d.org/scripting/class/classmissionarea/) for the source rectangle format.

Choose **Export for host…**, enter the map ID from the host catalog and select a parent folder. The tool creates a new folder containing three files.

- Place `minimap.png` in the portable map folder `lua/<MapId>/`.
- Copy the `minimap` object from `map-minimap.json` into the map's existing `map.json`.
- Add or replace the `minimap.png` inventory row in the map's existing `manifest.json` using `manifest-minimap.json`. It includes the host-relative path, byte count and SHA-256.

Validate the portable map package before refreshing the host catalog. The bounds, centre and extent are in Unreal centimetres. The artwork uses increasing world X from left to right and increasing world Y from top to bottom. Map settings can be updated without recooking terrain on hosts and clients with per-map minimap support. The preview shows imported marker positions and approximate HUD framing. Changes in Unreal after import are outside this preview.

### Intake packages

The **Source library…** button in the import window lists saved intakes and
the source linked to each Unreal draft. It shows missing original files,
portable intakes without a local source location, and drafts without a saved
link. Select an intake to reopen its coverage review. Original paths and draft
links stay in the private project library and do not enter the intake archive.

The reader runs outside Unreal. For a batch conversion run `python t2_reader.py source.mis output_folder`. For archive input use `--mission missions/Name.mis` and optionally `--terrain path/to/Name.ter`. The GUI keeps intakes in the private project's `Saved/T3MapIntakes` folder. Baked interior lighting uses [the v10 schema](intake-v10.schema.json). The editor also reads [v9 sky artwork](intake-v9.schema.json), [v8 weather](intake-v8.schema.json), [v7 base equipment](intake-v7.schema.json), [v6 model textures](intake-v6.schema.json), [v5 DTS](intake-v5.schema.json), [v4 DIF](intake-v4.schema.json), [v3 daylight and fog](intake-v3.schema.json), [v2](intake-v2.schema.json) and [v1](intake-v1.schema.json). The manifest records source names, file hashes, reader version, conversion profile hash and object counts. `objects.json` records source properties, parent IDs, coverage and converted objective transforms.

**Weather review** lists authored sky and precipitation settings. Double-click a row for the original properties and the checksummed values in `weather.json`, including cloud heights and scroll settings, wind, and precipitation radius. The draft projection shows the active count, radius and estimated fall speed for rain and snow. Unknown datablocks and invalid fields remain visible.

**Sky assets…** accepts a local content folder containing loose textures or
VL2 packs. The reader also searches the selected mission archive. For the
CLI, `--sky-assets` accepts a folder or an individual VL2. The text or
binary DML defines the source slot order. The review shows the textures
that resolved and any unresolved slots. When you create the draft, Unreal
imports the resolved PNGs into its `SourceSky` folder and creates a
private texture review batch for each sky. Confirm the face orientation
before applying the cubemap. [Material list reader](https://raw.githubusercontent.com/GarageGames/Torque3D/development/Engine/source/materials/materialList.cpp)

**Audio assets…** accepts a local Tribes 2 content folder with loose WAVs
or VL2 packs. The CLI option `--audio-assets` also accepts one VL2.
[Version 47](intake-v47.schema.json) retains source emitter positions,
gain, attenuation distances, cone settings, loop counts, and delay ranges
in [mission-audio.json](intake-v47.audio.schema.json). References resolve
against exact content paths, ignoring letter case. Matched PCM WAVs retain
their source bytes. Both Object Review views show missing, unreadable,
ambiguous, or over-budget sounds and source fields that need review.
The draft maps fully specified indoor-and-outdoor, non-directional
emitters to Unreal ambient sound actors. It supports one-shots and
continuous loops with zero repeat delay. Fixed schedules up to 8 MiB
each and 24 MiB combined build into PCM SoundWaves, with silence measured
in samples between plays. These schedules build without an editor
plugin. Variable delay ranges and fixed schedules beyond the PCM
budget use stock MetaSound sources made by the updated
[Community Map Motion editor plugin](Plugins/CommunityMapMotion).
A count of `0` plays once, a positive count adds that many repeats,
and `-1` repeats indefinitely. MetaSound preserves the original mono or
stereo channels and schedules repeats in the audio mixer. The launcher
enables Unreal's built-in MetaSound plugin when opening or cooking a
draft. The cooked map plays without Community Map Motion. Drafts made
with older builds of Community Map Motion still open and play their
Sound Cues.
Spatial emitters use mono sound and spherical linear attenuation between
the authored full-volume and cutoff distances. A WAV used for both
one-shot and continuous playback gets two SoundWave assets. Identical
audio schedules can share a sound asset.

Version 48 also resolves literal `AudioProfile` and `AudioDescription`
declarations in loose sound scripts and supplied VL2 archives. Parent
datablocks provide inherited values when the emitter requests them.
The private intake includes the used declarations and the digest of
each source script. Computed expressions, conflicting references and
unresolved parents stay in Object Review. [Manifest schema](intake-v48.schema.json) · [Audio index schema](intake-v48.audio.schema.json)

Version 49 preserves the primary detail's room tree, exterior-visible
surface flags, exterior portal centers and triangle-to-surface mapping
when an outside-only emitter and decodable DIF room data are present.
The acoustic data stays inside the private interior geometry files.
Room nodes store an oriented plane followed by the front and back
references. Nonnegative references index the room tree, `-1` is solid,
and `-2 - room` identifies an empty leaf. Room zero is exterior.
Portal centers use source-local metres. Each surface run gives the
source surface index, the first primary triangle and the triangle count.
An unresolved DIF still appears in Object Review.
The v47 and v48 audio index schemas still apply according to the audio
index's own format. [Manifest schema](intake-v49.schema.json) ·
[Embedded acoustic schema](intake-v49.dif-acoustics.schema.json)

Version 50 adds original solid-leaf identities, split-plane IDs, and
collision fan boundaries where the reader provides them. Plain geometry
can still carry the version 49 room data. The collision records identify
whether the floor trace reached a source surface or a solid leaf with no
matching face. [Manifest schema](intake-v50.schema.json) ·
[Embedded acoustic schema](intake-v50.dif-acoustics.schema.json)

The private draft preview can use the room data when every imported
DIF instance retains its source collision. It uses the local camera and
applies the resulting gain before starting each outside-only sound.
Direct Host staging includes the saved room and emitter bindings in the
map's private inventory. Connected playback requires the paired Host and
Starter builds that support this layout. An older Starter can still
download the map with its previous manifest format.

Shared layouts omit the private interior geometry, sound index, WAVs,
and source audio references.

When one authored sky resolves, the converter places up to three editable
cloud layers from the DML cloud slots. It also accepts packs that omit the
environment slot and explicitly name the seventh texture as a cloud.
Missing cloud slots leave gaps in the layer order. The draft uses each
layer's height, horizontal wind direction for scalar drift, and source
axis reflection for two-component drift. The Panner currently uses a
60 Hz conversion of the legacy scroll values. The imported texture's
alpha controls coverage, and the canopy fades at the horizon. You can
adjust the materials and layer actors in **TRIBES Environment/Clouds**.
Exact legacy speed parity still needs a comparison in-game.

### Rain and snow

The converter can place editable rain and snow actors under **TRIBES
Environment/Precipitation**. The draft uses the authored count, intensity,
radius and colors. Fall and drift treat the legacy speeds as metres per
second and apply the map's unit scale. Sky wind affects the particles when
the mission enables it. The effect follows the viewing player and uses
no particle collision. Imported static interiors contribute a baked
roof height mask shared by rain and snow. Particles disappear below the
roof while open gaps and the air above it retain their weather. The mask
uses a 125 cm target cell size, up to 512 cells per axis, so large drafts
have coarser roof edges. Rebuild the draft after changing source roof
geometry. Moving roofs and wind deflection around individual roofs remain
outside the baked mask.

Build the [Community Map Materials plugin](Plugins/CommunityMapMaterials)
for the same Windows Unreal Editor build as the authoring project. Use
Unreal's `RunUAT.bat BuildPlugin` with `-Plugin` pointing to
`CommunityMapMaterials.uplugin`, `-Package` pointing to a separate output
folder, and `-NoTargetPlatforms` for the editor build. Copy the packaged
plugin into the authoring project's `Plugins/CommunityMapMaterials`
directory. The launcher detects the editor DLL. An editor without the
plugin keeps the authored weather in the intake and logs an import warning.

### Review equipment and power groups

**Needs review only** keeps generators, sensors, base and sentry turrets, inventory and vehicle stations, and force shields visible. Double-click a row for the source datablock, team, position, full rotation and scale, and power-group records. Group records distinguish explicit `providesPower` declarations from grouping inferred from an ancestor named `…Powered`. They retain the source IDs of the generators in each group. Review missing teams, invalid transforms, and unresolved group assignments before wiring host gameplay.

**Create Unreal draft** places equipment reference points under **TRIBES CTF → Equipment review** for rows with valid transforms. These editor-only points carry the source object IDs and intake hash. Move a generator, radar, base turret or inventory station marker to its intended placement just above the finished support mesh. Select it and choose **Approve Selected Equipment for Direct Host**. Approved markers move to **TRIBES CTF → Approved equipment**. Use **Remove Selected Equipment Approval** to return them to review. Drafts imported before equipment source binding can still export flags and spawns. Import the source into a new draft to use these approvals.

Force shield references use an editor-only opening box. Center it in the doorway, rotate it around Z and size its local Y and Z axes to the clear width and height. Align the bottom with the floor before approving it. Older point-only shield references require a fresh import to create their boxes.

Use **TRIBES Map Editor → Force Shields** to set the selected openings to independent power, restore their imported circuit, or link them to a selected same-team generator from the same intake. Choose **Drop**, **Weaken**, or **Stay Up** for power loss. A weakened shield keeps the lower half of the opening blocked to enemies and leaves the upper half clear. When power returns, it fills the opening again. A custom power-loss policy requires a Direct Host baseline that advertises shield power policy support.

**Build for Direct Host** measures the support of each approved marker again. It records the exact cooked mesh and its actor name in the saved level. Set `direct_host_equipment_adapter` in the launcher config to the full path to `tools/attach_t2_equipment.py` in the source distribution. The selected host baseline needs both grouped equipment runtime modules in its verified inventory. Shield exports also need the grouped shield capability and `lua/powered_shields.lua`. The exporter includes the approved equipment in the same adapter as flags and spawns. Drafts with zero approvals keep the CTF-only flow. Generators, radar sensors and base turrets have native placement evidence. Two shield openings passed Unreal reopen, cook, and host staging verification. Their appearance and player collision still need in-game checks. A headless two-client match verified the generator-linked material changes on a cooked draft.

The optional `CommunityInventoryStationAuthoring` plugin exposes the native
station parent to the stock Unreal authoring workspace. Build it against
the workspace's UE 5.3 engine and install it under the project's `Plugins`
folder. In an imported Tribes 2 draft, choose **TRIBES Map Editor → Create
Inventory Station Blueprint**. This creates or selects the draft's own
Blueprint. If the project has a shared station template, the first use in
the draft makes a copy. Add station visuals in the Blueprint editor, then
approve the imported station markers and their circuit's generators. Build
for Direct Host cooks the Blueprint and seats the stations at the measured
floor height with the markers' yaw. The selected host bundle must include
the verified inventory station runtime in `lua/inventory_stations.lua`.
The installed game supplies the native interaction implementation. Keep the
optional authoring plugin in the Unreal workspace and distribute the cooked
map assets. The exported checklist includes friendly and enemy interaction
and generator outage and recovery.

The terrain reader accepts version 2 and 3 `.ter` files. `terrain.json` stores row-major heights in metres, hole indices, layer names, and Unreal placement in centimetres. Version 3's two authoring blocks are retained as text. The importer uses the authored heightfield and mask PNGs to build the draft. The PNGs carry raw 16-bit heights (divide by 32 for metres), material indices, and individual layer weights.

Intake v2 and later support minimap settings and a verified terrain relief image. Missing, invalid or ambiguous playable areas leave the preview unavailable until a valid mission area is supplied. Verified packages with the same contents can be reused. Intakes are local authoring data.

The boundary row in `objects.json` can store `properties.playable_bounds_cm` as `[[min_x, min_y], [max_x, max_y]]` in Unreal centimetres. The T1, T2, Vengeance and Ascend readers write this rectangle. Walls, the flag limit, player warnings and the gold preview outline use it. `minimap.image_bounds` registers the artwork in the world, so cropping the image preserves the gameplay rectangle. Older intakes without `playable_bounds_cm` use their original image bounds for both roles. Public layouts preserve the numeric rectangle.
