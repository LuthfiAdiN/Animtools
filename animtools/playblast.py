"""Playblast: render the viewport through the scene camera to MP4 and play it.

Button lives in the Dope Sheet header (Timeline, Dope Sheet, Action Editor, ...)
and in the AnimTools sidebar panel.
"""
import os
import subprocess
import sys
import tempfile
import time

import bpy
from bpy.props import BoolProperty, IntProperty

from .core import find_view3d


def _has_sound(scene):
    seq = scene.sequence_editor
    if not seq:
        return False
    strips = getattr(seq, "strips_all", None)
    if strips is None:
        strips = getattr(seq, "sequences_all", [])
    return any(s.type == "SOUND" for s in strips)


def _output_path():
    if bpy.data.filepath:
        folder = os.path.join(os.path.dirname(bpy.data.filepath), "playblast")
        name = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    else:
        folder = os.path.join(tempfile.gettempdir(), "blender_playblast")
        name = "playblast"
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name + ".mp4")
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:  # still open in the player: use a new name
            path = os.path.join(folder, "%s_%s.mp4" % (name, time.strftime("%H%M%S")))
    return path


def _play(path):
    if sys.platform.startswith("win"):
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class AT_OT_Playblast(bpy.types.Operator):
    bl_idname = "animtools.playblast"
    bl_label = "Playblast"
    bl_description = "Render the viewport through the scene camera to an MP4 and play it"

    def execute(self, context):
        scene = context.scene
        if scene.camera is None:
            self.report({"ERROR"}, "Scene has no camera")
            return {"CANCELLED"}
        found = find_view3d(context, "CAMERA")  # the viewport that is looking through the camera
        if found is None:
            self.report({"ERROR"}, "No 3D Viewport found")
            return {"CANCELLED"}
        window, area, region, space = found

        r = scene.render
        ims = r.image_settings
        ff = r.ffmpeg
        r3d = space.region_3d
        path = _output_path()

        saved = dict(
            filepath=r.filepath, media=ims.media_type, fmt=ims.file_format,
            color_mode=ims.color_mode, ff_format=ff.format, ff_codec=ff.codec,
            ff_crf=ff.constant_rate_factor, ff_audio=ff.audio_codec,
            pct=r.resolution_percentage, fs=scene.frame_start, fe=scene.frame_end,
            persp=r3d.view_perspective, frame=scene.frame_current,
        )
        started = time.time()
        ok = True
        try:
            ims.media_type = "VIDEO"
            ims.file_format = "FFMPEG"
            ims.color_mode = "RGB"
            ff.format = "MPEG4"
            ff.codec = "H264"
            ff.constant_rate_factor = "MEDIUM"
            ff.audio_codec = "AAC" if _has_sound(scene) else "NONE"
            r.filepath = path
            r.resolution_percentage = max(1, int(saved["pct"] * scene.at_playblast_scale / 100))
            if scene.use_preview_range:
                scene.frame_start = scene.frame_preview_start
                scene.frame_end = scene.frame_preview_end
            r3d.view_perspective = "CAMERA"
            with context.temp_override(window=window, area=area,
                                       region=region, space_data=space):
                bpy.ops.render.opengl(animation=True, view_context=True)
        except Exception as e:
            ok = False
            self.report({"ERROR"}, "Playblast failed: %s" % e)
        finally:
            ims.media_type = saved["media"]
            ims.file_format = saved["fmt"]
            ims.color_mode = saved["color_mode"]
            ff.format = saved["ff_format"]
            ff.codec = saved["ff_codec"]
            ff.constant_rate_factor = saved["ff_crf"]
            ff.audio_codec = saved["ff_audio"]
            r.filepath = saved["filepath"]
            r.resolution_percentage = saved["pct"]
            scene.frame_start = saved["fs"]
            scene.frame_end = saved["fe"]
            scene.frame_start = saved["fs"]
            scene.frame_set(saved["frame"])  # the viewport render leaves the playhead on the last frame
            r3d.view_perspective = saved["persp"]
        if not ok:
            return {"CANCELLED"}

        if not os.path.exists(path):  # Blender may add a frame-range suffix
            folder = os.path.dirname(path)
            fresh = [os.path.join(folder, f) for f in os.listdir(folder)
                     if f.lower().endswith(".mp4")
                     and os.path.getmtime(os.path.join(folder, f)) >= started]
            if not fresh:
                self.report({"ERROR"}, "Playblast file was not created")
                return {"CANCELLED"}
            path = max(fresh, key=os.path.getmtime)

        self.report({"INFO"}, "Playblast: " + path)
        if scene.at_playblast_autoplay:
            try:
                _play(path)
            except Exception as e:
                self.report({"WARNING"}, "Could not open player: %s" % e)
        return {"FINISHED"}


def draw_settings(layout, scene):
    col = layout.column()
    col.prop(scene, "at_playblast_scale")
    col.prop(scene, "at_playblast_autoplay")


class AT_PT_PlayblastSettings(bpy.types.Panel):
    """Popover next to the header button."""
    bl_idname = "AT_PT_PlayblastSettings"
    bl_label = "Playblast"
    bl_space_type = "DOPESHEET_EDITOR"
    bl_region_type = "HEADER"

    def draw(self, context):
        draw_settings(self.layout, context.scene)
        col = self.layout.column()
        col.label(text="Uses scene camera, resolution")
        col.label(text="and frame range (preview range if on)")


def draw_header_button(self, context):
    st = context.space_data
    if st is None or st.type != "DOPESHEET_EDITOR":
        return  # every Dope Sheet mode: Timeline, Dope Sheet, Action Editor, ...
    row = self.layout.row(align=True)
    row.operator("animtools.playblast", text="", icon="RENDER_ANIMATION")
    row.popover(panel="AT_PT_PlayblastSettings", text="")


classes = (AT_OT_Playblast, AT_PT_PlayblastSettings)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.at_playblast_scale = IntProperty(
        name="Scale", description="Playblast resolution relative to the scene resolution",
        default=100, min=10, max=100, subtype="PERCENTAGE")
    bpy.types.Scene.at_playblast_autoplay = BoolProperty(
        name="Auto Play", description="Open the playblast in the default player when done",
        default=True)
    bpy.types.DOPESHEET_HT_header.append(draw_header_button)


def unregister():
    bpy.types.DOPESHEET_HT_header.remove(draw_header_button)
    del bpy.types.Scene.at_playblast_autoplay
    del bpy.types.Scene.at_playblast_scale
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
