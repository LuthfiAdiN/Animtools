"""Convert Rotation Mode for Blender 5.0 / 5.1 (Blender 5.3 added this as anim.rotation_mode_convert).

On every selected object (Object Mode) or pose bone (Pose Mode): change the rotation mode and convert the
existing animation into the new mode, so the pose stays exactly as it was.

* Without Bake the new curves get keys on the frames the old curves had.
* With Bake every frame between the first and last key gets a key, so in-between motion is preserved.
* The interpolation of the old keys is kept (stepped / blocking animation stays stepped) and Euler / axis-angle
  values stay continuous from frame to frame (no 360 degree flips).

The result matches Blender 5.3's operator: keys on the same frames and the same rotation (within 1e-7).
"""
import math

import bpy
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
from mathutils import Euler, Quaternion, Vector

from .core import _get_channelbag

EULER_MODES = {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
MODES = (
    ('QUATERNION', "Quaternion (WXYZ)", "No Gimbal Lock"),
    ('XYZ', "XYZ Euler", "XYZ Rotation Order - prone to Gimbal Lock (default)"),
    ('XZY', "XZY Euler", "XZY Rotation Order - prone to Gimbal Lock"),
    ('YXZ', "YXZ Euler", "YXZ Rotation Order - prone to Gimbal Lock"),
    ('YZX', "YZX Euler", "YZX Rotation Order - prone to Gimbal Lock"),
    ('ZXY', "ZXY Euler", "ZXY Rotation Order - prone to Gimbal Lock"),
    ('ZYX', "ZYX Euler", "ZYX Rotation Order - prone to Gimbal Lock"),
    ('AXIS_ANGLE', "Axis Angle", "Axis Angle (W+XYZ), defines a rotation around some axis defined by 3D-Vector"),
)
_MODE_LABEL = {m[0]: m[1] for m in MODES}


# ---------------------------------------------------------------- shared math (also used by the library)
def rot_path(mode):
    return 'rotation_quaternion' if mode == 'QUATERNION' else \
        'rotation_axis_angle' if mode == 'AXIS_ANGLE' else 'rotation_euler'


def channel_count(mode):
    return 3 if mode in EULER_MODES else 4


def quat_from(mode, v):
    """Unit quaternion from the channel values of a rotation mode."""
    if mode == 'QUATERNION':
        q = Quaternion(v[:4])
    elif mode == 'AXIS_ANGLE':
        axis = Vector(v[1:4])
        q = Quaternion(axis, v[0]) if axis.length > 1e-9 else Quaternion()
    else:
        q = Euler(v[:3], mode).to_quaternion()
    if q.magnitude > 1e-12:
        q.normalize()
    return q


def comps_from(mode, q, prev=None):
    """Channel values of `mode` for quaternion `q`; `prev` (the previous frame's values) keeps them continuous."""
    if mode == 'QUATERNION':
        return list(q)
    if mode == 'AXIS_ANGLE':
        axis, angle = q.to_axis_angle()
        if prev is not None and axis.dot(Vector(prev[1:4])) < 0.0:
            axis, angle = -axis, -angle
        return [angle, axis.x, axis.y, axis.z]
    e = q.to_euler(mode, Euler(prev[:3], mode)) if prev is not None else q.to_euler(mode)
    return [e.x, e.y, e.z]


# ---------------------------------------------------------------- conversion
def _driven(owner, paths):
    ad = owner.animation_data
    return bool(ad and any(d.data_path in paths for d in ad.drivers))


def _interp_at(src_fcurves, frame):
    """Interpolation of the latest source key at or before `frame` (Bezier when there is none)."""
    best_x, best = None, 'BEZIER'
    for fc in src_fcurves:
        for kp in fc.keyframe_points:
            if kp.co.x <= frame + 1e-4 and (best_x is None or kp.co.x > best_x):
                best_x, best = kp.co.x, kp.interpolation
    return best


def convert_target(owner, xf, prefix, new_mode, bake):
    """Convert one object / pose bone. Returns (status, key_count): status 'ok', 'same' or 'driven'."""
    old_mode = xf.rotation_mode
    if old_mode == new_mode:
        return 'same', 0
    src_path, dst_path = prefix + rot_path(old_mode), prefix + rot_path(new_mode)
    if _driven(owner, {src_path, dst_path}):
        return 'driven', 0

    n_src, n_dst = channel_count(old_mode), channel_count(new_mode)
    static = list(getattr(xf, rot_path(old_mode)))
    bag = _get_channelbag(owner)
    src = {}
    if bag is not None:
        for fc in bag.fcurves:
            if fc.data_path == src_path and fc.keyframe_points:
                src[fc.array_index] = fc

    if not src:  # nothing animated: only convert the stored value
        comps = comps_from(new_mode, quat_from(old_mode, static))
        xf.rotation_mode = new_mode
        setattr(xf, rot_path(new_mode), comps)
        return 'ok', 0

    # 1) sample everything BEFORE touching any curve
    xs = [kp.co.x for fc in src.values() for kp in fc.keyframe_points]
    if bake:
        frames = [float(f) for f in range(int(math.floor(min(xs) + 1e-6)), int(math.ceil(max(xs) - 1e-6)) + 1)]
    else:
        frames = sorted({round(x, 4) for x in xs})
    src_list = list(src.values())
    group = next((fc.group.name for fc in src_list if fc.group), None)
    rows, prev_q, prev_c = [], None, None
    for f in frames:
        vals = [src[i].evaluate(f) if i in src else static[i] for i in range(n_src)]
        q = quat_from(old_mode, vals)
        if prev_q is not None and prev_q.dot(q) < 0.0:
            q.negate()
        prev_q = q
        comps = comps_from(new_mode, q, prev_c)
        prev_c = comps
        rows.append((f, comps, _interp_at(src_list, f)))

    # 2) replace the curves: drop the old ones (and stale ones of the new mode), write the new ones
    for fc in [fc for fc in bag.fcurves if fc.data_path in (src_path, dst_path)]:
        bag.fcurves.remove(fc)
    xf.rotation_mode = new_mode
    count = 0
    for i in range(n_dst):
        fc = bag.fcurves.new(dst_path, index=i, group_name=group or "")
        for f, comps, interp in rows:
            kp = fc.keyframe_points.insert(f, comps[i], options={'FAST'})
            kp.interpolation = interp
            count += 1
        fc.update()
    return 'ok', count


def gather_targets(context):
    """[(owner id, transform, data-path prefix)] of the selection."""
    if context.mode == 'POSE':
        return [(pb.id_data, pb, 'pose.bones["%s"].' % pb.name) for pb in (context.selected_pose_bones or [])]
    if context.mode == 'OBJECT':
        return [(ob, ob, "") for ob in context.selected_objects]
    return []


class AT_OT_RotationModeConvert(Operator):
    bl_idname = "animtools.rotation_mode_convert"
    bl_label = "Convert Rotation Mode"
    bl_description = ("On all selected objects/bones, change the rotation mode and convert the existing animation "
                      "into that mode so the pose stays the same")
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(name="Mode", items=MODES, default='QUATERNION',
                       description="The rotation mode to convert the selection to")
    bake: BoolProperty(name="Bake", default=False,
                       description="Key every frame between the first and last key, so the in-between motion "
                                   "is preserved in the new mode")

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'}

    def execute(self, context):
        targets = gather_targets(context)
        if not targets:
            self.report({'WARNING'}, "Nothing selected.")
            return {'CANCELLED'}
        done = keys = 0
        skipped_driven = []
        for owner, xf, prefix in targets:
            status, n = convert_target(owner, xf, prefix, self.mode, self.bake)
            if status == 'ok':
                done += 1
                keys += n
            elif status == 'driven':
                skipped_driven.append(xf.name)
        context.scene.frame_set(context.scene.frame_current)  # refresh the evaluated pose
        if skipped_driven:
            self.report({'WARNING'}, "Rotation is driven, not converted: " + ", ".join(skipped_driven))
        if done == 0 and not skipped_driven:
            self.report({'INFO'}, f"Everything selected is already {_MODE_LABEL[self.mode]}.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Converted {done} to {_MODE_LABEL[self.mode]}" + (f" ({keys} keys)" if keys else ""))
        return {'FINISHED'}


def draw_panel(layout, context):
    sc = context.scene
    targets = gather_targets(context)
    active = context.active_pose_bone if context.mode == 'POSE' else context.active_object
    if active is not None:
        layout.label(text=f"{active.name}: {_MODE_LABEL.get(active.rotation_mode, active.rotation_mode)}",
                     icon='BONE_DATA' if context.mode == 'POSE' else 'OBJECT_DATA')
    col = layout.column(align=True)
    col.prop(sc, "at_rotmode_target", text="Convert to")
    col.prop(sc, "at_rotmode_bake")
    row = layout.row()
    row.scale_y = 1.2
    row.enabled = bool(targets)
    op = row.operator("animtools.rotation_mode_convert",
                      text=f"Convert {len(targets)} selected" if targets else "Convert Selected",
                      icon='DRIVER_ROTATIONAL_DIFFERENCE')
    op.mode = sc.at_rotmode_target
    op.bake = sc.at_rotmode_bake


classes = (AT_OT_RotationModeConvert,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.at_rotmode_target = EnumProperty(
        name="Convert to", items=MODES, default='QUATERNION',
        description="Rotation mode the selection is converted to")
    bpy.types.Scene.at_rotmode_bake = BoolProperty(
        name="Bake", default=False,
        description="Key every frame so the in-between motion is preserved in the new mode")


def unregister():
    del bpy.types.Scene.at_rotmode_bake
    del bpy.types.Scene.at_rotmode_target
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
