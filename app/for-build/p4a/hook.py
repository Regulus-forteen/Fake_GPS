from pathlib import Path
from pythonforandroid.toolchain import ToolchainCL


def after_apk_build(toolchain: ToolchainCL):
    manifest = Path(toolchain._dist.dist_dir) / "src" / "main" / "AndroidManifest.xml"
    if not manifest.exists():
        return
    text = manifest.read_text(encoding="utf-8")
    pkg = "com.example.gpsreceiver"
    service_name = f"{pkg}.ServiceReceiver"
    old = f'android:name="{service_name}"'
    if old in text and "foregroundServiceType" not in text:
        text = text.replace(
            f'<service {old}',
            f'<service {old} android:foregroundServiceType="location"'
        )
        manifest.write_text(text, encoding="utf-8")
        print("[hook] foregroundServiceType=location 已注入")