"""Output names are strings, independent of XML identity and audio fingerprints."""
import re

NAME = re.compile(r'([0-9]+)([01])\.xml', re.IGNORECASE | re.ASCII)
OUTPUT = re.compile(r'[0-9]+\.mp3', re.ASCII)


def valid_output_name(value):
    return isinstance(value, str) and len(value) <= 255 and OUTPUT.fullmatch(value) is not None


def pair_filename(files):
    # Call only after XML identity and roles have been validated.
    matches = [NAME.fullmatch(item['name']) if isinstance(item.get('name'),str) else None for item in files]
    if len(matches) != 2 or not all(matches):
        raise ValueError('文件名须为相同非空数字前缀、分别以0和1结尾的两份 XML。')
    first, second = matches
    if first[1] != second[1] or {first[2], second[2]} != {'0', '1'}:
        raise ValueError('两份 XML 文件名须有相同数字前缀，末位分别为0和1。')
    filename = first[1] + '.mp3'
    if not valid_output_name(filename):
        raise ValueError('输出文件名过长，请缩短两份 XML 的共同数字前缀。')
    return filename
