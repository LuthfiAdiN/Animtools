"""Parent Rig: rig an object with a big ROOT cube bone and a smaller CTRL cube bone inside it.

Merged from the standalone "Double Parent" addon. Rigs made by that addon are still recognised.
The rig bones are normal pose bones, so the Dynamic Parent tools work on them directly.
"""
import bpy
from bpy.props import FloatProperty
from mathutils import Matrix

MARK = "parent_rig"            # custom property on the rig object
LEGACY_MARK = "double_parent"  # set by the old Double Parent addon
WIDGET = "WGT_ParentRig_Cube"
LEGACY_WIDGET = "WGT_DoubleParent_Cube"
ROOT_SUFFIX = "_ROOT_CTRL"
CTRL_SUFFIX = "_CTRL"


def is_rig(obj):
    return obj is not None and (MARK in obj or LEGACY_MARK in obj)


def _half_extent(obj):
    """Farthest point of the object from its origin along any axis, in world scale."""
    sc = obj.matrix_world.to_scale()
    if obj.type == 'EMPTY':
        ext = obj.empty_display_size * max(abs(v) for v in sc)
    else:
        box = obj.bound_box
        ext = max((abs(c[i]) * abs(sc[i]) for c in box for i in range(3)), default=0.0) if box else 0.0
    return ext if ext > 1e-4 else 0.5


def _widget():
    """Shared wire cube (-1..1) used as the bones' custom shape. Needs no scene link."""
    w = bpy.data.objects.get(WIDGET) or bpy.data.objects.get(LEGACY_WIDGET)
    if w:
        return w
    me = bpy.data.meshes.new(WIDGET)
    verts = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    edges = [(i, j) for i in range(8) for j in range(i + 1, 8) if bin(i ^ j).count("1") == 1]
    me.from_pydata(verts, edges, [])
    me.update()
    return bpy.data.objects.new(WIDGET, me)


def _pair(rig):
    """(ROOT, CTRL) pose bones. Found by name, then by hierarchy if the bones were renamed."""
    bones = rig.pose.bones
    root = next((b for b in bones if b.name.endswith(ROOT_SUFFIX)), None)
    ctrl = next((b for b in bones
                 if b.name.endswith(CTRL_SUFFIX) and not b.name.endswith(ROOT_SUFFIX)), None)
    if root is None or ctrl is None:
        root = next((b for b in bones if not b.parent), None)
        ctrl = root.children[0] if root is not None and root.children else None
    return root, ctrl


def _size_prop(index, label):
    """Uniform cube size of the ROOT (0) or CTRL (1) bone, editable without selecting the bone."""
    def get(self):
        if not is_rig(self):
            return 0.0
        pb = _pair(self)[index]
        return pb.custom_shape_scale_xyz[0] if pb else 0.0

    def set_(self, value):
        pb = _pair(self)[index]
        if pb:
            pb.custom_shape_scale_xyz = (value, value, value)

    return FloatProperty(
        name=label, min=0.001, unit='LENGTH', get=get, set=set_,
        update=lambda self, _ctx: self.update_tag(),
    )


def _rig(context, obj, widget, root_scale, ctrl_ratio):
    world = obj.matrix_world.copy()
    loc, rot, _scale = world.decompose()
    pose = Matrix.LocRotScale(loc, rot, None)  # position + orientation, no scale
    size = _half_extent(obj) * root_scale
    ctrl_size = size * ctrl_ratio
    root_name, ctrl_name = f"{obj.name}{ROOT_SUFFIX}", f"{obj.name}{CTRL_SUFFIX}"
    ctrl_len = ctrl_size * 0.5

    arm = bpy.data.armatures.new(f"Rig_{obj.name}")
    rig = bpy.data.objects.new(f"Rig_{obj.name}", arm)
    rig[MARK] = True
    rig.show_in_front = True  # keep the inner cube visible through the mesh
    coll = obj.users_collection[0] if obj.users_collection else context.scene.collection
    coll.objects.link(rig)

    context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode='EDIT')
    root_b = arm.edit_bones.new(root_name)
    ctrl_b = arm.edit_bones.new(ctrl_name)
    for b, length in ((root_b, size * 0.5), (ctrl_b, ctrl_len)):
        b.head, b.tail = (0, 0, 0), (0, length, 0)
        b.matrix = pose  # same world pose as the object origin
    ctrl_b.parent = root_b
    bpy.ops.object.mode_set(mode='OBJECT')

    for name, s, palette in ((root_name, size, 'THEME03'), (ctrl_name, ctrl_size, 'THEME04')):
        pb = rig.pose.bones[name]
        pb.custom_shape = widget
        pb.use_custom_shape_bone_size = False
        pb.custom_shape_scale_xyz = (s, s, s)
        pb.rotation_mode = 'XYZ'
        pb.color.palette = palette

    # Bone parenting measures from the bone tail; shift back so the object sits at the head.
    context.view_layer.update()
    obj.parent = rig
    obj.parent_type = 'BONE'
    obj.parent_bone = ctrl_name
    obj.matrix_parent_inverse = Matrix.Translation((0, -ctrl_len, 0))
    obj.matrix_world = world  # keep transform; local loc/rot end up zeroed

    arm.bones.active = arm.bones[ctrl_name]
    for pb in rig.pose.bones:
        pb.select = pb.name == ctrl_name  # selection lives on PoseBone since Blender 5.0
    return rig


class AT_OT_ParentRig(bpy.types.Operator):
    bl_idname = "animtools.parent_rig"
    bl_label = "Parent Rig"
    bl_description = "Rig selected objects with a large ROOT cube bone and a smaller CTRL cube bone inside it"
    bl_options = {'REGISTER', 'UNDO'}

    root_scale: FloatProperty(
        name="Root Scale", description="ROOT cube size relative to the object extent",
        default=1.25, min=0.1, soft_max=5.0)
    ctrl_ratio: FloatProperty(
        name="Ctrl Ratio", description="CTRL cube size relative to the ROOT cube",
        default=0.6, min=0.05, max=1.0)

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and bool(context.selected_objects)

    def execute(self, context):
        targets = [o for o in context.selected_objects
                   if not is_rig(o) and not is_rig(o.parent)]
        if not targets:
            self.report({'WARNING'}, "Nothing to rig: already rigged or only rigs selected")
            return {'CANCELLED'}

        replaced = [o.name for o in targets if o.parent]
        widget = _widget()
        rigs = [_rig(context, o, widget, self.root_scale, self.ctrl_ratio) for o in targets]

        for o in context.selected_objects:
            o.select_set(False)
        for r in rigs:
            r.select_set(True)
        context.view_layer.objects.active = rigs[-1]

        if replaced:
            self.report({'WARNING'}, "Previous parent replaced by the rig: " + ", ".join(replaced))
        self.report({'INFO'}, f"Rigged {len(rigs)} object(s), Ctrl+Tab for Pose Mode")
        return {'FINISHED'}


classes = (AT_OT_ParentRig,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.at_rig_root_size = _size_prop(0, "Root Size")
    bpy.types.Object.at_rig_ctrl_size = _size_prop(1, "Ctrl Size")


def unregister():
    del bpy.types.Object.at_rig_ctrl_size
    del bpy.types.Object.at_rig_root_size
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
