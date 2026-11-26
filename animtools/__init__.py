if "core" in locals():  # support Reload Scripts
    import importlib
    for _m in (core, hair_physics, playblast, parent_rig, foot_lock, library, rotation_mode, ui):
        importlib.reload(_m)
else:
    from . import core
    from . import hair_physics
    from . import playblast
    from . import parent_rig
    from . import foot_lock
    from . import library
    from . import rotation_mode
    from . import ui


def register():
    hair_physics.register()   # property group first: the panels read it
    playblast.register()
    parent_rig.register()
    foot_lock.register()
    library.register()
    rotation_mode.register()
    ui.register()


def unregister():
    ui.unregister()
    rotation_mode.unregister()
    library.unregister()
    foot_lock.unregister()
    parent_rig.unregister()
    playblast.unregister()
    hair_physics.unregister()


if __name__ == "__main__":
    register()
