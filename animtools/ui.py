import bpy
from .core import (
    get_active_entity, 
    get_world_matrix, 
    set_world_matrix, 
    toggle_child_of_logic,
    dynamic_parent_create,
    dynamic_parent_disable,
    dynamic_parent_clear,
    check_constraint_keyframe,
    AT_CLIPBOARD_MATRIX
)

# --- OPERATORS ---

class AT_OT_CopyWorld(bpy.types.Operator):
    bl_idname = "animtools.copy_world"
    bl_label = "Copy World"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        global AT_CLIPBOARD_MATRIX
        entity, is_bone = get_active_entity(context)
        if not entity:
            self.report({'WARNING'}, "Nothing selected")
            return {'CANCELLED'}
        AT_CLIPBOARD_MATRIX = get_world_matrix(entity, is_bone)
        self.report({'INFO'}, f"Copied transform")
        return {'FINISHED'}

class AT_OT_PasteWorld(bpy.types.Operator):
    bl_idname = "animtools.paste_world"
    bl_label = "Paste World"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return AT_CLIPBOARD_MATRIX is not None

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            return {'CANCELLED'}
        set_world_matrix(entity, is_bone, AT_CLIPBOARD_MATRIX)
        self.report({'INFO'}, f"Pasted transform")
        return {'FINISHED'}

class AT_OT_ToggleConstraint(bpy.types.Operator):
    bl_idname = "animtools.toggle_constraint"
    bl_label = "Toggle Constraint"
    bl_options = {'REGISTER', 'UNDO'}
    
    constraint_name: bpy.props.StringProperty(default="")

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            return {'CANCELLED'}
        toggle_child_of_logic(entity, is_bone, self.constraint_name)
        return {'FINISHED'}

# --- DYNAMIC PARENT OPERATORS ---

class AT_OT_DynamicParentCreate(bpy.types.Operator):
    bl_idname = "animtools.dp_create"
    bl_label = "Create"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        dynamic_parent_create(context, self)
        return {'FINISHED'}

class AT_OT_DynamicParentDisable(bpy.types.Operator):
    bl_idname = "animtools.dp_disable"
    bl_label = "Disable"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        entity, is_bone = get_active_entity(context)
        if entity:
            for con in entity.constraints:
                if con.name.startswith("DP_") and con.influence > 0.5:
                    return True
        return False

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            return {'CANCELLED'}
        
        con = None
        for c in entity.constraints:
            if c.name.startswith("DP_") and c.influence > 0.5:
                con = c
                break
        
        if con:
            dynamic_parent_disable(context, entity, is_bone, con)
            self.report({'INFO'}, "Disabled Dynamic Parent")
        
        return {'FINISHED'}

class AT_OT_DynamicParentClear(bpy.types.Operator):
    bl_idname = "animtools.dp_clear"
    bl_label = "Clear Dynamic Parent"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if entity:
            dynamic_parent_clear(entity, is_bone)
            self.report({'INFO'}, "Cleared Dynamic Parent data")
        return {'FINISHED'}

# --- PANEL ---

class AT_PT_MainPanel(bpy.types.Panel):
    bl_label = "AnimTools"
    bl_idname = "AT_PT_MainPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "AnimTools"
    
    def draw(self, context):
        layout = self.layout
        entity, is_bone = get_active_entity(context)
        scene = context.scene
        
        # --- Dynamic Parent Section ---
        box_dp = layout.box()
        box_dp.label(text="Dynamic Parent:", icon='ARMATURE_DATA')
        row = box_dp.row(align=True)
        row.operator("animtools.dp_create", text="Create")
        row.operator("animtools.dp_disable", text="Disable")
        
        row_menu = box_dp.row()
        row_menu.operator("animtools.dp_clear", text="Clear", icon='X')
        
        layout.separator()

        # --- Transform Section ---
        box = layout.box()
        row = box.row(align=True)
        row.operator("animtools.copy_world", text="Copy World")
        row.operator("animtools.paste_world", text="Paste World")
        
        # --- Constraint Section ---
        box_c = layout.box()
        box_c.label(text="Constraints:", icon='CONSTRAINT')
        
        if not entity:
            box_c.label(text="No active object/bone")
            return
            
        constraints = entity.constraints if hasattr(entity, 'constraints') else []
        found_childof = False
        
        for con in constraints:
            if con.type == 'CHILD_OF':
                found_childof = True
                # Icon Toggle
                toggle_icon = 'CHECKBOX_HLT' if con.influence > 0.5 else 'CHECKBOX_DEHLT'
                
                row = box_c.row(align=True)
                
                # Label Name
                target_name = con.target.name
                if con.subtarget:
                    target_name = f"{con.target.name}/{con.subtarget}"
                
                # Indicator if DP
                if con.name.startswith("DP_"):
                    row.label(text="", icon='LIBRARY_DATA_DIRECT')
                else:
                    row.label(text="", icon='BLANK1')
                
                # Toggle Button
                toggle_op = row.operator("animtools.toggle_constraint", text="", icon=toggle_icon, emboss=False)
                toggle_op.constraint_name = con.name
                
                row.label(text=f"{con.name} ({target_name})")
                
                # --- FITUR No. 3: KEYFRAME DETECTION ---
                # Cek apakah ada keyframe di frame saat ini
                kf_icon = check_constraint_keyframe(entity, is_bone, con.name, scene.frame_current)
                
                # Tampilkan ikon di sebelah kanan
                # Jika ada keyframe, tampilkan ikon KEYFRAME
                # Jika ada animasi tapi bukan di frame ini, tampilkan DECORATE_ANIMATE
                if kf_icon != 'NONE':
                    row.label(text="", icon=kf_icon)
                else:
                    row.label(text="", icon='BLANK1')
        
        if not found_childof:
            box_c.label(text="No Child Of constraints")

# --- CLASSES LIST (INI YANG TADI HILANG) ---
classes = (
    AT_OT_CopyWorld,
    AT_OT_PasteWorld,
    AT_OT_ToggleConstraint,
    AT_OT_DynamicParentCreate,
    AT_OT_DynamicParentDisable,
    AT_OT_DynamicParentClear,
    AT_PT_MainPanel,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
