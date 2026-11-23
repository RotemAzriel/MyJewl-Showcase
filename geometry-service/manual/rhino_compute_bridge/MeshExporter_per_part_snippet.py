import Rhino.Geometry as rg
import struct
import os
import clr
import hashlib
import json
import uuid
import datetime
import math
import shutil

clr.AddReference('Grasshopper')
try:
    import Grasshopper.Kernel as ghk
except Exception:
    ghk = None

# ── MeshExporter: receipt-backed same-solve per-part export ───────────────────
# Inputs (by param index): 0=band_brep, 1=stone_brep, 2=prong_breps, 3=job_id
# Outputs: a=stl_path, b=glb_path
#
# IMPORTANT OPERATOR STEP:
#   After pasting this source into the Save-As candidate .gh, save the .gh file
#   before solving/exporting. The receipt hashes the saved .gh definition.


def _runtime_error(message):
    try:
        if ghk is not None:
            ghenv.Component.AddRuntimeMessage(ghk.GH_RuntimeMessageLevel.Error, message)
    except Exception:
        pass


def _runtime_remark(message):
    try:
        if ghk is not None:
            ghenv.Component.AddRuntimeMessage(ghk.GH_RuntimeMessageLevel.Remark, message)
    except Exception:
        pass


# ── 1. Read Breps directly from GH component param volatile data ───────────────
def get_breps_from_param(param_index):
    """Extract all Breps from a GH input param's volatile data tree.
    Handles direct Brep, GH_Brep wrapper (.Value), and generic TryConvertBrep.
    More reliable than standard input coercion across Cluster boundaries."""
    param = ghenv.Component.Params.Input[param_index]
    vdata = param.VolatileData
    breps = []
    for path in vdata.Paths:
        for item in vdata.get_Branch(path):
            if item is None:
                continue
            if isinstance(item, rg.Brep):
                breps.append(item)
            elif hasattr(item, 'Value') and item.Value is not None:
                v = item.Value
                if isinstance(v, rg.Brep):
                    breps.append(v)
                else:
                    b = rg.Brep.TryConvertBrep(v)
                    if b:
                        breps.append(b)
            else:
                b = rg.Brep.TryConvertBrep(item)
                if b:
                    breps.append(b)
    return breps


def _sha256_file(filepath):
    digest = hashlib.sha256()
    with open(filepath, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _env_value(name):
    value = str(os.environ.get(name, "")).strip()
    if value:
        return value
    try:
        import System
        machine_value = System.Environment.GetEnvironmentVariable(
            name,
            System.EnvironmentVariableTarget.Machine,
        )
        if machine_value:
            return str(machine_value).strip()
    except Exception:
        pass
    return ""


def _definition_info():
    definition_document = ghenv.Component.OnPingDocument()
    definition_path = ""
    if definition_document is not None:
        try:
            definition_path = str(definition_document.FilePath or "").strip()
        except Exception:
            definition_path = ""
    if definition_path and os.path.isfile(definition_path):
        try:
            is_modified = bool(getattr(definition_document, "Modified", False))
        except Exception:
            is_modified = False
        if is_modified:
            raise ValueError("Save the candidate Grasshopper definition before exporting; it still has unsaved changes")
        return (
            definition_path,
            _sha256_file(definition_path),
            os.path.splitext(os.path.basename(definition_path))[0],
        )

    # Rhino.Compute /grasshopper often evaluates a posted base64 definition
    # without assigning GH_Document.FilePath. Keep the receipt authoritative by
    # hashing a VM-local copy of the exact candidate definition when provided.
    fallback_path = _env_value("MYJEWL_GH_DEFINITION_PATH")
    if fallback_path and os.path.isfile(fallback_path):
        return (
            fallback_path,
            _sha256_file(fallback_path),
            os.path.splitext(os.path.basename(fallback_path))[0],
        )

    fallback_hash = _env_value("MYJEWL_GH_DEFINITION_SHA256").lower()
    fallback_version = _env_value("MYJEWL_GH_DEFINITION_VERSION") or "solitaire_prototype_smoke_v0_compute_embedded"
    if len(fallback_hash) == 64 and all(char in "0123456789abcdef" for char in fallback_hash):
        return ("embedded:" + fallback_version, fallback_hash, fallback_version)

    raise ValueError("Save the candidate Grasshopper definition before exporting")


def _point_tuple(point):
    return (float(point.X), float(point.Y), float(point.Z))


def _mesh_breps_to_triangles(breps, mesh_params):
    triangles = []
    for brep in breps:
        meshes = rg.Mesh.CreateFromBrep(brep, mesh_params)
        if not meshes:
            continue
        for mesh in meshes:
            if mesh is None:
                continue
            mesh.Faces.ConvertQuadsToTriangles()
            verts = mesh.Vertices
            for i in range(mesh.Faces.Count):
                face = mesh.Faces[i]
                if face.IsTriangle:
                    triangles.append((
                        _point_tuple(verts[face.A]),
                        _point_tuple(verts[face.B]),
                        _point_tuple(verts[face.C]),
                    ))
                else:
                    triangles.append((
                        _point_tuple(verts[face.A]),
                        _point_tuple(verts[face.B]),
                        _point_tuple(verts[face.C]),
                    ))
                    triangles.append((
                        _point_tuple(verts[face.A]),
                        _point_tuple(verts[face.C]),
                        _point_tuple(verts[face.D]),
                    ))
    return triangles


def _triangle_normal(v0, v1, v2):
    e1x = v1[0] - v0[0]
    e1y = v1[1] - v0[1]
    e1z = v1[2] - v0[2]
    e2x = v2[0] - v0[0]
    e2y = v2[1] - v0[1]
    e2z = v2[2] - v0[2]
    nx = (e1y * e2z) - (e1z * e2y)
    ny = (e1z * e2x) - (e1x * e2z)
    nz = (e1x * e2y) - (e1y * e2x)
    length = math.sqrt((nx * nx) + (ny * ny) + (nz * nz))
    if length <= 0.0:
        return (0.0, 0.0, 0.0)
    return (nx / length, ny / length, nz / length)


def _bounds_mm(triangles):
    if not triangles:
        raise ValueError("Cannot export an empty triangle group")
    xs = []
    ys = []
    zs = []
    for triangle in triangles:
        for point in triangle:
            xs.append(point[0])
            ys.append(point[1])
            zs.append(point[2])
    return [
        [min(xs), min(ys), min(zs)],
        [max(xs), max(ys), max(zs)],
    ]


def _write_binary_stl_atomic(filepath, triangles):
    temporary = filepath + ".tmp"
    with open(temporary, "wb") as handle:
        header = b"MyJewl authoritative same-solve export"
        handle.write(header[:80].ljust(80, b"\0"))
        handle.write(struct.pack("<I", len(triangles)))
        for v0, v1, v2 in triangles:
            normal = _triangle_normal(v0, v1, v2)
            handle.write(struct.pack("<3f", normal[0], normal[1], normal[2]))
            handle.write(struct.pack("<3f", v0[0], v0[1], v0[2]))
            handle.write(struct.pack("<3f", v1[0], v1[1], v1[2]))
            handle.write(struct.pack("<3f", v2[0], v2[1], v2[2]))
            handle.write(struct.pack("<H", 0))
    os.replace(temporary, filepath)


def _verify_binary_stl(filepath, triangles):
    expected_size = 84 + (50 * len(triangles))
    actual_size = os.path.getsize(filepath)
    if actual_size != expected_size:
        raise IOError("Binary STL size mismatch for {0}: expected {1}, got {2}".format(
            filepath,
            expected_size,
            actual_size,
        ))
    with open(filepath, "rb") as handle:
        handle.seek(80)
        recorded_count = struct.unpack("<I", handle.read(4))[0]
    if recorded_count != len(triangles):
        raise IOError("Binary STL triangle-count mismatch for " + filepath)
    return {
        "sha256": _sha256_file(filepath),
        "byte_size": actual_size,
        "triangle_count": len(triangles),
        "bounds_mm": _bounds_mm(triangles),
    }


def _write_json_atomic(filepath, value):
    temporary = filepath + ".tmp"
    with open(temporary, "w") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, filepath)


def _mesh_from_triangles(triangles):
    mesh = rg.Mesh()
    for v0, v1, v2 in triangles:
        base = mesh.Vertices.Count
        mesh.Vertices.Add(v0[0], v0[1], v0[2])
        mesh.Vertices.Add(v1[0], v1[1], v1[2])
        mesh.Vertices.Add(v2[0], v2[1], v2[2])
        mesh.Faces.AddFace(base, base + 1, base + 2)
    mesh.Normals.ComputeNormals()
    mesh.Compact()
    return mesh


stl_path = ""
glb_path = ""

try:
    definition_path, definition_hash, definition_version = _definition_info()

    # ── 2. Output directory ───────────────────────────────────────────────────
    output_dir = r"C:\MyJewlGenerated"
    job_id_value = str(job_id)
    export_id = str(uuid.uuid4())
    job_dir = os.path.join(output_dir, job_id_value)
    export_dir = os.path.join(job_dir, export_id)
    os.makedirs(export_dir)

    # ── 3. Collect authoritative Breps by role ────────────────────────────────
    band_breps = get_breps_from_param(0)
    stone_breps = get_breps_from_param(1)
    prong_breps = get_breps_from_param(2)
    if not band_breps:
        raise ValueError("band_brep input produced no Breps")
    if not stone_breps:
        raise ValueError("stone_brep input produced no Breps")
    if not prong_breps:
        raise ValueError("prong_breps input produced no Breps")

    # ── 4. Mesh each role once. model.stl is the exact triangle concatenation ─
    mp = rg.MeshingParameters()
    mp.MaximumEdgeLength = 0.1

    band_triangles = _mesh_breps_to_triangles(band_breps, mp)
    stone_triangles = _mesh_breps_to_triangles(stone_breps, mp)
    prong_triangles = _mesh_breps_to_triangles(prong_breps, mp)
    if not band_triangles:
        raise ValueError("band.stl would be empty")
    if not stone_triangles:
        raise ValueError("stone.stl would be empty")
    if not prong_triangles:
        raise ValueError("prongs.stl would be empty")

    part_groups = [
        ("band.stl", band_triangles),
        ("stone.stl", stone_triangles),
        ("prongs.stl", prong_triangles),
    ]
    combined_triangles = []
    for filename, triangles in part_groups:
        combined_triangles.extend(triangles)

    required_files = [("model.stl", combined_triangles)] + part_groups
    file_facts = {}
    for filename, triangles in required_files:
        filepath = os.path.join(export_dir, filename)
        _write_binary_stl_atomic(filepath, triangles)
        file_facts[filename] = _verify_binary_stl(filepath, triangles)

    # Preserve the legacy JSONExporter path convention without including this
    # alias in export_receipt.json. The receipt intentionally describes only the
    # manifest-declared geometry files.
    model_stl = os.path.join(export_dir, "model.stl")
    legacy_stl = os.path.join(export_dir, job_id_value + "_solitaire.stl")
    shutil.copyfile(model_stl, legacy_stl)
    stl_path = legacy_stl

    # ── 5. Best-effort legacy GLB output. Blender creates authoritative GLB. ──
    try:
        import Rhino.FileIO as rfi
        glb_file = os.path.join(export_dir, job_id_value + "_solitaire.glb")
        scene_mesh = _mesh_from_triangles(combined_triangles)
        opts = rfi.FileGltfWriteOptions()
        ok = rfi.FileGltf.Write(glb_file, scene_mesh, opts)
        if ok and os.path.exists(glb_file) and os.path.getsize(glb_file) > 0:
            glb_path = glb_file
    except Exception:
        glb_path = ""

    # ── 6. Receipt is written last, after all same-solve STLs succeed. ────────
    receipt = {
        "schema_version": 1,
        "export_id": export_id,
        "job_id": job_id_value,
        "gh_definition_sha256": definition_hash,
        "gh_definition_version": definition_version,
        "gh_definition_path": definition_path,
        "exported_at_utc": datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "files": file_facts,
    }
    receipt_path = os.path.join(export_dir, "export_receipt.json")
    _write_json_atomic(receipt_path, receipt)

    _runtime_remark("G-1 export_id={0}; receipt={1}".format(export_id, receipt_path))
except Exception as exc:
    stl_path = ""
    glb_path = ""
    _runtime_error("MeshExporter G-1 export failed: " + str(exc))

a = stl_path   # wire → JSONExporter.stl_path
b = glb_path   # wire → JSONExporter.glb_path
