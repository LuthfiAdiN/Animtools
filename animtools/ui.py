import bpy
from .core import (
    get_active_entity,
    get_world_matrix,
    set_world_matrix,
    toggle_child_of_logic,
    dynamic_parent_create,
    dynamic_parent_disable,
    dynamic_parent_clear,
    get_last_dynamic_parent_constraint,
    get_selected_objects,
    check_constraint_keyframe,
    AT_CLIPBOARD_MATRIX,
)

# ==========================================
# TRANSFORM OPERATORS
# ==========================================

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
        self.report({'INFO'}, "Copied transform")
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
        self.report({'INFO'}, "Pasted transform")
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

# ==========================================
# DYNAMIC PARENT OPERATORS
# ==========================================

class AT_OT_DynamicParentCreate(bpy.types.Operator):
    """Create a new animated Child Of constraint"""
    bl_idname = "animtools.dp_create"
    bl_label = "Create"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "POSE")

    def execute(self, context):
        dynamic_parent_create(context, self)
        return {'FINISHED'}


class AT_OT_DynamicParentDisable(bpy.types.Operator):
    """Disable the current animated Child Of constraint"""
    bl_idname = "animtools.dp_disable"
    bl_label = "Disable"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "POSE")

    def execute(self, context):
        """
        Iterates ALL selected objects/bones and disables their last DP constraint.
        Mirrors DYNAMIC_PARENT_OT_disable.execute() from dynamic_parent.py.
        """
        objects = get_selected_objects(context)
        counter = 0

        if not objects:
            self.report({'ERROR'}, "Nothing selected.")
            return {'CANCELLED'}

        for entity in objects:
            if entity is None:
                continue
            # Determine is_bone
            is_bone = isinstance(entity, bpy.types.PoseBone)
            const = get_last_dynamic_parent_constraint(entity)
            if const is None:
                continue
            dynamic_parent_disable(context, entity, is_bone, const)
            counter += 1

        if counter == 0:
            self.report({'WARNING'}, "No active DP_ constraint found on selection.")
            return {'CANCELLED'}

        self.report({'INFO'}, f"{counter} constraint(s) disabled.")
        return {'FINISHED'}


class AT_OT_DynamicParentClear(bpy.types.Operator):
    """Clear all Dynamic Parent constraints and their keyframes"""
    bl_idname = "animtools.dp_clear"
    bl_label = "Clear"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "POSE")

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            self.report({'ERROR'}, "Nothing selected.")
            return {'CANCELLED'}
        dynamic_parent_clear(entity, is_bone)
        self.report({'INFO'}, "Cleared Dynamic Parent data")
        return {'FINISHED'}


class AT_OT_DynamicParentBake(bpy.types.Operator):
    """Bake Dynamic Parent animation then remove DP constraints"""
    bl_idname = "animtools.dp_bake"
    bl_label = "Bake and Clear"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in ("OBJECT", "POSE")

    def execute(self, context):
        """
        Mirrors DYNAMIC_PARENT_OT_bake.execute() from dynamic_parent.py.
        """
        obj = context.active_object
        scn = context.scene

        if obj.type == 'ARMATURE':
            bpy.ops.nla.bake(
                frame_start=scn.frame_start,
                frame_end=scn.frame_end,
                step=1,
                only_selected=True,
                visual_keying=True,
                clear_constraints=False,
                clear_parents=False,
                bake_types={'POSE'},
            )
            target = context.active_pose_bone
        else:
            bpy.ops.nla.bake(
                frame_start=scn.frame_start,
                frame_end=scn.frame_end,
                step=1,
                only_selected=True,
                visual_keying=True,
                clear_constraints=False,
                clear_parents=False,
                bake_types={'OBJECT'},
            )
            target = obj

        for con in target.constraints[:]:
            if con.name.startswith("DP_"):
                target.constraints.remove(con)

        self.report({'INFO'}, "Baked and cleared Dynamic Parent constraints")
        return {'FINISHED'}


class AT_MT_DynamicParentClearMenu(bpy.types.Menu):
    """Clear or bake Dynamic Parent constraints"""
    bl_label = "Clear Dynamic Parent?"
    bl_idname = "AT_MT_DynamicParentClearMenu"

    def draw(self, context):
        layout = self.layout
        layout.operator("animtools.dp_clear", text="Clear", icon='X')
        layout.operator("animtools.dp_bake", text="Bake and Clear", icon='REC')

# ==========================================
# MAIN PANEL
# ==========================================

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

        col_dp = box_dp.column(align=True)
        col_dp.operator("animtools.dp_create", text="Create", icon='KEY_HLT')
        col_dp.operator("animtools.dp_disable", text="Disable", icon='KEY_DEHLT')
        col_dp.menu("AT_MT_DynamicParentClearMenu", text="Clear", icon='TRASH')

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
                toggle_icon = 'CHECKBOX_HLT' if con.influence > 0.5 else 'CHECKBOX_DEHLT'

                row = box_c.row(align=True)

                target_name = con.target.name if con.target else "(none)"
                if con.subtarget:
                    target_name = f"{target_name}/{con.subtarget}"

                # DP indicator icon
                if con.name.startswith("DP_"):
                    row.label(text="", icon='LIBRARY_DATA_DIRECT')
                else:
                    row.label(text="", icon='BLANK1')

                # Toggle button
                toggle_op = row.operator(
                    "animtools.toggle_constraint", text="", icon=toggle_icon, emboss=False
                )
                toggle_op.constraint_name = con.name

                row.label(text=f"{con.name} ({target_name})")

                # Keyframe detection indicator
                kf_icon = check_constraint_keyframe(
                    entity, is_bone, con.name, scene.frame_current
                )
                row.label(text="", icon=kf_icon if kf_icon != 'NONE' else 'BLANK1')

        if not found_childof:
            box_c.label(text="No Child Of constraints")

# ==========================================
# CLASSES
# ==========================================

classes = (
    AT_OT_CopyWorld,
    AT_OT_PasteWorld,
    AT_OT_ToggleConstraint,
    AT_OT_DynamicParentCreate,
    AT_OT_DynamicParentDisable,
    AT_OT_DynamicParentClear,
    AT_OT_DynamicParentBake,
    AT_MT_DynamicParentClearMenu,
    AT_PT_MainPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
