import bpy
from . import core
from . import library
from . import rotation_mode
from .core import (
    get_active_entity,
    get_world_matrix,
    set_world_matrix,
    toggle_child_of_logic,
    dynamic_parent_create,
    dynamic_parent_disable,
    dynamic_parent_clear,
    check_constraint_keyframe,
)
from .hair_physics import get_sorted_bones, influence_at
from .playblast import draw_settings as draw_playblast_settings
from .parent_rig import is_rig

# Sidebar tab of the 3D Viewport. Change this one line to move every AnimTools panel.
PANEL_CATEGORY = "Animation"


# ==========================================
# OPERATORS
# ==========================================
class AT_OT_CopyWorld(bpy.types.Operator):
    bl_idname = "animtools.copy_world"
    bl_label = "Copy World Transform"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            self.report({'WARNING'}, "Nothing selected")
            return {'CANCELLED'}
        core.clipboard_set(get_world_matrix(entity, is_bone))
        self.report({'INFO'}, "Copied world transform")
        return {'FINISHED'}


class AT_OT_PasteWorld(bpy.types.Operator):
    bl_idname = "animtools.paste_world"
    bl_label = "Paste World Transform"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return core.clipboard_get() is not None

    def execute(self, context):
        entity, is_bone = get_active_entity(context)
        if not entity:
            return {'CANCELLED'}
        set_world_matrix(entity, is_bone, core.clipboard_get())
        self.report({'INFO'}, "Pasted world transform")
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


class AT_OT_DynamicParentCreate(bpy.types.Operator):
    bl_idname = "animtools.dp_create"
    bl_label = "Create Dynamic Parent"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        return {'FINISHED'} if dynamic_parent_create(context, self) else {'CANCELLED'}


class AT_OT_DynamicParentDisable(bpy.types.Operator):
    bl_idname = "animtools.dp_disable"
    bl_label = "Disable Dynamic Parent"
    bl_description = "Switch off the Dynamic Parent of ALL selected objects/bones without moving them"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(e is not None and core.active_dp(e) for e in core.get_selected_objects(context))

    def execute(self, context):
        count = 0
        for entity in core.get_selected_objects(context):
            if entity is None:
                continue
            con = core.active_dp(entity)
            if con is None:
                continue
            dynamic_parent_disable(context, entity, isinstance(entity, bpy.types.PoseBone), con)
            count += 1
        if count == 0:
            self.report({'WARNING'}, "No active Dynamic Parent on the selection.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Disabled {count} Dynamic Parent(s)")
        return {'FINISHED'}


class AT_OT_DynamicParentClear(bpy.types.Operator):
    bl_idname = "animtools.dp_clear"
    bl_label = "Clear"
    bl_description = "Remove the Dynamic Parent constraints and the keys made for them on all selected objects/bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'}

    def execute(self, context):
        count = 0
        for entity in core.get_selected_objects(context):
            if entity is None or not any(c.name.startswith("DP_") for c in entity.constraints):
                continue
            dynamic_parent_clear(entity, isinstance(entity, bpy.types.PoseBone))
            count += 1
        if count == 0:
            self.report({'WARNING'}, "No Dynamic Parent on the selection.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Cleared Dynamic Parent on {count} controller(s)")
        return {'FINISHED'}


class AT_OT_DynamicParentBake(bpy.types.Operator):
    bl_idname = "animtools.dp_bake"
    bl_label = "Bake and Clear"
    bl_description = ("Bake what the Dynamic Parent does into normal keys over the playback range, "
                      "then remove the constraints (selected controllers that have one)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'}

    def execute(self, context):
        return {'FINISHED'} if core.dynamic_parent_bake_clear(context, self) else {'CANCELLED'}


class AT_MT_DynamicParentClearMenu(bpy.types.Menu):
    bl_label = "Clear Dynamic Parent?"
    bl_idname = "AT_MT_DynamicParentClearMenu"

    def draw(self, context):
        layout = self.layout
        layout.operator("animtools.dp_clear", text="Clear", icon='X')
        layout.operator("animtools.dp_bake", text="Bake and Clear", icon='REC')


# ==========================================
# PANELS
# ==========================================
class _SidebarPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = PANEL_CATEGORY


class _SubPanel(_SidebarPanel):
    bl_parent_id = "AT_PT_MainPanel"
    bl_options = {'DEFAULT_CLOSED'}


class AT_PT_MainPanel(_SidebarPanel, bpy.types.Panel):
    bl_label = "AnimTools"
    bl_idname = "AT_PT_MainPanel"

    def draw(self, context):
        self.layout.use_property_split = False
        self.layout.use_property_decorate = False


class AT_PT_DynamicParentPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Dynamic Parent"
    bl_idname = "AT_PT_DynamicParentPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='ARMATURE_DATA')

    def draw(self, context):
        layout = self.layout
        entity, _ = get_active_entity(context)

        active_dp = None
        if entity:
            active_dp = next((c for c in entity.constraints
                              if c.name.startswith("DP_") and c.influence > 0.5), None)

        if active_dp:
            row = layout.box().row(align=True)
            row.label(text=active_dp.name, icon='LINKED')
            row.label(text="● ACTIVE", icon='BLANK1')

        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("animtools.dp_create",
                     text="Switch" if active_dp else "Create", icon='PLUS')
        col.operator("animtools.dp_disable", text="Disable", icon='UNLINKED')

        layout.separator(factor=0.3)
        row = layout.row()
        row.scale_y = 0.9
        row.menu("AT_MT_DynamicParentClearMenu", text="Clear / Bake", icon='TRASH')


class AT_PT_ParentRigPanel(_SubPanel, bpy.types.Panel):
    """Rig an object with ROOT + CTRL cube bones, ready for Dynamic Parent."""
    bl_label = "Parent Rig"
    bl_idname = "AT_PT_ParentRigPanel"
    bl_parent_id = "AT_PT_DynamicParentPanel"
    bl_options = set()  # open by default

    def draw_header(self, context):
        self.layout.label(text="", icon='MESH_CUBE')

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("animtools.parent_rig", text="Parent Rig", icon='MESH_CUBE')

        rig = context.object
        if is_rig(rig):  # a rig is active (Object or Pose Mode)
            col = layout.column(align=True)
            col.prop(rig, "at_rig_root_size", text="Root Size")
            col.prop(rig, "at_rig_ctrl_size", text="Ctrl Size")


class AT_PT_TransformPanel(_SubPanel, bpy.types.Panel):
    bl_label = "World Transform"
    bl_idname = "AT_PT_TransformPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='OBJECT_ORIGIN')

    def draw(self, context):
        layout = self.layout
        has_clipboard = core.clipboard_get() is not None

        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("animtools.copy_world", text="Copy", icon='COPYDOWN')
        row = col.row()
        row.enabled = has_clipboard
        row.operator("animtools.paste_world", text="Paste", icon='PASTEDOWN')

        if has_clipboard:
            layout.separator(factor=0.2)
            row = layout.row()
            row.scale_y = 0.8
            row.label(text="● Ready", icon='CHECKMARK')


class AT_PT_ConstraintsPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Constraints"
    bl_idname = "AT_PT_ConstraintsPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='CONSTRAINT')

    def draw(self, context):
        layout = self.layout
        entity, is_bone = get_active_entity(context)
        frame = context.scene.frame_current

        if not entity:
            layout.label(text="No active object", icon='INFO')
            return

        child_of_list = [c for c in entity.constraints if c.type == 'CHILD_OF']
        if not child_of_list:
            layout.label(text="No Child Of constraints", icon='INFO')
            return

        for con in child_of_list:
            if con.target:
                target_display = con.target.name + (f" / {con.subtarget}" if con.subtarget else "")
            else:
                target_display = "(no target)"

            box = layout.box()
            header = box.row(align=True)
            if con.name.startswith("DP_"):
                header.label(text="", icon='LIBRARY_DATA_DIRECT')
            op = header.operator(
                "animtools.toggle_constraint", text="",
                icon='RESTRICT_VIEW_OFF' if con.influence > 0.5 else 'RESTRICT_VIEW_ON')
            op.constraint_name = con.name
            header.label(text=con.name)

            kf_icon = check_constraint_keyframe(entity, is_bone, con.name, frame)
            if kf_icon != 'NONE':
                header.label(text="", icon=kf_icon)

            row = box.row()
            row.scale_y = 0.7
            row.label(text=f"→ {target_display}", icon='BLANK1')

            row = box.row()
            row.scale_y = 0.8
            row.prop(con, "influence", text="Influence", slider=True)


class AT_PT_RotationModePanel(_SubPanel, bpy.types.Panel):
    bl_label = "Rotation Mode"
    bl_idname = "AT_PT_RotationModePanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='DRIVER_ROTATIONAL_DIFFERENCE')

    def draw(self, context):
        rotation_mode.draw_panel(self.layout, context)


class AT_PT_FootLockPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Foot Lock"
    bl_idname = "AT_PT_FootLockPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='PINNED')

    def draw(self, context):
        layout = self.layout
        props = context.scene.at_foot_lock

        if context.mode == 'POSE':
            layout.label(text="Pose Mode: selected bones", icon='BONE_DATA')
        elif context.mode == 'OBJECT':
            layout.label(text="Object Mode: selected objects", icon='OBJECT_DATA')
        else:
            layout.label(text="Use Pose Mode or Object Mode", icon='INFO')

        layout.prop(props, "use_playback_range")
        if not props.use_playback_range:
            row = layout.row(align=True)
            row.prop(props, "frame_start")
            row.prop(props, "frame_end")
        layout.row().prop(props, "anchor", expand=True)

        box = layout.box()
        box.label(text="Lock (World Space)", icon='LOCKED')
        row = box.row(align=True)
        row.label(text="Location")
        for a in "xyz":
            row.prop(props, f"loc_{a}", toggle=True)
        row = box.row(align=True)
        row.label(text="Rotation")
        for a in "xyz":
            row.prop(props, f"rot_{a}", toggle=True)
        box.prop(props, "lock_scale", toggle=True)

        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator("animtools.foot_lock_pin", text="Pin", icon='PINNED')
        col.operator("animtools.foot_lock_clear", text="Clear Keys In Range", icon='TRASH')


class AT_PT_LibraryPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Pose & Anim Library"
    bl_idname = "AT_PT_LibraryPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='ASSET_MANAGER')

    def draw(self, context):
        library.draw_panel(self.layout, context)


class AT_PT_HairPhysicsPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Hair Physics"
    bl_idname = "AT_PT_HairPhysicsPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='FORCE_WIND')

    def draw(self, context):
        layout = self.layout
        props = context.scene.at_hair_physics
        layout.use_property_split = False
        layout.use_property_decorate = False

        row = layout.row(align=True)
        row.prop_enum(props, "mode", 'AUTO', text="Auto", icon='LINKED')
        row.prop_enum(props, "mode", 'MANUAL', text="Manual", icon='OUTLINER_OB_EMPTY')

        layout.separator(factor=0.4)
        col = layout.box().column(align=True)
        col.scale_y = 0.9
        if props.mode == 'AUTO':
            col.label(text="Chain: each bone tracks the next", icon='CONSTRAINT')
        else:
            col.prop(props, "target_object", text="Target")
            if props.target_object and props.target_object.type == 'ARMATURE':
                col.prop(props, "target_bone", text="Bone")
        row = col.row(align=True)
        row.prop(props, "start_value", text="Root")
        row.prop(props, "end_value", text="Tip")

        row = layout.row(align=True)
        row.scale_y = 0.9
        row.label(text="Axis:", icon='EMPTY_AXIS')
        row.prop(props, "track_axis", text="")

        layout.separator(factor=0.3)
        col = layout.column(align=True)
        col.prop(props, "use_collision", text="Collision", icon='MOD_PHYSICS')
        if props.use_collision:
            box = col.box()
            box.prop(props, "collision_object", text="Object")
            box.prop(props, "collision_distance", text="Distance")

        if context.mode == 'POSE' and context.selected_pose_bones:
            bones = get_sorted_bones(context)
            n = len(bones)
            layout.separator(factor=0.3)
            box = layout.box()
            row = box.row()
            row.scale_y = 0.8
            row.label(text=f"{n} bone{'s' if n != 1 else ''}", icon='BONE_DATA')

            col = box.column(align=True)
            col.scale_y = 0.65
            for i in range(min(n, 5)):
                val = influence_at(props, i, n)
                if props.mode == 'AUTO' and i < n - 1:
                    text = f"{bones[i].name} → {bones[i + 1].name} [{val:.2f}]"
                else:
                    text = f"{bones[i].name} [{val:.2f}]"
                col.label(text=text, icon='BLANK1')
            if n > 5:
                col.label(text=f"… +{n - 5} more", icon='BLANK1')

        layout.separator(factor=0.4)
        col = layout.column(align=True)
        col.scale_y = 1.1
        col.operator("animtools.apply_hair_physics", text="Apply", icon='FORCE_WIND')
        col.operator("animtools.clear_hair_physics", text="Clear", icon='TRASH')


class AT_PT_PlayblastPanel(_SubPanel, bpy.types.Panel):
    bl_label = "Playblast"
    bl_idname = "AT_PT_PlayblastPanel"

    def draw_header(self, context):
        self.layout.label(text="", icon='RENDER_ANIMATION')

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.3
        row.operator("animtools.playblast", text="Playblast", icon='RENDER_ANIMATION')
        draw_playblast_settings(layout, context.scene)


# ==========================================
# REGISTER
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
    AT_PT_DynamicParentPanel,
    AT_PT_ParentRigPanel,
    AT_PT_TransformPanel,
    AT_PT_RotationModePanel,
    AT_PT_ConstraintsPanel,
    AT_PT_FootLockPanel,
    AT_PT_LibraryPanel,
    AT_PT_HairPhysicsPanel,
    AT_PT_PlayblastPanel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
