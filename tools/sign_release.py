"""Sign a completed installer and generate its update feed. Never embeds a secret."""
import argparse
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import re
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

NS = 'http://www.andymatuschak.org/xml-namespaces/sparkle'
ET.register_namespace('sparkle', NS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=Path, required=True)
    parser.add_argument('--tool', type=Path, required=True, help='Official winsparkle-tool.exe')
    parser.add_argument('--public-key', type=Path, default=Path(__file__).resolve().parents[1] / 'updates/public-key.txt')
    parser.add_argument('--installer', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+', args.version):
        parser.error('Version must have the form 1.2.0')
    if args.installer.name != f'SSB-Setup-{args.version}.exe':
        parser.error('Installer name must match the release version')
    data = args.installer.read_bytes()
    # WinSparkle owns its key-file format. Keep private material out of Python
    # and verify against the exact public key embedded in the application.
    result = subprocess.run([str(args.tool.resolve()), 'sign', '-f', str(args.key.resolve()),
                             str(args.installer.resolve())], check=True, capture_output=True, text=True)
    signature = result.stdout.strip()
    if not re.fullmatch(r'[A-Za-z0-9+/]{86}==', signature):
        raise RuntimeError('WinSparkle did not return a valid EdDSA signature.')
    subprocess.run([str(args.tool.resolve()), 'verify', '--public-key',
                    args.public_key.read_text(encoding='ascii').strip(), '--signature',
                    signature, str(args.installer.resolve())], check=True)
    root = ET.Element('rss', {'version': '2.0'})
    channel = ET.SubElement(root, 'channel')
    ET.SubElement(channel, 'title').text = 'SSB stable updates'
    item = ET.SubElement(channel, 'item')
    ET.SubElement(item, 'title').text = 'SSB ' + args.version
    ET.SubElement(item, f'{{{NS}}}version').text = args.version
    ET.SubElement(item, 'pubDate').text = format_datetime(datetime.now(timezone.utc))
    ET.SubElement(item, 'description').text = 'Optional scheduled Internet cutoff. Your saved website list and settings are preserved.'
    ET.SubElement(item, 'enclosure', {
        'url': f'https://github.com/Randosa/simplesiteblocker/releases/download/v{args.version}/{args.installer.name}',
        'length': str(len(data)), 'type': 'application/octet-stream',
        f'{{{NS}}}edSignature': signature, f'{{{NS}}}os': 'windows-x64',
        f'{{{NS}}}installerArguments': '/SILENT /SP- /NORESTART',
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    ET.ElementTree(root).write(args.output, encoding='utf-8', xml_declaration=True)
    args.installer.with_suffix('.sha256').write_text(hashlib.sha256(data).hexdigest()+'  '+args.installer.name+'\n', encoding='ascii')
    print('Installer signed and verified. Publish this feed only after the matching release asset is available.')


if __name__ == '__main__':
    main()
