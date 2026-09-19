"""Build the HACS artifact from an explicit runtime allowlist."""
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / 'custom_components' / 'atodosol_solarman'


def build():
    manifest = json.loads((COMPONENT / 'manifest.json').read_text())
    assert manifest['domain'] == COMPONENT.name
    assert manifest['version'] != '0.0.0'
    output = ROOT / 'dist' / f'{COMPONENT.name}.zip'
    output.parent.mkdir(exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in sorted(COMPONENT.rglob('*')):
            relative = path.relative_to(COMPONENT)
            runtime_file = path.suffix in {'.py', '.json', '.yaml', '.svg', '.png'}
            license_file = path.name.lower() in {'license', 'license.txt'}
            if path.is_file() and (runtime_file or license_file) and not any(p.startswith('.') or p in ('__pycache__', 'custom') for p in relative.parts):
                archive.write(path, str(relative))
        archive.write(ROOT / 'LICENSE', 'LICENSE')
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert {'manifest.json','config_flow.py','lan.py','modbus_tcp.py','translations/es.json','LICENSE','pysolarman/license','pysolarman/umodbus/license'} <= set(archive.namelist())
    print(output)
    return output


if __name__ == '__main__':
    build()
