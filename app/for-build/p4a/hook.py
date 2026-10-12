from pathlib import Path


def after_apk_build(toolchain):
    """为前台 Service 注入 android:foregroundServiceType="location"
    Android 14+ 要求前台服务必须声明类型，否则启动时崩溃。"""
    try:
        dist_dir = getattr(getattr(toolchain, 'ctx', None), 'dist_dir', None)
        if not dist_dir:
            print("[hook] 未获取到 dist_dir，跳过")
            return

        # 在整个 dist 目录里找 AndroidManifest.xml，不猜具体路径
        manifests = list(Path(dist_dir).rglob("AndroidManifest.xml"))
        if not manifests:
            print("[hook] 未找到 AndroidManifest.xml")
            return

        marker = 'android:name="com.example.gpsreceiver.ServiceReceiver"'
        patched = False

        for manifest in manifests:
            text = manifest.read_text(encoding="utf-8")
            if marker not in text:
                continue
            if "foregroundServiceType" in text:
                print(f"[hook] 已含 foregroundServiceType，跳过 {manifest.name}")
                continue
            text = text.replace(
                f'<service {marker}',
                f'<service {marker} android:foregroundServiceType="location"',
                1,
            )
            manifest.write_text(text, encoding="utf-8")
            print(f"[hook] 注入成功: {manifest}")
            patched = True

        if not patched:
            print("[hook] 没有需要修改的 manifest")
    except Exception as e:
        # 关键：hook 出错绝不能往外抛，否则会杀掉 p4a 导致整个构建失败
        print(f"[hook] 出错但不影响构建: {e}")