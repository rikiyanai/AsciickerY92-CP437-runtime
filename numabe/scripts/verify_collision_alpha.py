#!/usr/bin/env python3
import argparse
import os
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import status as cli_status  # noqa: E402

class ElementSpec:
    __slots__ = ("name", "count", "properties")

    def __init__(self, name, count):
        self.name = name
        self.count = count
        self.properties = []

    def load(self, format_type, stream):
        if format_type == b"ascii":
            stream = stream.readline().split()
        return [p.load(format_type, stream) for p in self.properties]

    def index(self, name):
        for i, p in enumerate(self.properties):
            if p.name == name:
                return i
        return -1

class PropertySpec:
    __slots__ = ("name", "list_type", "numeric_type")

    def __init__(self, name, list_type, numeric_type):
        self.name = name
        self.list_type = list_type
        self.numeric_type = numeric_type

    def read_format(self, format_type, count, num_type, stream):
        if format_type == b"ascii":
            mapper = float if num_type in ("f", "d") else int
            ans = [mapper(x) for x in stream[:count]]
            stream[:count] = []
            return ans

        fmt = "%s%i%s" % (format_type, count, num_type)
        data = stream.read(struct.calcsize(fmt))
        return struct.unpack(fmt, data)

    def load(self, format_type, stream):
        if self.list_type is not None:
            count = int(self.read_format(format_type, 1, self.list_type, stream)[0])
            return self.read_format(format_type, count, self.numeric_type, stream)
        return self.read_format(format_type, 1, self.numeric_type, stream)[0]

class ObjectSpec:
    __slots__ = ("specs",)

    def __init__(self):
        self.specs = []

    def load(self, format_type, stream):
        return {
            i.name: [i.load(format_type, stream) for _ in range(i.count)]
            for i in self.specs
        }

def read_ply(filepath):
    format_specs = {
        b"binary_little_endian": "<",
        b"binary_big_endian": ">",
        b"ascii": b"ascii",
    }
    type_specs = {
        b"char": "b", b"uchar": "B",
        b"int8": "b", b"uint8": "B",
        b"int16": "h", b"uint16": "H",
        b"short": "h", b"ushort": "H",
        b"int": "i", b"int32": "i",
        b"uint": "I", b"uint32": "I",
        b"float": "f", b"float32": "f",
        b"float64": "d", b"double": "d",
        b"string": "s",
    }

    obj_spec = ObjectSpec()
    format_type = b""

    with open(filepath, "rb") as handle:
        signature = handle.readline()
        if not signature.startswith(b"ply"):
            return None, None

        valid_header = False
        for line in handle:
            tokens = re.split(br"[ \r\n]+", line)
            if not tokens:
                continue

            if tokens[0] == b"end_header":
                valid_header = True
                break
            if tokens[0] == b"format":
                if len(tokens) >= 3 and tokens[1] in format_specs:
                    format_type = tokens[1]
            elif tokens[0] == b"element":
                if len(tokens) >= 3:
                    obj_spec.specs.append(ElementSpec(tokens[1], int(tokens[2])))
            elif tokens[0] == b"property":
                if obj_spec.specs:
                    if tokens[1] == b"list":
                        obj_spec.specs[-1].properties.append(
                            PropertySpec(tokens[4], type_specs[tokens[2]], type_specs[tokens[3]])
                        )
                    else:
                        obj_spec.specs[-1].properties.append(
                            PropertySpec(tokens[2], None, type_specs[tokens[1]])
                        )

        if not valid_header:
            return None, None

        obj = obj_spec.load(format_specs[format_type], handle)

    return obj_spec, obj

DEFAULT_EXPECTED = {
    "assets/meshes/PassMesh.akm": 255,
    "assets/meshes/SolidMesh.akm": 0,
}

def collect_alpha(obj_spec, obj):
    vertex_spec = None
    for spec in obj_spec.specs:
        if spec.name == b"vertex":
            vertex_spec = spec
            break

    if not vertex_spec:
        raise ValueError("No vertex element found")

    alpha_idx = vertex_spec.index(b"alpha")
    if alpha_idx == -1:
        raise ValueError("No alpha property found in vertex data")

    vertices = obj.get(b"vertex", [])
    if not vertices:
        raise ValueError("No vertex data found")

    return [v[alpha_idx] for v in vertices]

def check_file(path, expected):
    obj_spec, obj = read_ply(path)
    if obj_spec is None or obj is None:
        raise ValueError("Failed to parse PLY")

    alphas = collect_alpha(obj_spec, obj)
    bad = [a for a in alphas if int(a) != expected]
    if bad:
        raise ValueError(f"Alpha mismatch: expected {expected}, found {len(bad)} mismatches")

def main():
    parser = argparse.ArgumentParser(description="Verify collision alpha values in AKM files.")
    parser.add_argument("--file", action="append", help="AKM file path to verify.")
    parser.add_argument("--expected", type=int, help="Expected alpha value (0-255).")
    args = parser.parse_args()

    targets = {}
    if args.file:
        if args.expected is None:
            print("--expected is required when using --file")
            sys.exit(2)
        for path in args.file:
            targets[path] = args.expected
    else:
        targets = DEFAULT_EXPECTED

    failures = []
    for path, expected in targets.items():
        if not os.path.exists(path):
            failures.append(f"{path}: missing file")
            continue
        try:
            check_file(path, expected)
            print(cli_status("PASS", f"{path} alpha={expected}"))
        except Exception as exc:
            failures.append(f"{path}: {exc}")

    if failures:
        for fail in failures:
            print(cli_status("FAIL", str(fail)))
        sys.exit(1)

    sys.exit(0)

if __name__ == "__main__":
    main()
