import bpy
from bpy.props import (
    FloatProperty,
    BoolProperty,
    StringProperty,
    PointerProperty,
    EnumProperty,
)
from bpy.types import PropertyGroup

# Pose-bone flag: "Inherit Rotation was turned off by Hair Physics" (restored on Clear)
_INH_KEY = "at_hp_inherit_rot"


# ==========================================
# PROPERTIES
# ==========================================
class AT_HairPhysicsProperties(PropertyGroup):
    """Menyimpan semua pengaturan untuk fitur Hair Physics."""

    mode: EnumProperty(
        name="Mode",
        description="Pilih mode: Auto (chain system) atau Manual (target spesifik)",
        items=[
            ('AUTO',   "Auto Chain",    "Otomatis detect bone chain (tip → root)"),
            ('MANUAL', "Manual Target", "Semua bone track ke 1 target"),
        ],
        default='AUTO',
    )
    start_value: FloatProperty(
        name="Root Influence",
        description="Nilai influence untuk bone root (paling kecil = loose)",
        default=0.1, min=0.0, max=1.0, step=1, precision=2,
    )
    end_value: FloatProperty(
        name="Tip Influence",
        description="Nilai influence untuk bone tip (paling besar = stiff)",
        default=1.0, min=0.0, max=1.0, step=1, precision=2,
    )
    target_object: PointerProperty(
        name="Target Object",
        description="Objek yang akan di-track oleh Damped Track constraint",
        type=bpy.types.Object,
    )
    target_bone: StringProperty(
        name="Target Bone",
        description="Nama bone target (isi jika target adalah Armature)",
        default="",
    )
    use_collision: BoolProperty(
        name="Use Collision",
        description="Aktifkan collision agar hair tidak tembus badan/baju",
        default=False,
    )
    collision_object: PointerProperty(
        name="Collision Object",
        description="Objek yang akan dijadikan collision (badan/baju)",
        type=bpy.types.Object,
    )
    collision_distance: FloatProperty(
        name="Distance",
        description="Jarak minimum antara hair dan collision object",
        default=0.05, min=0.0, max=1.0, step=1, precision=3,
    )
    track_axis: EnumProperty(
        name="Track Axis",
        description="Sumbu bone yang akan mengarah ke target",
        items=[
            ('TRACK_X',          "X",  "Sumbu X"),
            ('TRACK_Y',          "Y",  "Sumbu Y"),
            ('TRACK_Z',          "Z",  "Sumbu Z"),
            ('TRACK_NEGATIVE_X', "-X", "Sumbu -X"),
            ('TRACK_NEGATIVE_Y', "-Y", "Sumbu -Y"),
            ('TRACK_NEGATIVE_Z', "-Z", "Sumbu -Z"),
        ],
        default='TRACK_Y',
    )


# ==========================================
# CORE LOGIC
# ==========================================
def get_sorted_bones(context):
    selected = context.selected_pose_bones
    if not selected:
        return []
    index_map = {b.name: i for i, b in enumerate(context.active_object.data.bones)}
    return sorted(selected, key=lambda pb: index_map.get(pb.name, 999))


def influence_at(props, i, n):
    """Influence of bone i out of n: linear Root -> Tip."""
    t = i / (n - 1) if n > 1 else 0.0
    return max(0.0, min(1.0, props.start_value + t * (props.end_value - props.start_value)))


def _remove_constraints(bone, prefix):
    for con in bone.constraints[:]:
        if con.name.startswith(prefix):
            bone.constraints.remove(con)


def _disable_inherit_rotation(pose_bone):
    """A Damped Track chain does nothing while the target inherits rotation: turn it off, remember it."""
    if pose_bone.bone.use_inherit_rotation:
        pose_bone.bone.use_inherit_rotation = False
        pose_bone[_INH_KEY] = True


def _restore_inherit_rotation(pose_bone):
    if pose_bone.get(_INH_KEY):
        pose_bone.bone.use_inherit_rotation = True
        del pose_bone[_INH_KEY]


def apply_auto_mode(context, props, op):
    bones = get_sorted_bones(context)
    if len(bones) < 2:
        op.report({'ERROR'}, "Pilih minimal 2 bone untuk chain system!")
        return {'CANCELLED'}

    n = len(bones)
    applied = 0
    for i, bone in enumerate(bones):
        _remove_constraints(bone, "HP_DampedTrack")
        if i < n - 1:
            con = bone.constraints.new('DAMPED_TRACK')
            con.name = "HP_DampedTrack"
            con.target = context.active_object
            con.subtarget = bones[i + 1].name
            con.track_axis = props.track_axis
            con.influence = influence_at(props, i, n)
            _disable_inherit_rotation(bones[i + 1])
            applied += 1

    op.report({'INFO'}, f"Auto Mode: {applied} bone dengan chain system.")
    return {'FINISHED'}


def apply_manual_mode(context, props, op):
    bones = get_sorted_bones(context)
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}
    if not props.target_object:
        op.report({'ERROR'}, "Target object belum dipilih!")
        return {'CANCELLED'}

    target_obj = props.target_object
    target_bone_name = props.target_bone.strip()
    if target_obj.type == 'ARMATURE' and target_bone_name:
        if target_bone_name not in target_obj.data.bones:
            op.report({'ERROR'}, f"Bone '{target_bone_name}' tidak ditemukan!")
            return {'CANCELLED'}

    n = len(bones)
    for i, bone in enumerate(bones):
        _remove_constraints(bone, "HP_DampedTrack")
        con = bone.constraints.new('DAMPED_TRACK')
        con.name = "HP_DampedTrack"
        con.target = target_obj
        if target_obj.type == 'ARMATURE' and target_bone_name:
            con.subtarget = target_bone_name
        con.track_axis = props.track_axis
        con.influence = influence_at(props, i, n)

    op.report({'INFO'}, f"Manual Mode: {n} bone track ke target.")
    return {'FINISHED'}


def apply_collision(context, props, bones, op):
    if not props.use_collision:
        return 0
    if not props.collision_object:
        op.report({'WARNING'}, "Collision object belum dipilih!")
        return 0

    for bone in bones:
        _remove_constraints(bone, "HP_Collision")
        con = bone.constraints.new('SHRINKWRAP')
        con.name = "HP_Collision"
        con.target = props.collision_object
        con.shrinkwrap_type = 'NEAREST_SURFACE'
        con.wrap_mode = 'OUTSIDE'
        con.distance = props.collision_distance
        con.influence = 1.0
    return len(bones)


def apply_damped_track(context, props, op):
    if context.mode != 'POSE':
        op.report({'ERROR'}, "Harus dalam Pose Mode!")
        return {'CANCELLED'}

    bones = get_sorted_bones(context)
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}

    result = apply_auto_mode(context, props, op) if props.mode == 'AUTO' else apply_manual_mode(context, props, op)
    if result == {'CANCELLED'}:
        return result

    if props.use_collision:
        count = apply_collision(context, props, bones, op)
        if count:
            op.report({'INFO'}, f"Collision: {count} bone dengan shrinkwrap.")
    return {'FINISHED'}


def clear_hair_physics(context, op):
    if context.mode != 'POSE':
        op.report({'ERROR'}, "Harus dalam Pose Mode!")
        return {'CANCELLED'}

    bones = context.selected_pose_bones
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}

    removed = 0
    for bone in bones:
        for con in bone.constraints[:]:
            if con.name.startswith("HP_"):
                bone.constraints.remove(con)
                removed += 1
        _restore_inherit_rotation(bone)

    op.report({'INFO'}, f"Dihapus: {removed} constraint.")
    return {'FINISHED'}


# ==========================================
# OPERATORS
# ==========================================
class _PoseArmaturePoll:
    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE' and context.mode == 'POSE'


class AT_OT_ApplyHairPhysics(_PoseArmaturePoll, bpy.types.Operator):
    """Pasang Damped Track constraint pada bone yang terseleksi"""
    bl_idname = "animtools.apply_hair_physics"
    bl_label = "Apply Hair Physics"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return apply_damped_track(context, context.scene.at_hair_physics, self)


class AT_OT_ClearHairPhysics(_PoseArmaturePoll, bpy.types.Operator):
    """Hapus semua Hair Physics constraints (HP_) dari bone terseleksi"""
    bl_idname = "animtools.clear_hair_physics"
    bl_label = "Clear Hair Physics"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return clear_hair_physics(context, self)


# ==========================================
# REGISTER (the panel lives in ui.py)
# ==========================================
classes = (
    AT_HairPhysicsProperties,
    AT_OT_ApplyHairPhysics,
    AT_OT_ClearHairPhysics,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.at_hair_physics = PointerProperty(type=AT_HairPhysicsProperties)


def unregister():
    del bpy.types.Scene.at_hair_physics
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
