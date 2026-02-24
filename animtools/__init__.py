bl_info = {
    "name": "AnimTools",
    "author": "LuthfiAdiN",
    "version": (0, 0, 2),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar > AnimTools",
    "description": "Animation utilities focusing on constraint workflow and hair physics.",
    "category": "Animation",
}

from . import ui
from . import core
from . import hair_physics  # ← Modul baru

def register():
    ui.register()
    hair_physics.register()  # ← Daftarkan

def unregister():
    ui.unregister()
    hair_physics.unregister()  # ← Hapus saat di-uninstall

if __name__ == "__main__":
    register()
