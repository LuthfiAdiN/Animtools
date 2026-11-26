"""AnimTools core helpers (Blender 5.x, slotted actions)."""
import bpy
from mathutils import Matrix
from typing import Optional, Tuple, Union
from bpy_extras.anim_utils import action_get_channelbag_for_slot

# ==========================================
# CLIPBOARD (world transform)
# ==========================================
_clipboard: Optional[Matrix] = None


def clipboard_set(matrix: Matrix):
    global _clipboard
    _clipboard = matrix.copy()


def clipboard_get() -> Optional[Matrix]:
    return _clipboard


# ==========================================
# HELPERS
# ==========================================
def get_active_entity(context) -> Tuple[Union[bpy.types.Object, bpy.types.PoseBone, None], bool]:
    obj = context.active_object
    if not obj:
        return None, False
    if obj.mode == 'POSE' and context.active_pose_bone:
        return context.active_pose_bone, True
    return obj, False


def get_world_matrix(entity, is_bone: bool) -> Matrix:
    if is_bone:
        return entity.id_data.matrix_world @ entity.matrix
    return entity.matrix_world.copy()


def set_world_matrix(entity, is_bone: bool, matrix: Matrix):
    if is_bone:
        entity.matrix = entity.id_data.matrix_world.inverted() @ matrix
    else:
        entity.matrix_world = matrix


def get_rotation_mode(entity):
    if hasattr(entity, 'rotation_mode') and entity.rotation_mode in ("QUATERNION", "AXIS_ANGLE"):
        return entity.rotation_mode.lower()
    return "euler"


def _id_and_prefix(entity, is_bone: bool):
    """ID that owns the animation + the data-path prefix of the entity inside it."""
    if is_bone:
        return entity.id_data, f'pose.bones["{entity.name}"].'
    return entity, ""


def _get_channelbag(id_data):
    """Read-only access to the channelbag of the active action slot (never creates one)."""
    ad = id_data.animation_data
    if not ad or not ad.action or not ad.action_slot:
        return None
    return action_get_channelbag_for_slot(ad.action, ad.action_slot)


def insert_keyframe_transform(entity, is_bone: bool, frame: int):
    id_data, prefix = _id_and_prefix(entity, is_bone)
    for dp in ("location", f"rotation_{get_rotation_mode(entity)}", "scale"):
        id_data.keyframe_insert(data_path=prefix + dp, frame=frame)


def insert_keyframe_constraint(entity, is_bone: bool, constraint_name: str, frame: int):
    id_data, prefix = _id_and_prefix(entity, is_bone)
    id_data.keyframe_insert(
        data_path=f'{prefix}constraints["{constraint_name}"].influence', frame=frame)


def check_constraint_keyframe(entity, is_bone: bool, constraint_name: str, frame: int):
    """Icon name: keyframe on this frame / animated elsewhere / none.

    Called from panel draw(), so it is strictly read-only and O(keys of one curve).
    """
    id_data, prefix = _id_and_prefix(entity, is_bone)
    bag = _get_channelbag(id_data)
    if bag is None:
        return 'NONE'
    fcu = bag.fcurves.find(f'{prefix}constraints["{constraint_name}"].influence')
    if fcu is None:
        return 'NONE'
    for kp in fcu.keyframe_points:
        if abs(kp.co[0] - frame) < 0.001:
            return 'KEYFRAME'
    return 'DECORATE_ANIMATE'


# ==========================================
# TOGGLE LOGIC
# ==========================================
def toggle_child_of_logic(entity, is_bone: bool, constraint_name: str):
    con = entity.constraints.get(constraint_name)
    if not con or con.type != 'CHILD_OF':
        return

    # No target: just flip influence, nothing to compensate
    if not con.target:
        con.influence = 0.0 if con.influence > 0.5 else 1.0
        return

    # World pose BEFORE anything changes
    current_world_mat = get_world_matrix(entity, is_bone)
    new_influence = 0.0 if con.influence > 0.5 else 1.0
    con.influence = new_influence

    if new_influence == 0.0:
        # OFF: keep the pose where it is in world space
        set_world_matrix(entity, is_bone, current_world_mat)
        return

    # ON: local = effective_parent^-1 @ world, so the pose does not jump
    parent_world = con.target.matrix_world.copy()
    if con.subtarget and con.target.type == 'ARMATURE':
        p_bone = con.target.pose.bones.get(con.subtarget)
        if p_bone:
            parent_world = parent_world @ p_bone.matrix
    effective_parent = parent_world @ con.inverse_matrix
    set_world_matrix(entity, is_bone, effective_parent.inverted_safe() @ current_world_mat)


# ==========================================
# DYNAMIC PARENT
# ==========================================
def _is_descendant(bone, ancestor) -> bool:
    node = bone.parent
    while node is not None:
        if node == ancestor:
            return True
        node = node.parent
    return False


def get_selected_parent(context):
    active_obj = context.active_object
    selected_objs = context.selected_objects

    if active_obj and active_obj.mode == 'OBJECT':
        for obj in selected_objs:
            if obj != active_obj:
                return obj, None

    if active_obj and active_obj.mode == 'POSE':
        active_bone = context.active_pose_bone
        for bone in context.selected_pose_bones or ():
            # a descendant of the active bone can never be its parent (dependency cycle)
            if bone != active_bone and not _is_descendant(bone, active_bone):
                return active_obj, bone.name

        for obj in selected_objs:
            if obj != active_obj:
                if obj.type == 'ARMATURE' and obj.data.bones.active:
                    return obj, obj.data.bones.active.name
                return obj, None

    return None, None


def active_dp(entity):
    """The newest Dynamic Parent constraint that is switched on (influence > 0.5), or None."""
    for con in reversed(entity.constraints):
        if con.name.startswith("DP_") and con.influence > 0.5:
            return con
    return None


_active_dp = active_dp  # old name


def get_selected_objects(context):
    """Selected objects (Object Mode) or pose bones (Pose Mode); the active one comes last."""
    if context.mode == "OBJECT":
        active = context.active_object
        items = [o for o in context.selected_objects if o != active]
    elif context.mode == "POSE":
        active = context.active_pose_bone
        items = [b for b in (context.selected_pose_bones or ()) if b != active]
    else:
        return []
    if active is not None:
        items.append(active)
    return items


def playback_range(scene):
    """Scene playback range (the preview range when it is enabled)."""
    if scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def dynamic_parent_create(context, op) -> bool:
    """Create (or switch to) a dynamic parent. Returns False when nothing was done."""
    entity, is_bone = get_active_entity(context)
    if not entity:
        op.report({'ERROR'}, "Nothing selected.")
        return False

    # Validate BEFORE touching the current dynamic parent
    parent_obj, parent_bone_name = get_selected_parent(context)
    if not parent_obj:
        op.report({'ERROR'}, "Select a target parent (object or bone).")
        return False

    frame = context.scene.frame_current

    current = active_dp(entity)
    if current:
        dynamic_parent_disable(context, entity, is_bone, current)
        context.view_layer.update()

    insert_keyframe_transform(entity, is_bone, frame)

    con = entity.constraints.new('CHILD_OF')
    dp_name = f"DP_{parent_obj.name}"
    if parent_bone_name:
        dp_name += f".{parent_bone_name}"
    con.name = dp_name
    con.target = parent_obj
    if parent_bone_name and parent_obj.type == 'ARMATURE':
        con.subtarget = parent_bone_name

    # Set Inverse so the pose stays where it is
    try:
        if is_bone:
            with context.temp_override(object=entity.id_data, active_pose_bone=entity):
                bpy.ops.constraint.childof_set_inverse(constraint=con.name, owner='BONE')
        else:
            with context.temp_override(object=entity):
                bpy.ops.constraint.childof_set_inverse(constraint=con.name, owner='OBJECT')
    except Exception as e:
        print(f"AnimTools: Set Inverse failed ({e}), using matrix fallback")
        child_world = get_world_matrix(entity, is_bone)
        parent_world = parent_obj.matrix_world.copy()
        if parent_bone_name:
            p_bone = parent_obj.pose.bones.get(parent_bone_name)
            if p_bone:
                parent_world = parent_world @ p_bone.matrix
        con.inverse_matrix = parent_world.inverted_safe() @ child_world

    con.influence = 0.0
    insert_keyframe_constraint(entity, is_bone, con.name, frame - 1)
    con.influence = 1.0
    insert_keyframe_constraint(entity, is_bone, con.name, frame)

    op.report({'INFO'}, f"Created Dynamic Parent: {con.name}")
    return True


def dynamic_parent_disable(context, entity, is_bone, con):
    """Turn a dynamic parent off on the current frame WITHOUT moving the pose in world space."""
    frame = context.scene.frame_current
    prev = frame - 1

    world = get_world_matrix(entity, is_bone)   # pose as seen with the parent active

    con.influence = 1.0
    insert_keyframe_constraint(entity, is_bone, con.name, prev)
    insert_keyframe_transform(entity, is_bone, prev)

    con.influence = 0.0
    set_world_matrix(entity, is_bone, world)    # bake the world pose into local values
    insert_keyframe_constraint(entity, is_bone, con.name, frame)
    insert_keyframe_transform(entity, is_bone, frame)


def dynamic_parent_clear(entity, is_bone):
    """Remove the entity's DP_ constraints, their influence curves and the keys made for them."""
    id_data, prefix = _id_and_prefix(entity, is_bone)
    bag = _get_channelbag(id_data)

    if bag is not None:
        dp_prefix = f'{prefix}constraints["DP_'
        dp_curves = [fc for fc in bag.fcurves if fc.data_path.startswith(dp_prefix)]
        frames = {int(kp.co[0]) for fc in dp_curves for kp in fc.keyframe_points}

        if frames:
            transform_paths = {prefix + p for p in (
                "location", "rotation_euler", "rotation_quaternion",
                "rotation_axis_angle", "scale")}
            for fc in [fc for fc in bag.fcurves if fc.data_path in transform_paths]:
                kps = fc.keyframe_points
                # remove from the end, fetching a fresh point each time (removal re-allocates the array)
                for i in reversed(range(len(kps))):
                    if int(kps[i].co[0]) in frames:
                        kps.remove(kps[i])
                if len(kps) == 0:
                    bag.fcurves.remove(fc)

        for fc in dp_curves:
            bag.fcurves.remove(fc)

    for con in entity.constraints[:]:
        if con.name.startswith("DP_"):
            entity.constraints.remove(con)


def _remove_dp_constraints_and_curves(entity, is_bone: bool):
    """Delete DP_ constraints and their influence curves, keeping every transform key."""
    id_data, prefix = _id_and_prefix(entity, is_bone)
    bag = _get_channelbag(id_data)
    if bag is not None:
        dp_prefix = f'{prefix}constraints["DP_'
        for fc in [fc for fc in bag.fcurves if fc.data_path.startswith(dp_prefix)]:
            bag.fcurves.remove(fc)
    for con in entity.constraints[:]:
        if con.name.startswith("DP_"):
            entity.constraints.remove(con)


def dynamic_parent_bake_clear(context, op) -> int:
    """Bake the motion the Dynamic Parent produces into plain keys, then remove the constraints.

    Only controllers that carry a DP_ constraint are baked, over the playback range, into their
    CURRENT action (nla.bake would otherwise create a new action and drop the old one).
    Returns the number of controllers baked.
    """
    pose = context.mode == "POSE"
    targets = [e for e in get_selected_objects(context)
               if e is not None and any(c.name.startswith("DP_") for c in e.constraints)]
    if not targets:
        op.report({'WARNING'}, "No Dynamic Parent constraint on the selection.")
        return 0

    start, end = playback_range(context.scene)

    # nla.bake works on the selection: temporarily select only the controllers to bake
    if pose:
        owners = [context.active_object]
        keep = {e.name for e in targets}
        saved = {pb: pb.select for ob in owners for pb in ob.pose.bones}
        for pb in saved:
            pb.select = pb.name in keep
    else:
        keep = set(targets)
        saved = {ob: ob.select_get() for ob in context.view_layer.objects}
        for ob in saved:
            ob.select_set(ob in keep)

    try:
        bpy.ops.nla.bake(
            frame_start=start, frame_end=end, step=1,
            only_selected=True, visual_keying=True,
            clear_constraints=False, clear_parents=False,
            use_current_action=True, clean_curves=False,
            bake_types={'POSE'} if pose else {'OBJECT'},
            channel_types={'LOCATION', 'ROTATION', 'SCALE'},
        )
    finally:
        for item, was in saved.items():
            try:
                if pose:
                    item.select = was
                else:
                    item.select_set(was)
            except RuntimeError:
                pass

    for e in targets:
        _remove_dp_constraints_and_curves(e, isinstance(e, bpy.types.PoseBone))

    op.report({'INFO'}, f"Baked {len(targets)} controller(s), frames {start}-{end}, constraints removed")
    return len(targets)


def find_view3d(context, prefer="CAMERA"):
    """(window, area, region, space) of a 3D Viewport, looking at every window.

    prefer="CAMERA":  a viewport that is looking through a camera wins (the one you animate in),
                      then the viewport the operator was started from, then the biggest one.
    prefer="CONTEXT": the viewport the operator was started from wins, then a camera viewport,
                      then the biggest one.
    """
    here = context.area
    cands = []
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            space = area.spaces.active
            r3d = space.region_3d
            cands.append({
                "win": win, "area": area, "region": region, "space": space,
                "cam": r3d is not None and r3d.view_perspective == 'CAMERA',
                "here": here is not None and area == here,
                "size": area.width * area.height,
            })
    if not cands:
        return None
    order = ("cam", "here", "size") if prefer == "CAMERA" else ("here", "cam", "size")
    best = max(cands, key=lambda c: tuple(c[k] for k in order))
    return best["win"], best["area"], best["region"], best["space"]
