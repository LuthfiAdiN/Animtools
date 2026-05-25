import bpy
import math
from mathutils import Matrix
from typing import Tuple, Union, Optional
from bpy_extras.anim_utils import action_ensure_channelbag_for_slot

# Global storage
AT_CLIPBOARD_MATRIX: Optional[Matrix] = None

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_active_entity(context) -> Tuple[Union[bpy.types.Object, bpy.types.PoseBone], bool]:
    obj = context.active_object
    if not obj:
        return None, False
    if obj.mode == 'POSE' and context.active_pose_bone:
        return context.active_pose_bone, True
    return obj, False

def get_world_matrix(entity, is_bone: bool) -> Matrix:
    if is_bone:
        armature = entity.id_data
        return armature.matrix_world @ entity.matrix
    else:
        return entity.matrix_world.copy()

def set_world_matrix(entity, is_bone: bool, matrix: Matrix):
    if is_bone:
        armature = entity.id_data
        entity.matrix = armature.matrix_world.inverted() @ matrix
    else:
        entity.matrix_world = matrix

def get_rotation_mode(entity):
    if hasattr(entity, 'rotation_mode'):
        if entity.rotation_mode in ("QUATERNION", "AXIS_ANGLE"):
            return entity.rotation_mode.lower()
    return "euler"

def insert_keyframe_transform(entity, is_bone: bool, frame: int):
    """Insert location/rotation/scale keyframes. Mirrors dp_keyframe_insert_obj/pbone."""
    rotation_mode = get_rotation_mode(entity)
    data_paths = ["location", f"rotation_{rotation_mode}", "scale"]
    if is_bone:
        armature = entity.id_data
        for dp in data_paths:
            armature.keyframe_insert(
                data_path=f'pose.bones["{entity.name}"].{dp}', frame=frame
            )
    else:
        for dp in data_paths:
            entity.keyframe_insert(data_path=dp, frame=frame)

def insert_keyframe_constraint(entity, is_bone: bool, constraint_name: str, frame: int):
    """Insert influence keyframe on a constraint."""
    if is_bone:
        armature = entity.id_data
        armature.keyframe_insert(
            data_path=f'pose.bones["{entity.name}"].constraints["{constraint_name}"].influence',
            frame=frame
        )
    else:
        entity.keyframe_insert(
            data_path=f'constraints["{constraint_name}"].influence', frame=frame
        )

def check_constraint_keyframe(entity, is_bone: bool, constraint_name: str, frame: int):
    """Return icon string if keyframe exists on influence at specific frame."""
    id_data = entity.id_data if is_bone else entity

    if not id_data.animation_data or not id_data.animation_data.action:
        return 'NONE'

    action = id_data.animation_data.action
    slot = id_data.animation_data.action_slot

    if not slot:
        return 'NONE'

    try:
        bag = action_ensure_channelbag_for_slot(action, slot)
    except Exception:
        return 'NONE'

    if is_bone:
        data_path = f'pose.bones["{entity.name}"].constraints["{constraint_name}"].influence'
    else:
        data_path = f'constraints["{constraint_name}"].influence'

    fcurves = bag.fcurves if hasattr(bag, 'fcurves') else []

    for fcu in fcurves:
        if fcu.data_path == data_path:
            for kp in fcu.keyframe_points:
                if int(kp.co[0]) == frame:
                    return 'KEYFRAME'
            return 'DECORATE_ANIMATE'

    return 'NONE'

# ==========================================
# TOGGLE LOGIC (unchanged)
# ==========================================

def toggle_child_of_logic(entity, is_bone: bool, constraint_name: str):
    con = entity.constraints.get(constraint_name)
    if not con or con.type != 'CHILD_OF':
        return

    if not con.target:
        con.influence = 0.0 if con.influence > 0.5 else 1.0
        return

    current_world_mat = get_world_matrix(entity, is_bone)
    new_influence = 0.0 if con.influence > 0.5 else 1.0
    con.influence = new_influence

    if new_influence == 0.0:
        set_world_matrix(entity, is_bone, current_world_mat)
    else:
        parent_world = con.target.matrix_world.copy()
        if con.subtarget and con.target.type == 'ARMATURE':
            p_bone = con.target.pose.bones.get(con.subtarget)
            if p_bone:
                parent_world = parent_world @ p_bone.matrix

        effective_parent = parent_world @ con.inverse_matrix
        required_local = effective_parent.inverted_safe() @ current_world_mat
        set_world_matrix(entity, is_bone, required_local)

# ==========================================
# DYNAMIC PARENT LOGIC
# ==========================================

def get_last_dynamic_parent_constraint(entity):
    """
    Returns the LAST constraint if it's a DP_ constraint with influence == 1.
    Matches original dynamic_parent.py behaviour exactly.
    """
    if not entity.constraints:
        return None
    const = entity.constraints[-1]
    if const.name.startswith("DP_") and const.influence == 1:
        return const
    return None


def get_selected_objects(context):
    """
    Returns all selected objects/bones with active last.
    Mirrors get_selected_objects() from dynamic_parent.py.
    """
    if context.mode not in ("OBJECT", "POSE"):
        return []

    if context.mode == "OBJECT":
        active = context.active_object
        selected = [obj for obj in context.selected_objects if obj != active]
    else:
        active = context.active_pose_bone
        selected = [bone for bone in context.selected_pose_bones if bone != active]

    selected.append(active)
    return selected


def _dp_create_obj(context, op):
    """
    Create Dynamic Parent for a plain Object.
    Mirrors dp_create_dynamic_parent_obj() from dynamic_parent.py.
    """
    obj = context.active_object
    scn = context.scene
    list_selected_obj = list(context.selected_objects)

    if len(list_selected_obj) != 2:
        op.report({'ERROR'}, "Two objects must be selected")
        return

    i = list_selected_obj.index(obj)
    list_selected_obj.pop(i)
    parent_obj = list_selected_obj[0]

    # 1. Keyframe current transform BEFORE adding constraint
    insert_keyframe_transform(obj, False, scn.frame_current)

    # 2. Add constraint via ops (auto-assigns target)
    bpy.ops.object.constraint_add_with_targets(type='CHILD_OF')
    last_con = obj.constraints[-1]

    # 3. Name the constraint
    if parent_obj.type == 'ARMATURE':
        last_con.subtarget = parent_obj.data.bones.active.name
        last_con.name = f"DP_{last_con.target.name}.{last_con.subtarget}"
    else:
        last_con.name = f"DP_{last_con.target.name}"

    # 4. Set inverse
    bpy.ops.constraint.childof_set_inverse(
        constraint=last_con.name, owner='OBJECT'
    )

    # 5. Keyframe influence: 0 at frame-1, 1 at current frame
    current_frame = scn.frame_current
    scn.frame_current = current_frame - 1
    obj.constraints[last_con.name].influence = 0
    obj.keyframe_insert(
        data_path=f'constraints["{last_con.name}"].influence'
    )

    scn.frame_current = current_frame
    obj.constraints[last_con.name].influence = 1
    obj.keyframe_insert(
        data_path=f'constraints["{last_con.name}"].influence'
    )

    # 6. Deselect parent, keep child selected
    for ob in list_selected_obj:
        ob.select_set(False)
    obj.select_set(True)


def _dp_create_pbone(context, op):
    """
    Create Dynamic Parent for a Pose Bone.
    Mirrors dp_create_dynamic_parent_pbone() from dynamic_parent.py.
    """
    arm = context.active_object
    pbone = context.active_pose_bone
    scn = context.scene
    list_selected_obj = list(context.selected_objects)

    if len(list_selected_obj) == 2:
        # Parenting to another object / armature
        i = list_selected_obj.index(arm)
        list_selected_obj.pop(i)
        parent_obj = list_selected_obj[0]
        if parent_obj.type == 'ARMATURE':
            parent_obj_pbone = parent_obj.data.bones.active
            if parent_obj_pbone is None:
                op.report({'ERROR'}, "At least two bones must be selected")
                return
        else:
            parent_obj_pbone = None
    elif len(list_selected_obj) == 1:
        # Bone-to-bone within the same armature
        parent_obj = arm
        selected_bones = list(context.selected_pose_bones)
        if pbone in selected_bones:
            selected_bones.remove(pbone)
        if not selected_bones:
            op.report({'ERROR'}, "At least two bones must be selected")
            return
        parent_obj_pbone = selected_bones[0]
    else:
        op.report({'ERROR'}, "Two objects must be selected")
        return

    # 1. Keyframe current transform BEFORE adding constraint
    insert_keyframe_transform(pbone, True, scn.frame_current)

    # 2. Add constraint via ops (auto-assigns target)
    bpy.ops.pose.constraint_add_with_targets(type='CHILD_OF')
    last_con = pbone.constraints[-1]

    # 3. Name the constraint
    if parent_obj.type == 'ARMATURE':
        last_con.subtarget = parent_obj_pbone.name
        last_con.name = f"DP_{last_con.target.name}.{last_con.subtarget}"
    else:
        last_con.name = f"DP_{last_con.target.name}"

    # 4. Set inverse
    bpy.ops.constraint.childof_set_inverse(
        constraint=last_con.name, owner='BONE'
    )

    # 5. Keyframe influence: 0 at frame-1, 1 at current frame
    current_frame = scn.frame_current
    scn.frame_current = current_frame - 1
    pbone.constraints[last_con.name].influence = 0
    arm.keyframe_insert(
        data_path=f'pose.bones["{pbone.name}"].constraints["{last_con.name}"].influence'
    )

    scn.frame_current = current_frame
    pbone.constraints[last_con.name].influence = 1
    arm.keyframe_insert(
        data_path=f'pose.bones["{pbone.name}"].constraints["{last_con.name}"].influence'
    )


def dynamic_parent_create(context, op):
    """
    Entry point for Create.
    Mirrors DYNAMIC_PARENT_OT_create.execute() from dynamic_parent.py.
    """
    obj = context.active_object
    if not obj:
        op.report({'ERROR'}, "Nothing selected.")
        return

    if obj.type == 'ARMATURE':
        if obj.mode != 'POSE':
            op.report({'ERROR'}, "Armature objects must be in Pose mode.")
            return
        pbone = context.active_pose_bone
        const = get_last_dynamic_parent_constraint(pbone)
        if const:
            dynamic_parent_disable(context, pbone, True, const)
        _dp_create_pbone(context, op)
    else:
        const = get_last_dynamic_parent_constraint(obj)
        if const:
            dynamic_parent_disable(context, obj, False, const)
        _dp_create_obj(context, op)


def dynamic_parent_disable(context, entity, is_bone: bool, con):
    """
    Disable a DP constraint while preserving world position.
    Mirrors disable_constraint() from dynamic_parent.py exactly:
      1. Save world matrix
      2. Keyframe transform + influence at frame-1 (while constraint is still ON)
      3. Set influence = 0, restore world matrix
      4. Keyframe transform + influence at current frame (constraint now OFF)
    """
    frame = context.scene.frame_current

    # Save world matrix BEFORE touching anything
    matrix_final = get_world_matrix(entity, is_bone)

    # Keyframe the "before" state at frame-1
    insert_keyframe_transform(entity, is_bone, frame - 1)
    insert_keyframe_constraint(entity, is_bone, con.name, frame - 1)

    # Disable constraint and snap back to saved world position
    con.influence = 0.0
    set_world_matrix(entity, is_bone, matrix_final)

    # Keyframe the "after" state at current frame
    insert_keyframe_transform(entity, is_bone, frame)
    insert_keyframe_constraint(entity, is_bone, con.name, frame)


def dynamic_parent_clear(entity, is_bone: bool):
    """
    Remove all DP_ constraints and clean up their keyframes.
    Mirrors dp_clear() from dynamic_parent.py.
    """
    id_data = entity.id_data if is_bone else entity

    # If no animation data, just remove constraints
    if not id_data.animation_data or not id_data.animation_data.action:
        for con in entity.constraints[:]:
            if con.name.startswith("DP_"):
                entity.constraints.remove(con)
        return

    action = id_data.animation_data.action
    slot = id_data.animation_data.action_slot if id_data.animation_data.action_slot else None

    if not slot:
        for con in entity.constraints[:]:
            if con.name.startswith("DP_"):
                entity.constraints.remove(con)
        return

    channelbag = action_ensure_channelbag_for_slot(action, slot)

    # Collect all DP fcurves and the frames they touch
    dp_curves = []
    dp_keys = []
    for fcurve in channelbag.fcurves:
        if "DP_" in fcurve.data_path:
            dp_curves.append(fcurve)
            for kp in fcurve.keyframe_points:
                dp_keys.append(kp.co[0])

    dp_keys = list(set(dp_keys))
    dp_keys.sort()

    # Build prefix for bone data paths
    prefix = f'pose.bones["{entity.name}"].' if is_bone else ""
    transform_suffixes = [
        "location",
        "rotation_euler",
        "rotation_quaternion",
        "rotation_axis_angle",
        "scale",
    ]

    # Remove transform keyframes at DP frames, drop empty curves
    for fcurve in channelbag.fcurves[:]:
        if fcurve in dp_curves:
            continue  # handled separately below

        is_transform = any(
            fcurve.data_path == prefix + s for s in transform_suffixes
        )
        if not is_transform:
            continue

        for frame in dp_keys:
            for kp in fcurve.keyframe_points[:]:
                if kp.co[0] == frame:
                    fcurve.keyframe_points.remove(kp)

        if not fcurve.keyframe_points:
            channelbag.fcurves.remove(fcurve)

    # Remove DP constraint fcurves
    for fcurve in dp_curves:
        channelbag.fcurves.remove(fcurve)

    # Remove DP constraints
    for con in entity.constraints[:]:
        if con.name.startswith("DP_"):
            entity.constraints.remove(con)
