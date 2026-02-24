import bpy
import math
from mathutils import Matrix
from typing import Tuple, Union, Optional
# Import khusus untuk Blender 5.0+ Animation System
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
    data_paths = ["location", f"rotation_{get_rotation_mode(entity)}", "scale"]
    if is_bone:
        armature = entity.id_data
        for dp in data_paths:
            armature.keyframe_insert(data_path=f'pose.bones["{entity.name}"].{dp}', frame=frame)
    else:
        for dp in data_paths:
            entity.keyframe_insert(data_path=dp, frame=frame)

def insert_keyframe_constraint(entity, is_bone: bool, constraint_name: str, frame: int):
    if is_bone:
        armature = entity.id_data
        armature.keyframe_insert(
            data_path=f'pose.bones["{entity.name}"].constraints["{constraint_name}"].influence', 
            frame=frame
        )
    else:
        entity.keyframe_insert(data_path=f'constraints["{constraint_name}"].influence', frame=frame)

# --- Helper untuk Fitur No. 3: Check Keyframe ---
def check_constraint_keyframe(entity, is_bone: bool, constraint_name: str, frame: int):
    """
    Return icon string if keyframe exists on influence at specific frame.
    Support Blender 5.0 (Slotted Actions).
    """
    # 1. Dapatkan ID Data (Object atau Armature)
    id_data = entity.id_data if is_bone else entity
    
    if not id_data.animation_data or not id_data.animation_data.action:
        return 'NONE'

    action = id_data.animation_data.action
    slot = id_data.animation_data.action_slot
    
    if not slot:
        return 'NONE'

    # 2. Akses Channelbag (Blender 5.0 way)
    # Kita perlu mencari channelbag yang sesuai dengan slot
    channelbag = None
    # Blender 5.0 API: action.layers... strips... channelbag
    # Cara aman untuk membaca:
    try:
        # Karena API masih baru, kita gunakan helper bawaan jika ada, atau manual search
        # Namun untuk membaca, kita bisa iterate fcurves lama jika ada, atau pakai cara baru
        # Di Blender 5.0, fcurves bisa diakses via: action.slots[...].channels ??
        # Cara paling stabil di 5.0 awal adalah:
        bag = action_ensure_channelbag_for_slot(action, slot) # Ini akan membuat jika tidak ada, tapi ok untuk read
        # Alternatif read-only tidak ada di public API stabil, jadi kita pakai helper resmi
    except:
        return 'NONE'
        
    # 3. Tentukan Data Path
    if is_bone:
        data_path = f'pose.bones["{entity.name}"].constraints["{constraint_name}"].influence'
    else:
        data_path = f'constraints["{constraint_name}"].influence'

    # 4. Cari Fcurve di dalam bag
    # Di Blender 5.0, fcurves ada di dalam channelbag
    # Perhatian: API mungkin berubah, tapi biasanya .fcurves bisa diakses
    # Jika tidak, kita kembalikan NONE
    
    # Cara backward compatible (aman)
    fcurves = bag.fcurves if hasattr(bag, 'fcurves') else []
    
    for fcu in fcurves:
        if fcu.data_path == data_path:
            for kp in fcu.keyframe_points:
                if int(kp.co[0]) == frame:
                    return 'KEYFRAME' # Ikon keyframe
            return 'DECORATE_ANIMATE' # Ikon ada animasi, tapi bukan di frame ini
            
    return 'NONE'

# ==========================================
# TOGGLE LOGIC
# ==========================================

def toggle_child_of_logic(entity, is_bone: bool, constraint_name: str):
    con = entity.constraints.get(constraint_name)
    if not con or con.type != 'CHILD_OF':
        return

    # === JARING PENGAMAN ===
    # Jika constraint tidak punya target, toggle saja tanpa kalkulasi matrix
    if not con.target:
        con.influence = 0.0 if con.influence > 0.5 else 1.0
        return

    # Simpan posisi dunia SEBELUM apapun diubah
    current_world_mat = get_world_matrix(entity, is_bone)
    new_influence = 0.0 if con.influence > 0.5 else 1.0
    con.influence = new_influence

    if new_influence == 0.0:
        # === TOGGLE OFF ===
        # Constraint dimatikan, kembalikan posisi ke posisi dunia semula
        set_world_matrix(entity, is_bone, current_world_mat)

    else:
        # === TOGGLE ON ===
        # Kita perlu menghitung: "matrix lokal berapa yang, setelah constraint aktif,
        # menghasilkan posisi dunia yang SAMA seperti sebelumnya?"
        #
        # Rumusnya:
        # world = effective_parent @ local
        # Maka: local = effective_parent.inverted() @ world

        # Dapatkan matrix dunia dari parent target
        parent_world = con.target.matrix_world.copy()
        if con.subtarget and con.target.type == 'ARMATURE':
            p_bone = con.target.pose.bones.get(con.subtarget)
            if p_bone:
                parent_world = parent_world @ p_bone.matrix

        # "Effective parent" = posisi parent di dunia, dikoreksi oleh inverse matrix
        # (inverse matrix adalah "titik nol" yang direkam saat Set Inverse dijalankan)
        effective_parent = parent_world @ con.inverse_matrix

        # Hitung matrix lokal yang kita butuhkan
        required_local = effective_parent.inverted_safe() @ current_world_mat

        # Terapkan
        set_world_matrix(entity, is_bone, required_local)

# ==========================================
# DYNAMIC PARENT LOGIC
# ==========================================

def get_selected_parent(context):
    active_obj = context.active_object
    selected_objs = context.selected_objects
    
    if active_obj and active_obj.mode == 'OBJECT':
        for obj in selected_objs:
            if obj != active_obj:
                return obj, None
    
    if active_obj and active_obj.mode == 'POSE':
        active_bone = context.active_pose_bone
        selected_bones = context.selected_pose_bones
        
        for bone in selected_bones:
            if bone != active_bone:
                return active_obj, bone.name
        
        for obj in selected_objs:
            if obj != active_obj:
                if obj.type == 'ARMATURE' and obj.data.bones.active:
                    return obj, obj.data.bones.active.name
                return obj, None

    return None, None

def dynamic_parent_create(context, op):
    entity, is_bone = get_active_entity(context)
    if not entity:
        op.report({'ERROR'}, "Nothing selected.")
        return
    
    last_con = None
    for con in entity.constraints:
        if con.name.startswith("DP_") and con.influence == 1.0:
            last_con = con
            break
    
    current_frame = context.scene.frame_current
    
    if last_con:
        dynamic_parent_disable(context, entity, is_bone, last_con)

    parent_obj, parent_bone_name = get_selected_parent(context)
    if not parent_obj:
        op.report({'ERROR'}, "Select a target parent (object or bone).")
        return

    insert_keyframe_transform(entity, is_bone, current_frame)

    con = entity.constraints.new('CHILD_OF')
    dp_name = f"DP_{parent_obj.name}"
    if parent_bone_name:
        dp_name += f".{parent_bone_name}"
    con.name = dp_name
    
    con.target = parent_obj
    if parent_bone_name and parent_obj.type == 'ARMATURE':
        con.subtarget = parent_bone_name

    # SET INVERSE FIX
    try:
        if is_bone:
            armature = entity.id_data
            with context.temp_override(object=armature, active_pose_bone=entity):
                bpy.ops.constraint.childof_set_inverse(constraint=con.name, owner='BONE')
        else:
            with context.temp_override(object=entity):
                bpy.ops.constraint.childof_set_inverse(constraint=con.name, owner='OBJECT')
    except Exception as e:
        print(f"Error Set Inverse: {e}")
        child_world = get_world_matrix(entity, is_bone)
        parent_world = parent_obj.matrix_world.copy()
        if parent_bone_name:
             p_bone = parent_obj.pose.bones.get(parent_bone_name)
             if p_bone:
                parent_world = parent_world @ p_bone.matrix
        con.inverse_matrix = parent_world.inverted_safe() @ child_world

    prev_frame = current_frame - 1
    
    con.influence = 0.0
    insert_keyframe_constraint(entity, is_bone, con.name, prev_frame)
    
    con.influence = 1.0
    insert_keyframe_constraint(entity, is_bone, con.name, current_frame)
    
    op.report({'INFO'}, f"Created Dynamic Parent: {con.name}")

def dynamic_parent_disable(context, entity, is_bone, con):
    current_frame = context.scene.frame_current
    prev_frame = current_frame - 1
    
    con.influence = 1.0
    insert_keyframe_constraint(entity, is_bone, con.name, prev_frame)
    
    insert_keyframe_transform(entity, is_bone, prev_frame)
    
    con.influence = 0.0
    insert_keyframe_constraint(entity, is_bone, con.name, current_frame)
    
    insert_keyframe_transform(entity, is_bone, current_frame)

def dynamic_parent_clear(entity, is_bone):
    frames_to_clean = []
    id_data = entity.id_data if is_bone else entity
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
    
    dp_curves = []
    for fcurve in channelbag.fcurves:
        if "DP_" in fcurve.data_path:
            dp_curves.append(fcurve)
            for kp in fcurve.keyframe_points:
                frames_to_clean.append(int(kp.co[0]))
    
    frames_to_clean = list(set(frames_to_clean))
    
    prefix = f'pose.bones["{entity.name}"].' if is_bone else ""
    paths = ["location", "rotation_euler", "rotation_quaternion", "scale"]
    
    for fcurve in channelbag.fcurves[:]:
        is_transform = False
        for p in paths:
            if prefix + p in fcurve.data_path:
                is_transform = True
                break
        
        if is_transform:
            for kp in fcurve.keyframe_points[:]:
                if int(kp.co[0]) in frames_to_clean:
                    fcurve.keyframe_points.remove(kp)
            
            if len(fcurve.keyframe_points) == 0:
                channelbag.fcurves.remove(fcurve)

    for fcurve in dp_curves:
        channelbag.fcurves.remove(fcurve)

    for con in entity.constraints[:]:
        if con.name.startswith("DP_"):
            entity.constraints.remove(con)
