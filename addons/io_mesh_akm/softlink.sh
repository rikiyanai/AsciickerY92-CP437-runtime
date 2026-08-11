
BLENDER_ADDONS_DIR="${1:-${BLENDER_ADDONS_DIR:-$HOME/blender-2.82a-linux64/2.82/scripts/addons}}"
ASCIIID_DIR="${2:-${ASCIIID_DIR:-$HOME/asciiid}}"
cd "$BLENDER_ADDONS_DIR"
ln -s "$ASCIIID_DIR/io_mesh_akm" io_mesh_akm
