import os
import qrcode
import app.context as ctx

def save_qr_png(text: str, filename_no_ext: str) -> str:
    os.makedirs(ctx.SET.QRCODES_DIR, exist_ok=True)
    path = os.path.join(ctx.SET.QRCODES_DIR, f"{filename_no_ext}.png")
    img = qrcode.make(text)
    img.save(path)
    return path
