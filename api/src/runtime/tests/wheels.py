"""テストで使う小さなwheel（純粋なPython。`import <name>` で `VALUE` を読める）"""

import base64
import hashlib
import io
import zipfile

from artifact.models import BlobObject
from runtime.models import PythonWheel


def build_wheel(name: str, version: str, value: str = "ok") -> tuple[str, bytes]:
    """（ファイル名、中身）"""
    module = name.replace("-", "_")
    dist = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": f"VALUE = {value!r}\n".encode(),
        f"{dist}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{dist}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(data)}")
    record.append(f"{dist}/RECORD,,")
    files[f"{dist}/RECORD"] = ("\n".join(record) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return f"{module}-{version}-py3-none-any.whl", buffer.getvalue()


def stored_wheel(name: str, version: str, value: str = "ok") -> PythonWheel:
    filename, data = build_wheel(name, version, value)
    return PythonWheel.objects.create(
        filename=filename,
        name=name,
        version=version,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        blob=BlobObject.put(io.BytesIO(data)),
    )
