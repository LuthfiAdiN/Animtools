# AnimTools 0.1.0

Animation utilities for Blender 5.0+ (tested on 5.0.1, 5.2 and 5.3). Everything lives in the 3D Viewport sidebar,
tab **Animation > AnimTools**, plus a Playblast button in the Dope Sheet / Timeline / Action Editor header.

## Tools

- **Dynamic Parent**: Create / Switch / Disable a Child Of "dynamic parent" without the pose jumping.
  Disable works on every selected object or bone. *Clear / Bake* removes the constraints, or bakes the
  result into normal keys over the playback range first (into the current action).
- **Parent Rig**: wraps an object in a ROOT + CTRL cube-bone rig (Root/Ctrl size sliders), ready for Dynamic Parent.
- **World Transform**: copy / paste a world-space transform (objects and bones).
- **Rotation Mode**: converts the rotation mode of all selected objects / bones and converts their animation with it,
  so the pose stays the same (optional Bake = a key on every frame). This is the feature Blender 5.3 added as
  *Convert Rotation Mode*; it is here for Blender 5.0 / 5.1 and gives the same result as the 5.3 operator.
- **Constraints**: quick on/off for Child Of constraints with keyframe indicator.
- **Foot Lock**: pin controllers in WORLD space over a frame range (per-axis location / rotation, optional scale),
  constraint-aware, automatic solver, continuous rotations. Works on bones and objects.
- **Pose & Anim Library**: save poses and animations of a rig to a folder (JSON + thumbnail) and apply them to
  any rig of the same kind. Bones are matched across characters automatically (see below).
  Apply with Blend (overshoot / negative too); animations paste as Replace / Insert / Merge,
  also between rigs with different rotation modes. Folders, rename, update, delete, Select Controllers.
- **Hair Physics**: Damped Track chain (Auto or Manual target), optional Shrinkwrap collision.
- **Playblast**: renders the viewport that is looking through the camera (its shading and overlays) to MP4 and opens it
  in the default player. With several 3D viewports, the one in camera view is used, whatever its size.

## Library: using it across characters

Set the library folder once (Library panel > gear icon, or the add-on preferences).
Saved bone names do not have to match the target rig exactly. In *Auto* matching AnimTools finds, per item,
the character prefix / suffix / namespace swap that fits (`CharA_` -> `CharB_`, `ns1:` -> `ns2:`, `_hero` -> `_mob`,
a prefix that exists on one rig only), then falls back to case/separator-insensitive names (`arm_L` = `ARM.L`).
The panel shows "Match: 28/30 bones (CharA_ -> CharB_)" for the selected item. *Exact* disables all guessing;
the From/To fields force a prefix swap. Files use the Mad Library format
(`.pose.json`, `.anim.json`, `.png`), so one library folder works in both add-ons.

## Install

Blender 5.0+: Edit > Preferences > Get Extensions > (v) menu > **Install from Disk...** > pick `animtools-0.4.1.zip`.

## Notes

- Playblast saves `playblast/<blend name>.mp4` next to the .blend (temp folder for unsaved files).
- Foot Lock **Clear** deletes the keys of the enabled channel groups inside the range, including keys that existed before.
- Library thumbnails are rendered from the current 3D viewport (overlays hidden): frame your character first.
- Foot Lock is an independent implementation of the idea used by Mad Animation Tools' foot lock (GPL-3.0-or-later);
  the library follows the file format of Mad Library. The Dynamic Parent workflow follows the behaviour of the
  community "Dynamic Parent" add-on.
- This add-on is inspired by mad art tools, raha tools and animo

## License

GPL-3.0-or-later. See `LICENSE`.
