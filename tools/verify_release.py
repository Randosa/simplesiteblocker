"""Check feed/installer agreement and prove that changed bytes fail verification."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tool', type=Path, required=True)
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--feed', type=Path, required=True)
    parser.add_argument('--public-key', type=Path, default=Path(__file__).resolve().parents[1] / 'updates/public-key.txt')
    args = parser.parse_args()
    enclosure = ET.parse(args.feed).find('channel/item/enclosure')
    data = args.installer.read_bytes()
    assert int(enclosure.get('length')) == len(data)
    assert enclosure.get('url').endswith('/' + args.installer.name)
    digest = args.installer.with_suffix('.sha256').read_text().split()[0]
    assert hashlib.sha256(data).hexdigest() == digest
    signature = enclosure.get('{http://www.andymatuschak.org/xml-namespaces/sparkle}edSignature')
    command = [str(args.tool.resolve()), 'verify', '--public-key', args.public_key.read_text().strip(), '--signature', signature]
    subprocess.run([*command, str(args.installer.resolve())], check=True)
    with tempfile.TemporaryDirectory(prefix='ssb-signature-test-') as directory:
        changed = Path(directory) / args.installer.name
        changed.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        result = subprocess.run([*command, str(changed)], capture_output=True)
        assert result.returncode != 0, 'A changed installer must be rejected'
    print('PASS: feed length, filename, SHA-256, valid signature, and rejection after one changed byte.')


if __name__ == '__main__':
    main()
