"""Foot Lock: pin controllers (pose bones or objects) in WORLD space across a frame range.

Idea (same as the Mad Art foot lock, written from scratch): capture the controller's world
matrix on an anchor frame, then on every frame of the range drive the controller back to that
matrix (constraint-aware) and key it, so the controller stays planted while its parents move.

Differences: axis locks are real world axes, the solver works on all independent controllers at
once (one depsgraph update per pass), iterations are automatic, and rotations are kept continuous.
"""
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty
from bpy.types import PropertyGroup
from mathutils import Euler, Matrix

from .core import _get_channelbag

_EULER_MODES = {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
_TOL = 1e-5        # relative matrix tolerance: "planted"
_WARN_TOL = 1e-3   # worse than this after solving: tell the user
_MAX_PASSES = 8    # hard cap of the automatic re-solve


# ==========================================
# PROPERTIES
# ==========================================
class AT_FootLockProps(PropertyGroup):
    use_playback_range: BoolProperty(
        name="Playback Range",
        description="Lock over the scene playback range (the preview range if it is enabled)",
        default=True)
    frame_start: IntProperty(name="Start", description="First frame of the lock range", default=1)
    frame_end: IntProperty(name="End", description="Last frame of the lock range", default=24)
    anchor: EnumProperty(
        name="Anchor",
        description="Frame whose world pose is captured and held for the whole range",
        items=[
            ('START', "First", "Hold the pose the controller has on the first frame"),
            ('CURRENT', "Current", "Hold the pose the controller has on the current frame"),
            ('END', "Last", "Hold the pose the controller has on the last frame"),
        ],
        default='START')

    loc_x: BoolProperty(name="X", description="Lock world X position", default=True)
    loc_y: BoolProperty(name="Y", description="Lock world Y position", default=True)
    loc_z: BoolProperty(name="Z", description="Lock world Z position", default=True)
    rot_x: BoolProperty(name="X", description="Lock world X rotation", default=True)
    rot_y: BoolProperty(name="Y", description="Lock world Y rotation", default=True)
    rot_z: BoolProperty(name="Z", description="Lock world Z rotation", default=True)
    lock_scale: BoolProperty(name="Scale", description="Also lock the world scale", default=False)


# ==========================================
# TARGET WRAPPER (pose bone or object)
# ==========================================
class _Target:
    """Bones are read/written in armature space, objects in world space."""
    __slots__ = ("x", "arm", "is_bone")

    def __init__(self, xform, arm=None):
        self.x = xform
        self.arm = arm
        self.is_bone = arm is not None

    @property
    def name(self):
        return self.x.name

    def get_world(self):
        if self.is_bone:
            return self.arm.matrix_world @ self.x.matrix
        return self.x.matrix_world.copy()

    def to_space(self, world):
        if self.is_bone:
            return self.arm.matrix_world.inverted_safe() @ world
        return world.copy()

    def get_space(self):
        return self.x.matrix.copy() if self.is_bone else self.x.matrix_world.copy()

    def set_space(self, mat):
        if self.is_bone:
            self.x.matrix = mat
        else:
            self.x.matrix_world = mat


def _gather_targets(context):
    if context.mode == 'POSE':
        arm = context.active_object
        if arm is None or arm.type != 'ARMATURE':
            return []
        return [_Target(pb, arm) for pb in (context.selected_pose_bones or [])]
    if context.mode == 'OBJECT':
        return [_Target(ob) for ob in context.selected_objects]
    return []


def _levels(targets):
    """Group targets so a target never shares a group with one of its own selected ancestors."""
    names = {t.name for t in targets}
    groups = {}
    for t in targets:
        depth, p = 0, t.x.parent
        while p is not None:
            if p.name in names:
                depth += 1
            p = p.parent
        groups.setdefault(depth, []).append(t)
    return [groups[d] for d in sorted(groups)]


def _range(scene, props):
    if props.use_playback_range:
        if scene.use_preview_range:
            a, b = scene.frame_preview_start, scene.frame_preview_end
        else:
            a, b = scene.frame_start, scene.frame_end
    else:
        a, b = props.frame_start, props.frame_end
    return min(a, b), max(a, b)


def _driven_groups(t):
    """Channel groups ('loc', 'rot', 'scl') of the target that are driven (cannot be keyed)."""
    owner = t.arm if t.is_bone else t.x
    ad = owner.animation_data
    if not ad or not ad.drivers:
        return set()
    prefix = 'pose.bones["%s"].' % t.name if t.is_bone else ""
    out = set()
    for d in ad.drivers:
        if not d.data_path.startswith(prefix):
            continue
        sub = d.data_path[len(prefix):]
        if sub == 'location':
            out.add('loc')
        elif sub.startswith('rotation_'):
            out.add('rot')
        elif sub == 'scale':
            out.add('scl')
    return out


# ==========================================
# GOAL + SOLVER
# ==========================================
class _Anchor:
    __slots__ = ("matrix", "loc", "quat", "euler", "scale")

    def __init__(self, matrix):
        self.matrix = matrix
        self.loc, self.quat, self.scale = matrix.decompose()
        self.euler = self.quat.to_euler('XYZ')


def _compose_goal(a, cur, lock_loc, lock_rot, lock_scl, full):
    """World matrix to reach: locked world axes from the anchor, the rest from the animation."""
    if full:
        return a.matrix
    c_loc, c_rot, c_scl = cur.decompose()
    loc = tuple(a.loc[i] if lock_loc[i] else c_loc[i] for i in range(3))
    if all(lock_rot):
        rot = a.quat
    elif not any(lock_rot):
        rot = c_rot
    else:
        ce = c_rot.to_euler('XYZ', a.euler)
        rot = Euler(tuple(a.euler[i] if lock_rot[i] else ce[i] for i in range(3)), 'XYZ').to_quaternion()
    return Matrix.LocRotScale(loc, rot, a.scale if lock_scl else c_scl)


def _mat_err(a, b):
    return max(abs(a[r][c] - b[r][c]) for r in range(4) for c in range(4))


def _solve(vl, batch, goals):
    """Drive every target in `batch` so its post-constraint matrix equals its goal.

    Setting a matrix ignores the target's own constraints, so we measure the residual and
    correct it. All targets share ONE depsgraph update per pass. Targets that are planted after
    the first pass (no constraints) cost nothing more; stalled ones stop early and keep their
    best attempt.
    """
    goal = {id(t): t.to_space(goals[id(t)]) for t in batch}
    tol = {k: _TOL * max(1.0, max(abs(m[r][3]) for r in range(3))) for k, m in goal.items()}
    attempt = {k: m.copy() for k, m in goal.items()}
    applied, best_att, best_err, prev_err = {}, {}, {}, {}

    pending = list(batch)
    for _ in range(_MAX_PASSES):
        for t in pending:
            k = id(t)
            t.set_space(attempt[k])
            applied[k] = attempt[k]
        vl.update()
        nxt = []
        for t in pending:
            k = id(t)
            actual = t.get_space()
            err = _mat_err(actual, goal[k])
            if err < best_err.get(k, float("inf")):
                best_err[k], best_att[k] = err, applied[k]
            if err <= tol[k]:
                continue
            if k in prev_err and err >= prev_err[k] * 0.999:
                continue  # stalled: more passes will not help (limit constraint, ...)
            prev_err[k] = err
            attempt[k] = (goal[k] @ actual.inverted_safe()) @ attempt[k]
            nxt.append(t)
        pending = nxt
        if not pending:
            break

    redo = [t for t in batch if applied[id(t)] is not best_att[id(t)]]
    if redo:
        for t in redo:
            t.set_space(best_att[id(t)])
        vl.update()
    return {id(t): best_err[id(t)] for t in batch}


def _read_rotation(xf):
    rm = xf.rotation_mode
    if rm in _EULER_MODES:
        return xf.rotation_euler.copy()
    if rm == 'QUATERNION':
        return xf.rotation_quaternion.copy()
    return None


def _key_target(t, frame, key_loc, key_rot, key_scl, driven, prev_rot):
    xf = t.x

    def ins(path):
        try:
            xf.keyframe_insert(data_path=path, frame=frame)
        except (RuntimeError, TypeError):
            pass

    if key_loc and 'loc' not in driven:
        ins("location")
    if key_rot and 'rot' not in driven:
        rm, k = xf.rotation_mode, id(t)
        if rm in _EULER_MODES:
            e = xf.rotation_euler.copy()
            if prev_rot.get(k) is not None:
                e.make_compatible(prev_rot[k])  # no 360 degree flips between frames
                xf.rotation_euler = e
            prev_rot[k] = e
            ins("rotation_euler")
        elif rm == 'QUATERNION':
            q = xf.rotation_quaternion.copy()
            if prev_rot.get(k) is not None and q.dot(prev_rot[k]) < 0.0:
                q.negate()
                xf.rotation_quaternion = q
            prev_rot[k] = q
            ins("rotation_quaternion")
        else:
            ins("rotation_axis_angle")
    if key_scl and 'scl' not in driven:
        ins("scale")


# ==========================================
# OPERATORS
# ==========================================
class _PoseOrObjectPoll:
    @classmethod
    def poll(cls, context):
        if context.mode == 'POSE':
            return bool(context.selected_pose_bones)
        if context.mode == 'OBJECT':
            return bool(context.selected_objects)
        return False


class AT_OT_FootLockPin(_PoseOrObjectPoll, bpy.types.Operator):
    bl_idname = "animtools.foot_lock_pin"
    bl_label = "Pin"
    bl_description = ("Capture the world pose on the anchor frame and key the controllers over the "
                      "range so they stay planted while their parents move. Works on bones and "
                      "objects and respects constraints")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        props = scene.at_foot_lock
        vl = context.view_layer
        targets = _gather_targets(context)
        if not targets:
            self.report({'WARNING'}, "Nothing selected to pin.")
            return {'CANCELLED'}

        lock_loc = (props.loc_x, props.loc_y, props.loc_z)
        lock_rot = (props.rot_x, props.rot_y, props.rot_z)
        lock_scl = props.lock_scale
        key_loc, key_rot = any(lock_loc), any(lock_rot)
        if not (key_loc or key_rot or lock_scl):
            self.report({'WARNING'}, "No axis is enabled to lock.")
            return {'CANCELLED'}
        full = all(lock_loc) and all(lock_rot) and lock_scl
        needs_cur = not (all(lock_loc) and all(lock_rot))  # partial lock keeps the original animation

        f_start, f_end = _range(scene, props)
        anchor_frame = {'START': f_start, 'END': f_end, 'CURRENT': scene.frame_current}[props.anchor]
        frames = list(range(f_start, f_end + 1))
        original_frame = scene.frame_current
        levels = _levels(targets)
        driven = {id(t): _driven_groups(t) for t in targets}

        wm = context.window_manager
        wm.progress_begin(0, len(frames) * (2 if needs_cur else 1))
        progress = 0
        cur_store, prev_rot, bad = {}, {}, {}
        try:
            scene.frame_set(anchor_frame)
            anchors = {id(t): _Anchor(t.get_world()) for t in targets}

            if needs_cur:  # original animated world poses, read BEFORE any key is changed
                for f in frames:
                    scene.frame_set(f)
                    for t in targets:
                        cur_store[(id(t), f)] = t.get_world()
                    progress += 1
                    wm.progress_update(progress)

            for f in frames:
                scene.frame_set(f)
                for level in levels:
                    goals = {}
                    for t in level:
                        k = id(t)
                        if k not in prev_rot:
                            prev_rot[k] = _read_rotation(t.x)
                        cur = cur_store.get((k, f))
                        if cur is None and not full:
                            cur = t.get_world()
                        goals[k] = _compose_goal(anchors[k], cur, lock_loc, lock_rot, lock_scl, full)
                    errs = _solve(vl, level, goals)
                    for t in level:
                        if errs[id(t)] > _WARN_TOL:
                            bad[t.name] = max(bad.get(t.name, 0.0), errs[id(t)])
                        _key_target(t, f, key_loc, key_rot, lock_scl, driven[id(t)], prev_rot)
                progress += 1
                wm.progress_update(progress)
        finally:
            wm.progress_end()
            scene.frame_set(original_frame)

        self.report({'INFO'}, "Foot Lock: %d controller(s), frames %d-%d, anchor %d"
                    % (len(targets), f_start, f_end, anchor_frame))
        if bad:
            self.report({'WARNING'}, "Could not lock exactly (constraint limits?): " + ", ".join(sorted(bad)))
        skipped = sorted(t.name for t in targets if driven[id(t)])
        if skipped:
            self.report({'WARNING'}, "Driver-controlled channels were not keyed: " + ", ".join(skipped))
        return {'FINISHED'}


class AT_OT_FootLockClear(_PoseOrObjectPoll, bpy.types.Operator):
    bl_idname = "animtools.foot_lock_clear"
    bl_label = "Clear"
    bl_description = ("Delete the keys of the enabled channel groups (location / rotation / scale) "
                      "inside the range for the selected controllers, so you can pin again cleanly")
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        props = scene.at_foot_lock
        targets = _gather_targets(context)
        f_start, f_end = _range(scene, props)
        groups = []
        if props.loc_x or props.loc_y or props.loc_z:
            groups.append('location')
        if props.rot_x or props.rot_y or props.rot_z:
            groups += ['rotation_euler', 'rotation_quaternion', 'rotation_axis_angle']
        if props.lock_scale:
            groups.append('scale')

        removed = 0
        for t in targets:
            owner = t.arm if t.is_bone else t.x
            bag = _get_channelbag(owner)
            if bag is None:
                continue
            prefix = 'pose.bones["%s"].' % t.name if t.is_bone else ""
            paths = {prefix + g for g in groups}
            for fc in [fc for fc in bag.fcurves if fc.data_path in paths]:
                kps = fc.keyframe_points
                for i in reversed(range(len(kps))):  # fresh point each time: removal re-allocates
                    if f_start - 0.001 <= kps[i].co[0] <= f_end + 0.001:
                        kps.remove(kps[i])
                        removed += 1
                if len(kps) == 0:
                    bag.fcurves.remove(fc)
                else:
                    fc.update()
            owner.update_tag()
        context.view_layer.update()
        self.report({'INFO'}, "Removed %d key(s) in %d-%d" % (removed, f_start, f_end))
        return {'FINISHED'}


# ==========================================
# REGISTER (the panel lives in ui.py)
# ==========================================
classes = (AT_FootLockProps, AT_OT_FootLockPin, AT_OT_FootLockClear)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.at_foot_lock = PointerProperty(type=AT_FootLockProps)


def unregister():
    del bpy.types.Scene.at_foot_lock
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
