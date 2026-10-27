"""Generate PyInstaller PE identity/version metadata from the release constant."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from web_dialogue.updates import CURRENT_VERSION, version_tuple

def write_resource(path):
    version=version_tuple(CURRENT_VERSION);quad=(*version,0)
    value=f'''VSVersionInfo(ffi=FixedFileInfo(filevers={quad!r}, prodvers={quad!r}, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', 'Harry6400'), StringStruct('FileDescription', 'DialogueTTS'), StringStruct('FileVersion', '{CURRENT_VERSION}'), StringStruct('InternalName', 'DialogueTTS'), StringStruct('OriginalFilename', 'DialogueTTS.exe'), StringStruct('ProductName', 'DialogueTTS'), StringStruct('ProductVersion', '{CURRENT_VERSION}')])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])'''
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(value,encoding='utf-8');return path
