"""Pose & Animation Library.

Poses and animations of a rig are saved as small JSON files (+ a PNG thumbnail) in a library
folder and can be applied to any rig of the SAME KIND, even when the bone names differ by a
character prefix / suffix / namespace (ProjectA_L_arm_ctrl -> ProjectB_L_arm_ctrl).

Files are compatible with the Mad Library format: <name>.pose.json / <name>.anim.json / <name>.<kind>.png
"""
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import AddonPreferences, Operator
from mathutils import Euler, Quaternion

from .core import _get_channelbag, find_view3d
from .rotation_mode import comps_from, quat_from

POSE_EXT = ".pose.json"
ANIM_EXT = ".anim.json"
EULER_MODES = {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
ROT_SUBS = {'.rotation_quaternion', '.rotation_euler', '.rotation_axis_angle'}
_INVALID = re.compile(r'[<>:"/\\|?*]')

_folder_cache, _item_cache, _cache_valid = [], [], False
_pcoll = None
_names_cache = {}   # (json path, mtime) -> list of stored bone names
_match_cache = {}   # (...) -> (mapping, info)


def invalidate_cache():
    global _cache_valid
    _cache_valid = False
    _names_cache.clear()
    _match_cache.clear()


# ==========================================================================================
# BONE NAME MATCHING  (the part that makes a library usable across characters)
# ==========================================================================================
_SEP = re.compile(r"[:_.\-| ]")


def _tails(name):
    """(tail, head) pairs: the whole name, then every suffix that starts right after a separator."""
    out = [(name, "")]
    for m in _SEP.finditer(name):
        out.append((name[m.end():], name[:m.end()]))
    return out


def _learn_prefix(stored, targets):
    """Most common (stored_prefix, target_prefix) that turns stored names into target names.

    For every stored name we look for its longest tail that exists exactly once among the target
    names; the heads in front of that tail are one vote. The consensus is the prefix swap
    (e.g. "ProjA_" -> "ProjB_" or "ns1:" -> "ns2:"). Returns ((a, b), votes) or None.
    """
    index = {}
    for t in targets:
        for tail, head in _tails(t):
            if tail:
                index.setdefault(tail, []).append(head)
    votes = Counter()
    for s in stored:
        for tail, head_s in _tails(s):
            heads = index.get(tail)
            if heads and len(heads) == 1:
                if head_s != heads[0]:
                    votes[(head_s, heads[0])] += 1
                break
    return votes.most_common(1)[0] if votes else None


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_bones(stored_names, target_names, mode="AUTO", src_prefix="", dst_prefix=""):
    """Map stored bone names to the bones of the target rig.

    Order: exact name > manual prefix swap > learned prefix/suffix swaps (up to 3 rounds)
    > case/separator-insensitive name. Returns (mapping, info).
    """
    target_set = set(target_names)
    free_t = set(target_set)
    mapping = {}
    info = {"exact": 0, "affix": 0, "fuzzy": 0, "pairs": [], "total": len(stored_names)}

    for s in stored_names:
        if s in target_set:
            mapping[s] = s
            free_t.discard(s)
            info["exact"] += 1
    if mode == "EXACT":
        return mapping, info

    def apply_pair(a, b, suffix=False):
        n = 0
        for s in stored_names:
            if s in mapping:
                continue
            if suffix:
                if not s.endswith(a):
                    continue
                t = (s[:len(s) - len(a)] if a else s) + b
            else:
                if not s.startswith(a):
                    continue
                t = b + s[len(a):]
            if t in free_t:
                mapping[s] = t
                free_t.discard(t)
                n += 1
        return n

    if src_prefix or dst_prefix:
        n = apply_pair(src_prefix, dst_prefix)
        if n:
            info["affix"] += n
            info["pairs"].append(("prefix", src_prefix, dst_prefix, n))

    for _ in range(3):
        rest = [s for s in stored_names if s not in mapping]
        if not rest or not free_t:
            break
        cands = []
        pre = _learn_prefix(rest, free_t)
        if pre:
            cands.append((pre[1], False, pre[0]))
        suf = _learn_prefix([s[::-1] for s in rest], [t[::-1] for t in free_t])
        if suf:
            cands.append((suf[1], True, (suf[0][0][::-1], suf[0][1][::-1])))
        if not cands:
            break
        _votes, is_suffix, (a, b) = max(cands, key=lambda c: c[0])
        n = apply_pair(a, b, is_suffix)
        if not n:
            break
        info["affix"] += n
        info["pairs"].append(("suffix" if is_suffix else "prefix", a, b, n))

    tgt_norm = {}
    for t in free_t:
        tgt_norm.setdefault(_norm(t), []).append(t)
    for s in stored_names:
        if s in mapping:
            continue
        c = tgt_norm.get(_norm(s))
        if c and len(c) == 1 and c[0] in free_t:
            mapping[s] = c[0]
            free_t.discard(c[0])
            info["fuzzy"] += 1
    return mapping, info


def describe_match(info):
    """'28/30 bones (CharA_ -> CharB_)': the swap is named only when it explains most matches."""
    got = info["exact"] + info["affix"] + info["fuzzy"]
    text = f"{got}/{info['total']} bones"
    pair = max(info["pairs"], key=lambda p: p[3]) if info["pairs"] else None
    if pair and pair[3] * 2 >= got:
        kind, a, b, _n = pair
        text += f" ({a or 'none'} -> {b or 'none'}{' suffix' if kind == 'suffix' else ''})"
    elif info["fuzzy"] and info["fuzzy"] * 2 >= got:
        text += " (names normalised)"
    return text


# ==========================================================================================
# PREFERENCES + PATHS
# ==========================================================================================
def _on_path_changed(self, context):
    context.window_manager.at_lib_relpath = ""
    context.window_manager.at_lib_selected = ""
    invalidate_cache()


class AT_LibraryPrefs(AddonPreferences):
    bl_idname = __package__

    library_path: StringProperty(
        name="Library Folder", subtype='DIR_PATH', update=_on_path_changed,
        description="Folder where all poses and animations are stored")

    def draw(self, context):
        self.layout.prop(self, "library_path")
        self.layout.label(text="Pick a folder outside your projects so every project shares one library.",
                          icon='INFO')


def get_prefs():
    return bpy.context.preferences.addons[__package__].preferences


def get_root():
    p = get_prefs().library_path
    return os.path.abspath(bpy.path.abspath(p)) if p else ""


def get_current_dir(context):
    root = get_root()
    if not root:
        return ""
    rel = context.window_manager.at_lib_relpath
    cur = os.path.normpath(os.path.join(root, rel)) if rel else root
    if not (cur == root or cur.startswith(root + os.sep)):
        context.window_manager.at_lib_relpath = ""
        return root
    return cur


def scan_current_dir(context):
    """Cached scan of the current folder: (folders, items)."""
    global _folder_cache, _item_cache, _cache_valid
    if _cache_valid:
        return _folder_cache, _item_cache
    cur = get_current_dir(context)
    folders, items = [], []
    if cur and os.path.isdir(cur):
        try:
            for fname in sorted(os.listdir(cur), key=str.lower):
                if fname.startswith("."):
                    continue
                full = os.path.join(cur, fname)
                if os.path.isdir(full):
                    folders.append(fname)
                    continue
                low = fname.lower()
                if low.endswith(POSE_EXT):
                    kind, ext = 'pose', POSE_EXT
                elif low.endswith(ANIM_EXT):
                    kind, ext = 'anim', ANIM_EXT
                else:
                    continue
                base = fname[:-len(ext)]
                items.append({"id": fname, "name": base, "kind": kind, "json_path": full,
                              "png_path": os.path.join(cur, base + "." + kind + ".png"),
                              "preview_dir": os.path.join(cur, "." + base + ".anim.preview")})
        except OSError:
            pass
    _folder_cache, _item_cache, _cache_valid = folders, items, True
    return folders, items


def get_item(context, item_id):
    for it in scan_current_dir(context)[1]:
        if it["id"] == item_id:
            return it
    return None


def load_item_json(item):
    with open(item["json_path"], "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def clean_name(name):
    return _INVALID.sub("", name).strip().rstrip(".")


def thumb_icon_id(item):
    if _pcoll is None or not os.path.isfile(item["png_path"]):
        return 0
    path = item["png_path"]
    key = "%s|%d" % (path, os.path.getmtime(path))
    if key not in _pcoll:
        try:
            _pcoll.load(key, path, 'IMAGE')
        except Exception:
            return 0
    return _pcoll[key].icon_id


def stored_names(item):
    """Bone names stored in an item (cached by file modification time)."""
    key = (item["json_path"], os.path.getmtime(item["json_path"]))
    if key not in _names_cache:
        try:
            _names_cache[key] = list(load_item_json(item).get("bones", {}))
        except (OSError, ValueError):
            _names_cache[key] = []
    return _names_cache[key]


def match_for(context, arm, names, cache_key):
    sc = context.scene
    mode, src, dst = sc.at_lib_match, sc.at_lib_from, sc.at_lib_to
    key = (cache_key, arm.name, len(arm.pose.bones), mode, src, dst)
    if key not in _match_cache:
        _match_cache[key] = match_bones(names, [pb.name for pb in arm.pose.bones], mode, src, dst)
    return _match_cache[key]


# ==========================================================================================
# ARMATURE HELPERS
# ==========================================================================================
def get_armature(context):
    ob = context.active_object
    return ob if ob and ob.type == 'ARMATURE' else None


def selected_bone_names(context, arm):
    if arm.mode != 'POSE':
        return set()
    return {pb.name for pb in (context.selected_pose_bones or [])}


def keyed_bone_names(arm):
    bag = _get_channelbag(arm)
    names = set()
    if bag:
        for fc in bag.fcurves:
            bname, _ = split_bone_path(fc.data_path)
            if bname:
                names.add(bname)
    return names


_BONE_PATH = re.compile(r'^pose\.bones\["(.+?)"\]')


def split_bone_path(data_path):
    m = _BONE_PATH.match(data_path)
    return (m.group(1), data_path[m.end():]) if m else (None, None)


def rotation_as_quat(pb):
    if pb.rotation_mode == 'QUATERNION':
        return pb.rotation_quaternion.copy()
    if pb.rotation_mode == 'AXIS_ANGLE':
        a = pb.rotation_axis_angle
        return Quaternion((a[1], a[2], a[3]), a[0])
    return pb.rotation_euler.to_quaternion()


def set_rotation_from_quat(pb, quat):
    q = Quaternion(quat)
    if pb.rotation_mode == 'QUATERNION':
        pb.rotation_quaternion = q
    elif pb.rotation_mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
    else:
        pb.rotation_euler = q.to_euler(pb.rotation_mode)


def custom_props(pb):
    out = {}
    for key in pb.keys():
        if key.startswith("_"):
            continue
        try:
            val = pb[key]
        except Exception:
            continue
        if isinstance(val, (int, float, bool)):
            out[key] = val
    return out


# ==========================================================================================
# POSE DATA
# ==========================================================================================
def bone_data(pb):
    return {"location": list(pb.location), "rotation": list(rotation_as_quat(pb)),
            "scale": list(pb.scale), "props": custom_props(pb)}


def apply_bone_data(pb, data, autokey, factor=1.0):
    if factor == 1.0:
        pb.location = data["location"]
        set_rotation_from_quat(pb, data["rotation"])
        pb.scale = data["scale"]
    else:
        pb.location = [a + (b - a) * factor for a, b in zip(pb.location, data["location"])]
        q0, q1 = rotation_as_quat(pb), Quaternion(data["rotation"])
        axis, angle = q0.rotation_difference(q1).to_axis_angle()
        set_rotation_from_quat(pb, Quaternion(axis, angle * factor) @ q0)
        pb.scale = [a + (b - a) * factor for a, b in zip(pb.scale, data["scale"])]
    for key, val in data.get("props", {}).items():
        if key in pb.keys():
            try:
                cur = pb[key]
                if isinstance(cur, bool):
                    new = bool(val) if factor > 0.5 else cur
                elif isinstance(cur, int):
                    new = int(round(cur + (float(val) - cur) * factor))
                else:
                    new = float(cur) + (float(val) - float(cur)) * factor
                pb[key] = new
            except Exception:
                pass
    if autokey:
        pb.keyframe_insert("location")
        rm = pb.rotation_mode
        pb.keyframe_insert("rotation_quaternion" if rm == 'QUATERNION'
                           else "rotation_axis_angle" if rm == 'AXIS_ANGLE' else "rotation_euler")
        pb.keyframe_insert("scale")
        for key in data.get("props", {}):
            if key in pb.keys():
                try:
                    pb.keyframe_insert(f'["{key}"]')
                except Exception:
                    pass


def collect_pose_data(context, arm):
    """Selected bones > keyed bones > all bones."""
    names = selected_bone_names(context, arm) or keyed_bone_names(arm) or {pb.name for pb in arm.pose.bones}
    return {n: bone_data(arm.pose.bones[n]) for n in names if n in arm.pose.bones}


# ==========================================================================================
# ANIMATION DATA
# key = [frame (relative to the first key), value, hl_dx, hl_dy, hr_dx, hr_dy, interp, hl_type, hr_type, easing]
# ==========================================================================================
def serialize_key(kp, origin):
    v = kp.co.y
    return [kp.co.x - origin, v, kp.handle_left.x - kp.co.x, kp.handle_left.y - v,
            kp.handle_right.x - kp.co.x, kp.handle_right.y - v,
            kp.interpolation, kp.handle_left_type, kp.handle_right_type, kp.easing]


def collect_anim_data(context, arm, frange=None):
    """(bones, span, origin, error)."""
    bag = _get_channelbag(arm)
    if bag is None:
        return None, 0, 0, "Rig has no animation to save"
    sel = selected_bone_names(context, arm)
    per_bone = {}
    for fc in bag.fcurves:
        bname, sub = split_bone_path(fc.data_path)
        if not bname or (sel and bname not in sel):
            continue
        if frange:
            kps = [kp for kp in fc.keyframe_points if frange[0] - 1e-4 <= kp.co.x <= frange[1] + 1e-4]
        else:
            kps = list(fc.keyframe_points)
        if kps:
            per_bone.setdefault(bname, []).append((sub, fc, kps))
    if not per_bone:
        if frange:
            return None, 0, 0, "No keyframes found in that frame range"
        return None, 0, 0, "Selected bones have no keyframes" if sel else "No bone keyframes found on this rig"

    if frange:
        origin, span = float(frange[0]), float(frange[1] - frange[0])
    else:
        origin = min(kps[0].co.x for ch in per_bone.values() for _, _, kps in ch)
        span = max(kps[-1].co.x for ch in per_bone.values() for _, _, kps in ch) - origin

    bones = {}
    for bname, chans in per_bone.items():
        pb = arm.pose.bones.get(bname)
        bones[bname] = {
            "rotation_mode": pb.rotation_mode if pb else 'QUATERNION',
            "channels": [{"path": sub, "index": fc.array_index,
                          "keys": [serialize_key(kp, origin) for kp in kps]} for sub, fc, kps in chans],
        }
    return bones, span, origin, None


def action_key_range(arm):
    bag = _get_channelbag(arm)
    lo = hi = None
    if bag:
        for fc in bag.fcurves:
            if split_bone_path(fc.data_path)[0] and fc.keyframe_points:
                a, b = fc.keyframe_points[0].co.x, fc.keyframe_points[-1].co.x
                lo = a if lo is None else min(lo, a)
                hi = b if hi is None else max(hi, b)
    if lo is None:
        sc = bpy.context.scene
        return sc.frame_start, sc.frame_end
    return int(lo), int(hi)


# --- applying ---------------------------------------------------------------------------
def _find_fc(arm, full, index):
    bag = _get_channelbag(arm)
    return bag.fcurves.find(full, index=index) if bag else None


def ensure_fcurve(arm, pb, sub, index, frame):
    """(fcurve, existed). A missing channel is created with a throw-away key (removed by the caller)."""
    full = f'pose.bones["{pb.name}"]{sub}'
    fc = _find_fc(arm, full, index)
    if fc is not None:
        return fc, True
    try:
        if sub.startswith('.'):
            pb.keyframe_insert(sub[1:], index=index, frame=frame)
        else:
            pb.keyframe_insert(sub, frame=frame)
    except Exception:
        return None, False
    return _find_fc(arm, full, index), False


def current_channel_value(pb, sub, index):
    try:
        if sub.startswith('.'):
            val = pb.path_resolve(sub[1:])
            try:
                return float(val[index])
            except TypeError:
                return float(val)
        return float(pb[sub[2:-2]])
    except Exception:
        return 0.0


def eval_stored(keys, f):
    """Linear interpolation of stored keys (used when converting rotation modes)."""
    if f <= keys[0][0]:
        return keys[0][1]
    if f >= keys[-1][0]:
        return keys[-1][1]
    for i in range(1, len(keys)):
        if keys[i][0] >= f:
            f0, v0, f1, v1 = keys[i - 1][0], keys[i - 1][1], keys[i][0], keys[i][1]
            return v1 if f1 <= f0 else v0 + (v1 - v0) * (f - f0) / (f1 - f0)
    return keys[-1][1]


_ROT_DEFAULTS = {'.rotation_quaternion': [1.0, 0.0, 0.0, 0.0], '.rotation_euler': [0.0, 0.0, 0.0],
                 '.rotation_axis_angle': [0.0, 0.0, 1.0, 0.0]}


def rot_sub_for_mode(mode):
    return '.rotation_quaternion' if mode == 'QUATERNION' else \
        '.rotation_axis_angle' if mode == 'AXIS_ANGLE' else '.rotation_euler'


def resample_rotation(channels, src_mode, tgt_mode):
    """Rotation channels of another rotation mode, resampled on the union of their key frames."""
    defaults = _ROT_DEFAULTS[rot_sub_for_mode(src_mode)]
    by_index = {ch["index"]: ch["keys"] for ch in channels}
    frames = sorted({k[0] for keys in by_index.values() for k in keys})
    if not frames:
        return []
    tgt_sub = rot_sub_for_mode(tgt_mode)
    n_tgt = len(_ROT_DEFAULTS[tgt_sub])
    out, prev_q, prev_c = [[] for _ in range(n_tgt)], None, None
    for f in frames:
        vals = [eval_stored(by_index[i], f) if by_index.get(i) else defaults[i] for i in range(len(defaults))]
        q = quat_from(src_mode, vals)
        if prev_q is not None and prev_q.dot(q) < 0.0:
            q.negate()
        prev_q = q
        comps = comps_from(tgt_mode, q, prev_c)   # continuous Euler / axis-angle, no 360 degree flips
        prev_c = comps
        for i in range(n_tgt):
            out[i].append([f, comps[i]])
    return [{"path": tgt_sub, "index": i, "keys": out[i]} for i in range(n_tgt)]


def apply_channel(arm, pb, sub, index, keys, start, span, mode, factor):
    """Write one channel's keys at `start`. Returns the number of keys written."""
    if not keys:
        return 0
    fc, existed = ensure_fcurve(arm, pb, sub, index, start + keys[0][0])
    if fc is None:
        return 0
    bases = None
    if factor != 1.0:  # blend base values: read BEFORE the curve is modified
        bases = ([fc.evaluate(start + k[0]) for k in keys] if existed
                 else [current_channel_value(pb, sub, index)] * len(keys))
    kps = fc.keyframe_points
    if not existed:
        for i in reversed(range(len(kps))):   # drop the throw-away key
            kps.remove(kps[i])
    elif mode == 'REPLACE':
        end = start + span
        for i in reversed(range(len(kps))):   # fresh point each time: removal re-allocates the array
            if start - 1e-4 <= kps[i].co.x <= end + 1e-4:
                kps.remove(kps[i])
    elif mode == 'INSERT':
        shift = span + 1.0
        for kp in kps:
            if kp.co.x >= start - 1e-4:
                kp.co.x += shift
                kp.handle_left.x += shift
                kp.handle_right.x += shift
    count = 0
    for i, k in enumerate(keys):
        f, v = start + k[0], k[1]
        if bases is not None:
            v = bases[i] + (v - bases[i]) * factor
        try:
            kp = kps.insert(f, v, options={'FAST'})
        except Exception:
            continue
        if len(k) >= 10:
            hs = factor if bases is not None else 1.0
            try:
                kp.interpolation, kp.easing = k[6], k[9]
                kp.handle_left_type, kp.handle_right_type = k[7], k[8]
                kp.handle_left = (f + k[2], v + k[3] * hs)
                kp.handle_right = (f + k[4], v + k[5] * hs)
            except Exception:
                pass
        count += 1
    try:
        fc.update()
    except Exception:
        pass
    return count


# ==========================================================================================
# THUMBNAILS
# ==========================================================================================
def _find_view3d(context):
    """(window, area, region, space): the viewport the operator was started from, else a camera / the biggest one."""
    return find_view3d(context, "CONTEXT")


def capture_thumbnail(context, filepath, size=512, frame=None):
    """OpenGL-render the 3D viewport (overlays and gizmos hidden) into a PNG."""
    found = _find_view3d(context)
    if found is None:
        return False
    win, area, region, space = found
    scene = context.scene
    r = scene.render
    ims = r.image_settings
    saved = (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath,
             ims.media_type, ims.file_format, ims.color_mode)
    overlay, gizmo, frame0 = space.overlay.show_overlays, space.show_gizmo, scene.frame_current
    ok = False
    try:
        r.resolution_x = r.resolution_y = size
        r.resolution_percentage = 100
        ims.media_type = 'IMAGE'
        ims.file_format = 'PNG'
        ims.color_mode = 'RGBA'
        space.overlay.show_overlays = False
        space.show_gizmo = False
        if frame is not None:
            scene.frame_set(int(round(frame)))
        r.filepath = filepath
        with context.temp_override(window=win, area=area, region=region, space_data=space):
            bpy.ops.render.opengl(write_still=True, view_context=True)
        ok = os.path.isfile(filepath)
    except Exception:
        ok = False
    finally:
        (r.resolution_x, r.resolution_y, r.resolution_percentage, r.filepath) = saved[:4]
        ims.media_type, ims.file_format, ims.color_mode = saved[4:]
        space.overlay.show_overlays, space.show_gizmo = overlay, gizmo
        if frame is not None:
            scene.frame_set(frame0)
    return ok


# ==========================================================================================
# OPERATORS
# ==========================================================================================
def _need_dir(op, context):
    cur = get_current_dir(context)
    if not cur or not os.path.isdir(cur):
        op.report({'ERROR'}, "Library folder not available")
        return None
    return cur


class AT_OT_LibGoto(Operator):
    bl_idname = "animtools.lib_goto"
    bl_label = "Go To"
    bl_description = "Open this folder"
    bl_options = {'INTERNAL'}
    relpath: StringProperty()

    def execute(self, context):
        context.window_manager.at_lib_relpath = self.relpath
        context.window_manager.at_lib_selected = ""
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibBack(Operator):
    bl_idname = "animtools.lib_back"
    bl_label = "Back"
    bl_description = "Go to the parent folder"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.at_lib_relpath = os.path.dirname(wm.at_lib_relpath)
        wm.at_lib_selected = ""
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibRefresh(Operator):
    bl_idname = "animtools.lib_refresh"
    bl_label = "Refresh"
    bl_description = "Re-scan the library folder"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibOpenFolder(Operator):
    bl_idname = "animtools.lib_open_folder"
    bl_label = "Open in File Browser"
    bl_description = "Open the current library folder in the system file browser"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        cur = _need_dir(self, context)
        if not cur:
            return {'CANCELLED'}
        if sys.platform.startswith("win"):
            os.startfile(cur)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", cur])
        else:
            subprocess.Popen(["xdg-open", cur])
        return {'FINISHED'}


class AT_OT_LibCreateRoot(Operator):
    bl_idname = "animtools.lib_create_root"
    bl_label = "Create Library Folder"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        root = get_root()
        try:
            os.makedirs(root, exist_ok=True)
        except OSError as e:
            self.report({'ERROR'}, f"Could not create folder: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibNewFolder(Operator):
    bl_idname = "animtools.lib_new_folder"
    bl_label = "New Folder"
    bl_description = "Create a folder here (e.g. Walk, Run, Poses)"
    bl_options = {'INTERNAL'}
    name: StringProperty(name="Name", default="New Folder")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=240)

    def execute(self, context):
        cur, name = _need_dir(self, context), clean_name(self.name)
        if not cur or not name:
            return {'CANCELLED'}
        try:
            os.makedirs(os.path.join(cur, name), exist_ok=False)
        except OSError as e:
            self.report({'ERROR'}, f"Could not create folder: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibRenameFolder(Operator):
    bl_idname = "animtools.lib_rename_folder"
    bl_label = "Rename Folder"
    bl_options = {'INTERNAL'}
    old_name: StringProperty()
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        self.name = self.old_name
        return context.window_manager.invoke_props_dialog(self, width=240)

    def execute(self, context):
        cur, name = _need_dir(self, context), clean_name(self.name)
        if not cur or not name or name == self.old_name:
            return {'CANCELLED'}
        try:
            os.rename(os.path.join(cur, self.old_name), os.path.join(cur, name))
        except OSError as e:
            self.report({'ERROR'}, f"Could not rename: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibDeleteFolder(Operator):
    bl_idname = "animtools.lib_delete_folder"
    bl_label = "Delete Folder"
    bl_description = "Delete this folder and EVERYTHING inside it"
    bl_options = {'INTERNAL'}
    name: StringProperty()

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, message=f"Delete '{self.name}' and everything inside it?")

    def execute(self, context):
        cur = _need_dir(self, context)
        if not cur or not self.name or clean_name(self.name) != self.name:
            return {'CANCELLED'}
        try:
            shutil.rmtree(os.path.join(cur, self.name))
        except OSError as e:
            self.report({'ERROR'}, f"Could not delete: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        return {'FINISHED'}


class AT_OT_LibSavePose(Operator):
    bl_idname = "animtools.lib_save_pose"
    bl_label = "Save Pose"
    bl_description = "Save the current pose of the active rig (selected bones, else keyed bones, else all)"
    bl_options = {'INTERNAL'}
    name: StringProperty(name="Pose Name", default="Pose")

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def invoke(self, context, event):
        self.name = "Pose"
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        self.layout.prop(self, "name")
        arm = get_armature(context)
        sel = selected_bone_names(context, arm)
        n = len(sel) or len(keyed_bone_names(arm)) or len(arm.pose.bones)
        what = "selected" if sel else ("keyed" if keyed_bone_names(arm) else "all")
        self.layout.label(text=f"Saving {what} bones ({n})", icon='BONE_DATA')

    def execute(self, context):
        name, cur = clean_name(self.name), _need_dir(self, context)
        if not name:
            self.report({'ERROR'}, "Invalid pose name")
            return {'CANCELLED'}
        if not cur:
            return {'CANCELLED'}
        json_path = os.path.join(cur, name + POSE_EXT)
        if os.path.exists(json_path):
            self.report({'ERROR'}, f"'{name}' already exists - update it or choose another name")
            return {'CANCELLED'}
        arm = get_armature(context)
        bones = collect_pose_data(context, arm)
        if not bones:
            self.report({'ERROR'}, "No bones to save")
            return {'CANCELLED'}
        try:
            _write_json(json_path, {"type": "pose", "version": 1, "rig": arm.name, "bones": bones})
        except OSError as e:
            self.report({'ERROR'}, f"Could not write file: {e}")
            return {'CANCELLED'}
        if not capture_thumbnail(context, os.path.join(cur, name + ".pose.png")):
            self.report({'WARNING'}, "Pose saved, but thumbnail capture failed")
        invalidate_cache()
        context.window_manager.at_lib_selected = name + POSE_EXT
        self.report({'INFO'}, f"Pose '{name}' saved ({len(bones)} bones)")
        return {'FINISHED'}


class _RangeProps:
    use_range: BoolProperty(name="Frame Range", default=False,
                            description="Save only the keyframes inside this frame range")
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=250)

    def _draw_range(self, layout):
        layout.prop(self, "use_range")
        row = layout.row(align=True)
        row.enabled = self.use_range
        row.prop(self, "frame_start")
        row.prop(self, "frame_end")

    def _frange(self):
        if not self.use_range:
            return None
        return (self.frame_start, self.frame_end)


class AT_OT_LibSaveAnim(_RangeProps, Operator):
    bl_idname = "animtools.lib_save_anim"
    bl_label = "Save Animation"
    bl_description = "Save the animation of the active rig (selected bones, else all keyed bones)"
    bl_options = {'INTERNAL'}
    name: StringProperty(name="Animation Name", default="Animation")

    @classmethod
    def poll(cls, context):
        arm = get_armature(context)
        return arm is not None and _get_channelbag(arm) is not None

    def invoke(self, context, event):
        self.name = "Animation"
        self.frame_start, self.frame_end = action_key_range(get_armature(context))
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context):
        self.layout.prop(self, "name")
        self._draw_range(self.layout)
        arm = get_armature(context)
        sel, keyed = selected_bone_names(context, arm), keyed_bone_names(arm)
        text = f"Saving {len(sel & keyed)} selected keyed bone(s)" if sel else f"Saving all keyed bones ({len(keyed)})"
        self.layout.label(text=text, icon='BONE_DATA')

    def execute(self, context):
        name, cur = clean_name(self.name), _need_dir(self, context)
        if not name:
            self.report({'ERROR'}, "Invalid animation name")
            return {'CANCELLED'}
        if not cur:
            return {'CANCELLED'}
        json_path = os.path.join(cur, name + ANIM_EXT)
        if os.path.exists(json_path):
            self.report({'ERROR'}, f"'{name}' already exists - update it or choose another name")
            return {'CANCELLED'}
        if self.use_range and self.frame_end <= self.frame_start:
            self.report({'ERROR'}, "End frame must be after start frame")
            return {'CANCELLED'}
        arm = get_armature(context)
        bones, span, origin, err = collect_anim_data(context, arm, self._frange())
        if err:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}
        try:
            _write_json(json_path, {"type": "anim", "version": 1, "rig": arm.name, "span": span, "bones": bones})
        except OSError as e:
            self.report({'ERROR'}, f"Could not write file: {e}")
            return {'CANCELLED'}
        if not capture_thumbnail(context, os.path.join(cur, name + ".anim.png"), frame=origin):
            self.report({'WARNING'}, "Animation saved, but thumbnail capture failed")
        invalidate_cache()
        context.window_manager.at_lib_selected = name + ANIM_EXT
        self.report({'INFO'}, f"Animation '{name}' saved ({len(bones)} bones, {span:.0f} frames)")
        return {'FINISHED'}


class AT_OT_LibUpdate(_RangeProps, Operator):
    bl_idname = "animtools.lib_update"
    bl_label = "Update"
    bl_description = "Overwrite this item (and its thumbnail) with the active rig's current pose/animation"
    bl_options = {'INTERNAL'}
    item_id: StringProperty()

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def invoke(self, context, event):
        self.frame_start, self.frame_end = action_key_range(get_armature(context))
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        item = get_item(context, self.item_id)
        self.layout.label(text=f"Overwrite '{item['name'] if item else self.item_id}'?", icon='ERROR')
        if item and item["kind"] == 'anim':
            self._draw_range(self.layout)

    def execute(self, context):
        item, arm = get_item(context, self.item_id), get_armature(context)
        if item is None:
            self.report({'ERROR'}, "Item not found")
            return {'CANCELLED'}
        frame = None
        if item["kind"] == 'anim':
            bones, span, origin, err = collect_anim_data(context, arm, self._frange())
            if err:
                self.report({'ERROR'}, err)
                return {'CANCELLED'}
            data, frame = {"type": "anim", "version": 1, "rig": arm.name, "span": span, "bones": bones}, origin
        else:
            bones = collect_pose_data(context, arm)
            if not bones:
                self.report({'ERROR'}, "No bones to save")
                return {'CANCELLED'}
            data = {"type": "pose", "version": 1, "rig": arm.name, "bones": bones}
        try:
            _write_json(item["json_path"], data)
        except OSError as e:
            self.report({'ERROR'}, f"Could not write file: {e}")
            return {'CANCELLED'}
        if not capture_thumbnail(context, item["png_path"], frame=frame):
            self.report({'WARNING'}, "Updated, but thumbnail capture failed")
        invalidate_cache()
        self.report({'INFO'}, f"'{item['name']}' updated ({len(bones)} bones)")
        return {'FINISHED'}


def _prepare_apply(op, context, item_id):
    """(item, data, arm, [(pose bone, stored data)], info) or None after reporting why not."""
    item, arm = get_item(context, item_id), get_armature(context)
    if item is None:
        op.report({'ERROR'}, "Library item not found")
        return None
    try:
        data = load_item_json(item)
    except (OSError, ValueError) as e:
        op.report({'ERROR'}, f"Could not read item: {e}")
        return None
    stored = data.get("bones", {})
    mapping, info = match_for(context, arm, list(stored), (item["json_path"], os.path.getmtime(item["json_path"])))
    sel = selected_bone_names(context, arm)
    targets = [(arm.pose.bones[t], stored[s]) for s, t in mapping.items() if not sel or t in sel]
    if not targets:
        op.report({'ERROR'}, "None of the selected bones are in this item" if sel
                  else "No matching bones - is this the same kind of rig?")
        return None
    return item, data, arm, targets, info


def _report_match(op, info, what):
    if info["pairs"] or info["fuzzy"]:
        op.report({'INFO'}, f"{what}: {describe_match(info)}")
    missing = info["total"] - (info["exact"] + info["affix"] + info["fuzzy"])
    if missing > 0:
        op.report({'WARNING'}, f"{what}: {missing} stored bone(s) have no match on this rig")


class AT_OT_LibApplyPose(Operator):
    bl_idname = "animtools.lib_apply_pose"
    bl_label = "Apply Pose"
    bl_description = "Apply this pose to the active rig (selected bones only, if any). Tweak Blend in Adjust Last Operation"
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty()
    blend: FloatProperty(name="Blend", default=1.0, soft_min=-1.5, soft_max=1.5, min=-3.0, max=3.0,
                         step=2, precision=2,
                         description="1.0 = full pose, 0 = no change, above 1.0 overshoots, negative pushes away")

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def draw(self, context):
        self.layout.prop(self, "blend", slider=True)

    def execute(self, context):
        prep = _prepare_apply(self, context, self.item_id)
        if prep is None:
            return {'CANCELLED'}
        _item, _data, _arm, targets, info = prep
        autokey = context.scene.tool_settings.use_keyframe_insert_auto
        for pb, bdata in targets:
            apply_bone_data(pb, bdata, autokey, self.blend)
        self.report({'INFO'}, f"Pose applied ({len(targets)} bones)")
        _report_match(self, info, "Pose")
        return {'FINISHED'}


ANIM_MODES = (
    ('REPLACE', "Replace", "Clear existing keys in the pasted range on affected channels, then paste"),
    ('INSERT', "Insert", "Push existing keys after the current frame forward to make room, then paste"),
    ('MERGE', "Merge", "Paste over existing keys; only exact-frame collisions are replaced"),
)


class AT_OT_LibApplyAnim(Operator):
    bl_idname = "animtools.lib_apply_anim"
    bl_label = "Apply Animation"
    bl_description = "Paste this animation on the active rig from the current frame (selected bones only, if any)"
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty()
    mode: EnumProperty(name="Mode", items=ANIM_MODES, default='REPLACE')
    blend: FloatProperty(name="Blend", default=1.0, soft_min=-1.5, soft_max=1.5, min=-3.0, max=3.0,
                         step=2, precision=2,
                         description="1.0 = paste exactly, 0 = no change, above 1.0 overshoots, negative pushes away")

    @classmethod
    def poll(cls, context):
        return get_armature(context) is not None

    def draw(self, context):
        self.layout.row(align=True).prop(self, "mode", expand=True)
        self.layout.prop(self, "blend", slider=True)

    def execute(self, context):
        prep = _prepare_apply(self, context, self.item_id)
        if prep is None:
            return {'CANCELLED'}
        _item, data, arm, targets, info = prep
        span = float(data.get("span", 0.0))
        start = float(context.scene.frame_current)
        total = 0
        for pb, bdata in targets:
            chans = bdata.get("channels", [])
            src_mode = bdata.get("rotation_mode", pb.rotation_mode)
            direct = [c for c in chans if c["path"] not in ROT_SUBS]
            rots = [c for c in chans if c["path"] in ROT_SUBS]
            if rots:
                direct += rots if src_mode == pb.rotation_mode else resample_rotation(rots, src_mode, pb.rotation_mode)
            for ch in direct:
                total += apply_channel(arm, pb, ch["path"], ch.get("index", 0), ch["keys"],
                                       start, span, self.mode, self.blend)
        if total == 0:
            self.report({'ERROR'}, "No keyframes could be written")
            return {'CANCELLED'}
        context.scene.frame_set(context.scene.frame_current)
        self.report({'INFO'}, f"Animation pasted ({len(targets)} bones, {total} keys)")
        _report_match(self, info, "Animation")
        return {'FINISHED'}


class AT_OT_LibSelectControllers(Operator):
    bl_idname = "animtools.lib_select_controllers"
    bl_label = "Select Controllers"
    bl_description = "Select the bones this item was saved from (matched to this rig)"
    bl_options = {'REGISTER', 'UNDO'}
    item_id: StringProperty()

    @classmethod
    def poll(cls, context):
        arm = get_armature(context)
        return arm is not None and arm.mode == 'POSE'

    def execute(self, context):
        item, arm = get_item(context, self.item_id), get_armature(context)
        if item is None:
            return {'CANCELLED'}
        mapping, _info = match_for(context, arm, stored_names(item),
                                   (item["json_path"], os.path.getmtime(item["json_path"])))
        wanted = set(mapping.values())
        if not wanted:
            self.report({'ERROR'}, "No matching bones on this rig")
            return {'CANCELLED'}
        last = None
        for pb in arm.pose.bones:
            pb.select = pb.name in wanted
            if pb.name in wanted:
                last = pb
        if last is not None:
            arm.data.bones.active = last.bone
        self.report({'INFO'}, f"Selected {len(wanted)} controller(s)")
        return {'FINISHED'}


class AT_OT_LibSelectItem(Operator):
    bl_idname = "animtools.lib_select_item"
    bl_label = "Select Item"
    bl_description = "Select this library item (right-click for more actions)"
    bl_options = {'INTERNAL'}
    item_id: StringProperty()

    def execute(self, context):
        wm = context.window_manager
        wm.at_lib_selected = "" if wm.at_lib_selected == self.item_id else self.item_id
        return {'FINISHED'}


class AT_OT_LibRenameItem(Operator):
    bl_idname = "animtools.lib_rename_item"
    bl_label = "Rename"
    bl_options = {'INTERNAL'}
    item_id: StringProperty()
    name: StringProperty(name="Name")

    def invoke(self, context, event):
        item = get_item(context, self.item_id)
        self.name = item["name"] if item else ""
        return context.window_manager.invoke_props_dialog(self, width=260)

    def execute(self, context):
        item, name = get_item(context, self.item_id), clean_name(self.name)
        if item is None or not name or name == item["name"]:
            return {'CANCELLED'}
        cur = os.path.dirname(item["json_path"])
        ext = POSE_EXT if item["kind"] == 'pose' else ANIM_EXT
        new_json = os.path.join(cur, name + ext)
        if os.path.exists(new_json):
            self.report({'ERROR'}, f"'{name}' already exists")
            return {'CANCELLED'}
        try:
            os.rename(item["json_path"], new_json)
            if os.path.isfile(item["png_path"]):
                os.rename(item["png_path"], os.path.join(cur, name + "." + item["kind"] + ".png"))
        except OSError as e:
            self.report({'ERROR'}, f"Could not rename: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        context.window_manager.at_lib_selected = name + ext
        return {'FINISHED'}


class AT_OT_LibDeleteItem(Operator):
    bl_idname = "animtools.lib_delete_item"
    bl_label = "Delete Item"
    bl_description = "Delete this pose/animation from the library"
    bl_options = {'INTERNAL'}
    item_id: StringProperty()

    def invoke(self, context, event):
        item = get_item(context, self.item_id)
        return context.window_manager.invoke_confirm(
            self, event, message=f"Delete '{item['name'] if item else self.item_id}' from the library?")

    def execute(self, context):
        item = get_item(context, self.item_id)
        if item is None:
            return {'CANCELLED'}
        try:
            os.remove(item["json_path"])
            if os.path.isfile(item["png_path"]):
                os.remove(item["png_path"])
            if os.path.isdir(item["preview_dir"]):
                shutil.rmtree(item["preview_dir"])
        except OSError as e:
            self.report({'ERROR'}, f"Could not delete: {e}")
            return {'CANCELLED'}
        invalidate_cache()
        context.window_manager.at_lib_selected = ""
        return {'FINISHED'}


# ==========================================================================================
# UI (drawn by the AnimTools sub-panel in ui.py)
# ==========================================================================================
def _kind_icon(kind):
    return 'POSE_HLT' if kind == 'pose' else 'RENDER_ANIMATION'


def draw_panel(layout, context):
    prefs, root, wm, sc = get_prefs(), get_root(), context.window_manager, context.scene
    if not root:
        col = layout.column(align=True)
        col.label(text="Set your library folder:", icon='INFO')
        col.prop(prefs, "library_path", text="")
        return
    if not os.path.isdir(root):
        col = layout.column(align=True)
        col.label(text="Library folder does not exist:", icon='ERROR')
        col.prop(prefs, "library_path", text="")
        col.operator("animtools.lib_create_root", icon='NEWFOLDER')
        return

    rel = wm.at_lib_relpath
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.enabled = bool(rel)
    sub.operator("animtools.lib_back", text="", icon='BACK')
    row.operator("animtools.lib_new_folder", text="", icon='NEWFOLDER')
    row.operator("animtools.lib_refresh", text="", icon='FILE_REFRESH')
    row.operator("animtools.lib_open_folder", text="", icon='FILEBROWSER')
    row.prop(wm, "at_lib_show_settings", text="", icon='PREFERENCES')
    if wm.at_lib_show_settings:
        box = layout.box()
        box.prop(prefs, "library_path", text="Path")
        box.label(text="Rig matching", icon='LINKED')
        box.row().prop(sc, "at_lib_match", expand=True)
        if sc.at_lib_match == 'AUTO':
            r = box.row(align=True)
            r.prop(sc, "at_lib_from", text="From")
            r.prop(sc, "at_lib_to", text="To")
            box.label(text="Prefix swap is detected automatically; From/To only to force it.")

    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator("animtools.lib_save_pose", text="Pose", icon='POSE_HLT')
    row.operator("animtools.lib_save_anim", text="Anim", icon='RENDER_ANIMATION')

    crumb = layout.row(align=True)
    op = crumb.operator("animtools.lib_goto", text="Library", icon='HOME', emboss=False)
    op.relpath = ""
    if rel:
        accum = ""
        for part in rel.split(os.sep):
            accum = os.path.join(accum, part) if accum else part
            crumb.label(text="/")
            op = crumb.operator("animtools.lib_goto", text=part, emboss=False)
            op.relpath = accum

    folders, items = scan_current_dir(context)
    if folders:
        col = layout.column(align=True)
        for name in folders:
            frow = col.row(align=True)
            op = frow.operator("animtools.lib_goto", text=name, icon='FILE_FOLDER')
            op.relpath = os.path.join(rel, name) if rel else name
            op = frow.operator("animtools.lib_rename_folder", text="", icon='OUTLINER_DATA_GP_LAYER')
            op.old_name = name
            op = frow.operator("animtools.lib_delete_folder", text="", icon='TRASH')
            op.name = name

    if items:
        layout.separator(factor=0.3)
        flow = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=False)
        for it in items:
            cell = flow.column(align=True)
            box = cell.box()
            icon_id = thumb_icon_id(it)
            if icon_id:
                box.template_icon(icon_value=icon_id, scale=5.0)
            else:
                ph = box.column()
                ph.scale_y = 5.0
                ph.label(text="", icon=_kind_icon(it["kind"]))
            op = cell.operator("animtools.lib_select_item", text=it["name"], icon=_kind_icon(it["kind"]),
                               depress=(wm.at_lib_selected == it["id"]))
            op.item_id = it["id"]
    if not folders and not items:
        box = layout.box()
        box.label(text="Empty folder", icon='FILE_FOLDER')
        box.label(text="Save a pose/anim or create a folder.")

    item = get_item(context, wm.at_lib_selected) if wm.at_lib_selected else None
    if item is None:
        return
    arm = get_armature(context)
    is_anim = item["kind"] == 'anim'
    box = layout.box()
    hdr = box.row(align=True)
    hdr.label(text=item["name"], icon=_kind_icon(item["kind"]))
    if arm is not None:
        op = hdr.operator("animtools.lib_update", text="", icon='FILE_REFRESH')
        op.item_id = item["id"]
    op = hdr.operator("animtools.lib_rename_item", text="", icon='OUTLINER_DATA_GP_LAYER')
    op.item_id = item["id"]
    op = hdr.operator("animtools.lib_delete_item", text="", icon='TRASH')
    op.item_id = item["id"]

    col = box.column(align=True)
    if arm is None:
        col.label(text="Select a rig to apply", icon='INFO')
        return
    names = stored_names(item)
    mapping, info = match_for(context, arm, names, (item["json_path"], os.path.getmtime(item["json_path"])))
    got = len(mapping)
    mrow = col.row()
    mrow.alert = got == 0
    mrow.label(text="Match: " + describe_match(info), icon='LINKED' if got else 'ERROR')
    col.separator(factor=0.3)
    if is_anim:
        col.row(align=True).prop(wm, "at_lib_anim_mode", expand=True)
        col.separator(factor=0.3)
    arow = col.column(align=True)
    arow.scale_y = 1.2
    if is_anim:
        op = arow.operator("animtools.lib_apply_anim", icon='CHECKMARK')
        op.mode = wm.at_lib_anim_mode
    else:
        op = arow.operator("animtools.lib_apply_pose", icon='CHECKMARK')
    op.item_id = item["id"]
    op = arow.operator("animtools.lib_select_controllers", icon='RESTRICT_SELECT_OFF')
    op.item_id = item["id"]


def _button_context_menu(self, context):
    op = getattr(context, "button_operator", None)
    if op is None or op.rna_type.identifier != "ANIMTOOLS_OT_lib_select_item":
        return
    item_id = op.item_id
    layout = self.layout
    layout.separator()
    layout.operator("animtools.lib_select_controllers", icon='RESTRICT_SELECT_OFF').item_id = item_id
    if item_id.lower().endswith(ANIM_EXT):
        p = layout.operator("animtools.lib_apply_anim", icon='CHECKMARK')
        p.item_id, p.mode = item_id, context.window_manager.at_lib_anim_mode
    else:
        layout.operator("animtools.lib_apply_pose", icon='CHECKMARK').item_id = item_id
    layout.operator("animtools.lib_update", icon='FILE_REFRESH').item_id = item_id


# ==========================================================================================
# REGISTER
# ==========================================================================================
classes = (
    AT_LibraryPrefs, AT_OT_LibGoto, AT_OT_LibBack, AT_OT_LibRefresh, AT_OT_LibOpenFolder,
    AT_OT_LibCreateRoot, AT_OT_LibNewFolder, AT_OT_LibRenameFolder, AT_OT_LibDeleteFolder,
    AT_OT_LibSavePose, AT_OT_LibSaveAnim, AT_OT_LibUpdate, AT_OT_LibApplyPose, AT_OT_LibApplyAnim,
    AT_OT_LibSelectControllers, AT_OT_LibSelectItem, AT_OT_LibRenameItem, AT_OT_LibDeleteItem,
)


def register():
    global _pcoll
    for cls in classes:
        bpy.utils.register_class(cls)
    _pcoll = bpy.utils.previews.new()
    menu = getattr(bpy.types, "UI_MT_button_context_menu", None)
    if menu:
        menu.append(_button_context_menu)
    wm = bpy.types.WindowManager
    wm.at_lib_relpath = StringProperty(default="")
    wm.at_lib_selected = StringProperty(default="")
    wm.at_lib_show_settings = BoolProperty(default=False, name="Settings",
                                           description="Show the library folder and rig matching settings")
    wm.at_lib_anim_mode = EnumProperty(name="Paste Mode", items=ANIM_MODES, default='REPLACE')
    sc = bpy.types.Scene
    sc.at_lib_match = EnumProperty(
        name="Rig Matching",
        items=[('AUTO', "Auto", "Match bones by name, also across character prefixes / suffixes / namespaces"),
               ('EXACT', "Exact", "Only bones with exactly the same name")],
        default='AUTO', update=lambda self, ctx: _match_cache.clear())
    sc.at_lib_from = StringProperty(name="From", description="Prefix of the SAVED bones to replace",
                                    update=lambda self, ctx: _match_cache.clear())
    sc.at_lib_to = StringProperty(name="To", description="Prefix to use on THIS rig instead",
                                  update=lambda self, ctx: _match_cache.clear())


def unregister():
    global _pcoll
    menu = getattr(bpy.types, "UI_MT_button_context_menu", None)
    if menu:
        try:
            menu.remove(_button_context_menu)
        except Exception:
            pass
    for p in ("at_lib_match", "at_lib_from", "at_lib_to"):
        delattr(bpy.types.Scene, p)
    for p in ("at_lib_relpath", "at_lib_selected", "at_lib_show_settings", "at_lib_anim_mode"):
        delattr(bpy.types.WindowManager, p)
    if _pcoll is not None:
        bpy.utils.previews.remove(_pcoll)
        _pcoll = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
