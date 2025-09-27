"""QR code generation helper.

This module provides a simple wrapper around the `qrcode` library to
save WireGuard configuration text as a PNG image.  The generated QR
codes can be scanned by mobile WireGuard clients to import
configuration easily.
"""

import os
import qrcode
import app.context as ctx


def save_qr_png(text: str, filename_no_ext: str) -> str:
    """Generate a QR code from ``text`` and save it as a PNG file.

    The file is saved in the directory specified by
    ``ctx.SET.QRCODES_DIR``.  The filename is constructed from
    ``filename_no_ext`` with a ``.png`` extension.  The resulting path
    is returned.

    Parameters
    ----------
    text: str
        The textual content to encode into the QR code.
    filename_no_ext: str
        The base filename (without extension) for the PNG.

    Returns
    -------
    str
        The absolute path to the saved PNG file.
    """
    os.makedirs(ctx.SET.QRCODES_DIR, exist_ok=True)
    path = os.path.join(ctx.SET.QRCODES_DIR, f"{filename_no_ext}.png")
    img = qrcode.make(text)
    img.save(path)
    return path