# -*- coding: utf-8 -*-
"""生成 Android 发布签名用的 keystore（需要 JDK 的 keytool）

用法：
    python make_keystore.py
    python make_keystore.py 你的密码

自动在常见位置找 keytool，找不到会提示下载地址。
"""
import base64
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = Path(__file__).resolve().parent
OUT = BASE / 'app' / 'release.keystore'
B64OUT = BASE / 'keystore_base64.txt'

ALIAS = 'fakegps'
DNAME = 'CN=Regulus, OU=Release, O=regulus14.top, C=CN'

SEARCH = [
    r'C:\Program Files\Java',
    r'C:\Program Files\Eclipse Adoptium',
    r'C:\Program Files\Microsoft',
    r'C:\Program Files\Android\Android Studio\jbr',
    os.path.expandvars(r'%LOCALAPPDATA%\Programs\Eclipse Adoptium'),
    os.path.expandvars(r'%USERPROFILE%\.jdks'),
]


def find_keytool():
    # 1) JAVA_HOME
    jh = os.environ.get('JAVA_HOME')
    if jh:
        p = Path(jh) / 'bin' / ('keytool.exe' if os.name == 'nt' else 'keytool')
        if p.exists():
            return str(p)

    # 2) PATH
    p = shutil.which('keytool')
    if p:
        return p

    # 3) 常见安装目录
    for root in SEARCH:
        rp = Path(root)
        if not rp.exists():
            continue
        for cand in rp.glob('*/bin/keytool.exe'):
            return str(cand)
        for cand in rp.glob('bin/keytool.exe'):
            return str(cand)
    return None


def main():
    if OUT.exists():
        print('已存在 %s' % OUT)
        ans = input('覆盖？(y/N) ').strip().lower()
        if ans != 'y':
            return 1
        OUT.unlink()

    keytool = find_keytool()
    if not keytool:
        print('找不到 keytool（JDK 自带）。')
        print()
        print('两个办法：')
        print('  A. 装一个 JDK 17（约 180MB）：')
        print('     https://adoptium.net/temurin/releases/?version=17')
        print('     装完重开终端再跑本脚本')
        print()
        print('  B. 不本地生成，直接让 GitHub Actions 生成（推荐，省事）：')
        print('     见仓库里的 docs/签名说明.md')
        return 1

    print('使用 keytool: %s' % keytool)
    pw = sys.argv[1] if len(sys.argv) > 1 else secrets.token_urlsafe(18)
    if len(pw) < 8:
        print('密码至少 8 位')
        return 1

    cmd = [
        keytool, '-genkeypair', '-v',
        '-keystore', str(OUT),
        '-alias', ALIAS,
        '-keyalg', 'RSA', '-keysize', '2048',
        '-validity', '10000',
        '-storepass', pw,
        '-keypass', pw,
        '-dname', DNAME,
        '-storetype', 'JKS',
    ]

    print('生成 keystore...')
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
    if r.returncode != 0:
        print('keytool 失败：')
        print(r.stdout[-1200:])
        print(r.stderr[-1200:])
        return 1

    print('已生成 %s（%d 字节）' % (OUT, OUT.stat().st_size))

    raw = OUT.read_bytes()
    B64OUT.write_text(base64.b64encode(raw).decode('ascii'), encoding='ascii')
    print('已生成 %s' % B64OUT.name)

    print()
    print('=' * 66)
    print('去 GitHub → Settings → Secrets and variables → Actions')
    print('点 New repository secret，逐个填 4 个：')
    print('=' * 66)
    print('  KEYSTORE_BASE64     = keystore_base64.txt 全部内容')
    print('  KEYSTORE_PASSWORD   = %s' % pw)
    print('  KEY_ALIAS           = %s' % ALIAS)
    print('  KEY_PASSWORD        = %s' % pw)
    print('=' * 66)
    print('把 release.keystore 和密码另存一份，丢了就无法给同一包名发更新。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())