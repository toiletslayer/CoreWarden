# CoreWarden desktop branding

The approved source artwork is tracked without modification at:

- `assets/Sprite32.png`
- `assets/Sprite64.png`
- `assets/Sprite128.png`

Run `python scripts/build_icon.py` to create `assets/corewarden.ico`. The icon
contains those three native PNG payloads byte-for-byte at 32, 64, and 128 pixels.
The GUI and `CoreWarden.spec` use the resulting icon and packaged PNG assets.
