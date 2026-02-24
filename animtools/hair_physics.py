import bpy
from bpy.props import (
    FloatProperty,
    BoolProperty,
    StringProperty,
    PointerProperty,
    EnumProperty,
)
from bpy.types import PropertyGroup

# ==========================================
# PROPERTIES
# ==========================================

class AT_HairPhysicsProperties(PropertyGroup):
    """Menyimpan semua pengaturan untuk fitur Hair Physics."""
    
    # --- MODE SELECTOR ---
    mode: EnumProperty(
        name="Mode",
        description="Pilih mode: Auto (chain system) atau Manual (target spesifik)",
        items=[
            ('AUTO', "Auto Chain", "Otomatis detect bone chain (tip → root)"),
            ('MANUAL', "Manual Target", "Semua bone track ke 1 target"),
        ],
        default='AUTO',
    )
    
    # --- AUTO MODE SETTINGS ---
    start_value: FloatProperty(
        name="Root Influence",
        description="Nilai influence untuk bone root (paling kecil = loose)",
        default=0.1,
        min=0.0,
        max=1.0,
        step=1,
        precision=2,
    )
    
    end_value: FloatProperty(
        name="Tip Influence",
        description="Nilai influence untuk bone tip (paling besar = stiff)",
        default=1.0,
        min=0.0,
        max=1.0,
        step=1,
        precision=2,
    )
    
    # --- MANUAL MODE SETTINGS ---
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
    
    # --- COLLISION SETTINGS ---
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
        default=0.05,
        min=0.0,
        max=1.0,
        step=1,
        precision=3,
    )
    
    # --- COMMON SETTINGS ---
    track_axis: EnumProperty(
        name="Track Axis",
        description="Sumbu bone yang akan mengarah ke target",
        items=[
            ('TRACK_X',           "X",   "Sumbu X mengarah ke target"),
            ('TRACK_Y',           "Y",   "Sumbu Y mengarah ke target"),
            ('TRACK_Z',           "Z",   "Sumbu Z mengarah ke target"),
            ('TRACK_NEGATIVE_X',  "-X",  "Sumbu -X mengarah ke target"),
            ('TRACK_NEGATIVE_Y',  "-Y",  "Sumbu -Y mengarah ke target"),
            ('TRACK_NEGATIVE_Z',  "-Z",  "Sumbu -Z mengarah ke target"),
        ],
        default='TRACK_Y',
    )

# ==========================================
# CORE LOGIC
# ==========================================

def get_sorted_bones(context):
    """
    Mengembalikan daftar bone yang terseleksi, sudah diurutkan
    berdasarkan index di armature (root → tip secara default).
    """
    selected_bones = context.selected_pose_bones
    if not selected_bones:
        return []

    armature_data = context.active_object.data
    bone_index_map = {bone.name: i for i, bone in enumerate(armature_data.bones)}
    sorted_bones = sorted(selected_bones, key=lambda pb: bone_index_map.get(pb.name, 999))
    
    return sorted_bones

def apply_auto_mode(context, props, op):
    """
    AUTO MODE: Chain system
    - Bone N track ke Bone (N+1)
    - Influence: Root = 0.1 (loose), Tip = 1.0 (stiff)
    """
    bones = get_sorted_bones(context)
    
    if len(bones) < 2:
        op.report({'ERROR'}, "Pilih minimal 2 bone untuk chain system!")
        return {'CANCELLED'}
    
    constraint_name = "HP_DampedTrack"
    applied_count = 0
    num_bones = len(bones)
    
    for i, bone in enumerate(bones):
        # Hitung influence
        if num_bones > 1:
            t = i / (num_bones - 1)
            current_value = props.start_value + t * (props.end_value - props.start_value)
        else:
            current_value = props.start_value
        
        current_value = max(0.0, min(1.0, current_value))
        
        # Hapus constraint lama
        for existing_con in bone.constraints[:]:
            if existing_con.name.startswith("HP_DampedTrack"):
                bone.constraints.remove(existing_con)
        
        # Bone saat ini track ke bone berikutnya (N → N+1)
        if i < num_bones - 1:
            target_bone = bones[i + 1]
            
            con = bone.constraints.new('DAMPED_TRACK')
            con.name = constraint_name
            con.target = context.active_object
            con.subtarget = target_bone.name
            con.track_axis = props.track_axis
            con.influence = current_value
            
            applied_count += 1
    
    op.report({'INFO'}, f"Auto Mode: {applied_count} bone dengan chain system.")
    return {'FINISHED'}

def apply_manual_mode(context, props, op):
    """
    MANUAL MODE: Semua bone track ke 1 target spesifik
    """
    bones = get_sorted_bones(context)
    
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}
    
    if not props.target_object:
        op.report({'ERROR'}, "Target object belum dipilih!")
        return {'CANCELLED'}
    
    target_obj = props.target_object
    target_bone_name = props.target_bone.strip()
    
    # Validasi target bone
    if target_obj.type == 'ARMATURE' and target_bone_name:
        if target_bone_name not in target_obj.data.bones:
            op.report({'ERROR'}, f"Bone '{target_bone_name}' tidak ditemukan!")
            return {'CANCELLED'}
    
    applied_count = 0
    num_bones = len(bones)
    
    for i, bone in enumerate(bones):
        # Hitung influence
        if num_bones > 1:
            t = i / (num_bones - 1)
            current_value = props.start_value + t * (props.end_value - props.start_value)
        else:
            current_value = props.start_value
        
        current_value = max(0.0, min(1.0, current_value))
        
        # Hapus constraint lama
        for existing_con in bone.constraints[:]:
            if existing_con.name.startswith("HP_DampedTrack"):
                bone.constraints.remove(existing_con)
        
        # Semua bone track ke target yang sama
        con = bone.constraints.new('DAMPED_TRACK')
        con.name = constraint_name
        con.target = target_obj
        
        if target_obj.type == 'ARMATURE' and target_bone_name:
            con.subtarget = target_bone_name
        
        con.track_axis = props.track_axis
        con.influence = current_value
        
        applied_count += 1
    
    op.report({'INFO'}, f"Manual Mode: {applied_count} bone track ke target.")
    return {'FINISHED'}

def apply_collision(context, props, bones, op):
    """
    Apply Shrinkwrap constraint untuk collision prevention
    FIX untuk Blender 5.0 API - property yang benar
    """
    if not props.use_collision:
        return 0
    
    if not props.collision_object:
        op.report({'WARNING'}, "Collision object belum dipilih!")
        return 0
    
    collision_obj = props.collision_object
    applied_count = 0
    
    for bone in bones:
        # Hapus collision constraint lama
        for existing_con in bone.constraints[:]:
            if existing_con.name.startswith("HP_Collision"):
                bone.constraints.remove(existing_con)
        
        # Tambah Shrinkwrap constraint
        con = bone.constraints.new('SHRINKWRAP')
        con.name = "HP_Collision"
        con.target = collision_obj
        
        # FIX: Blender 5.0 API - property dan enum yang benar
        con.shrinkwrap_type = 'NEAREST_SURFACE'  # Bukan NEAREST_SURFACEPOINT
        con.wrap_mode = 'OUTSIDE'
        con.distance = props.collision_distance   # Bukan offset!
        con.influence = 1.0
        
        applied_count += 1
    
    return applied_count

def apply_damped_track(context, props, op):
    """Fungsi utama: pilih mode auto atau manual + collision."""
    if context.mode != 'POSE':
        op.report({'ERROR'}, "Harus dalam Pose Mode!")
        return {'CANCELLED'}
    
    bones = get_sorted_bones(context)
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}
    
    # Apply mode yang dipilih
    if props.mode == 'AUTO':
        result = apply_auto_mode(context, props, op)
    else:
        result = apply_manual_mode(context, props, op)
    
    if result == {'CANCELLED'}:
        return result
    
    # Apply collision jika aktif
    if props.use_collision:
        collision_count = apply_collision(context, props, bones, op)
        if collision_count > 0:
            op.report({'INFO'}, f"Collision: {collision_count} bone dengan shrinkwrap.")
    
    return {'FINISHED'}

def clear_hair_physics(context, op):
    """Menghapus semua constraint HP_ (Damped Track + Collision)."""
    if context.mode != 'POSE':
        op.report({'ERROR'}, "Harus dalam Pose Mode!")
        return {'CANCELLED'}
    
    bones = context.selected_pose_bones
    if not bones:
        op.report({'ERROR'}, "Tidak ada bone yang terseleksi!")
        return {'CANCELLED'}
    
    removed_count = 0
    for bone in bones:
        for con in bone.constraints[:]:
            if con.name.startswith("HP_"):
                bone.constraints.remove(con)
                removed_count += 1
    
    op.report({'INFO'}, f"Dihapus: {removed_count} constraint.")
    return {'FINISHED'}

# ==========================================
# OPERATORS
# ==========================================

class AT_OT_ApplyHairPhysics(bpy.types.Operator):
    """Pasang Damped Track constraint"""
    bl_idname = "animtools.apply_hair_physics"
    bl_label = "Apply Hair Physics"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'ARMATURE'
            and context.mode == 'POSE'
        )
    
    def execute(self, context):
        props = context.scene.at_hair_physics
        return apply_damped_track(context, props, self)

class AT_OT_ClearHairPhysics(bpy.types.Operator):
    """Hapus semua Hair Physics constraints"""
    bl_idname = "animtools.clear_hair_physics"
    bl_label = "Clear Hair Physics"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (
            context.active_object is not None
            and context.active_object.type == 'ARMATURE'
            and context.mode == 'POSE'
        )
    
    def execute(self, context):
        return clear_hair_physics(context, self)

# ==========================================
# UI PANEL
# ==========================================

class AT_PT_HairPhysicsPanel(bpy.types.Panel):
    """Panel UI untuk fitur Hair Physics"""
    bl_label = "Hair Physics"
    bl_idname = "AT_PT_HairPhysicsPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AnimTools"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.at_hair_physics
        
        # --- MODE SELECTOR (FIXED - PAKAI BUTTON) ---
        box_mode = layout.box()
        box_mode.label(text="Mode:", icon='PREFERENCES')
        
        # Pakai row dengan button toggle yang jelas
        row_mode = box_mode.row(align=True)
        row_mode.prop(props, "mode", text="Auto Chain", toggle=True)
        row_mode.prop(props, "mode", text="Manual Target", toggle=True)
        
        # --- AUTO MODE SETTINGS ---
        if props.mode == 'AUTO':
            box_auto = layout.box()
            box_auto.label(text="Auto Chain Settings:", icon='AUTO')
            box_auto.label(text="Bone N → track ke Bone N+1", icon='INFO')
            
            box_val = box_auto.box()
            box_val.label(text="Influence (Root → Tip):", icon='DRIVER')
            row = box_val.row(align=True)
            row.prop(props, "start_value", text="Root (Loose)")
            row.prop(props, "end_value", text="Tip (Stiff)")
        
        # --- MANUAL MODE SETTINGS ---
        else:
            box_manual = layout.box()
            box_manual.label(text="Manual Target:", icon='OBJECT_DATA')
            box_manual.prop(props, "target_object", text="Object")
            
            if props.target_object and props.target_object.type == 'ARMATURE':
                box_manual.prop(props, "target_bone", text="Bone")
            
            box_val = box_manual.box()
            box_val.label(text="Influence (Root → Tip):", icon='DRIVER')
            row = box_val.row(align=True)
            row.prop(props, "start_value", text="Root")
            row.prop(props, "end_value", text="Tip")
        
        # --- COLLISION SETTINGS ---
        box_collision = layout.box()
        box_collision.label(text="Collision Prevention:", icon='MOD_PHYSICS')
        box_collision.prop(props, "use_collision", text="Enable Collision")
        
        if props.use_collision:
            box_col_settings = box_collision.box()
            box_col_settings.prop(props, "collision_object", text="Object")
            box_col_settings.prop(props, "collision_distance", text="Distance")
            box_col_settings.label(text="Tip: Pilih body/cloth mesh", icon='INFO')
        
        # --- COMMON SETTINGS ---
        box_common = layout.box()
        box_common.label(text="Common Settings:", icon='SETTINGS')
        box_common.prop(props, "track_axis", text="Track Axis")
        
        # --- PREVIEW ---
        if context.selected_pose_bones:
            num_bones = len(context.selected_pose_bones)
            box_preview = layout.box()
            box_preview.label(text=f"Preview ({num_bones} bones):", icon='INFO')
            
            col_preview = box_preview.column(align=True)
            col_preview.scale_y = 0.7
            
            bones = get_sorted_bones(context)
            preview_limit = min(len(bones), 5)
            
            for i in range(preview_limit):
                if len(bones) > 1:
                    t = i / (len(bones) - 1)
                    val = props.start_value + t * (props.end_value - props.start_value)
                else:
                    val = props.start_value
                
                val = max(0.0, min(1.0, val))
                
                if props.mode == 'AUTO' and i < len(bones) - 1:
                    target_name = bones[i+1].name
                    col_preview.label(text=f"  {bones[i].name} → {target_name}: {val:.2f}", icon='BLANK1')
                else:
                    col_preview.label(text=f"  {bones[i].name}: {val:.2f}", icon='BLANK1')
            
            if num_bones > 5:
                col_preview.label(text=f"  ... dan {num_bones - 5} bone lainnya")
        
        # --- ACTION BUTTONS ---
        layout.separator()
        col_btn = layout.column(align=True)
        col_btn.scale_y = 1.4
        col_btn.operator("animtools.apply_hair_physics", text="Apply Hair Physics", icon='FORCE_WIND')
        layout.operator("animtools.clear_hair_physics", text="Clear Hair Physics", icon='X')

# ==========================================
# CLASSES
# ==========================================

classes = (
    AT_HairPhysicsProperties,
    AT_OT_ApplyHairPhysics,
    AT_OT_ClearHairPhysics,
    AT_PT_HairPhysicsPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.at_hair_physics = PointerProperty(type=AT_HairPhysicsProperties)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.at_hair_physics
