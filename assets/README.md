# CoreWarden desktop icon hook

Place the final Windows icon at `assets/corewarden.ico` before packaging.

The GUI and `CoreWarden.spec` detect that exact file automatically. When it is
absent, the build succeeds with the default application icon.
