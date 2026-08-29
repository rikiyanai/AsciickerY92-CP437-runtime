
import os
import sys
import shutil

# Add scripts dir to path to allow importing xp_mcp_server
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    # Attempt to import the tool functions. 
    # FastMCP decorators might wrap them, but usually they are callable or we can access the underlying function.
    from xp_mcp_server import create_xp_file, read_xp_info, add_layer, write_cell, fill_rect, set_metadata, read_layer_region
except ImportError as e:
    print(f"Failed to import xp_mcp_server: {e}")
    sys.exit(1)

def test_xp_mcp():
    test_file = "test_output.xp"
    if os.path.exists(test_file):
        os.remove(test_file)
        
    print("--- Testing create_xp_file ---")
    res = create_xp_file(test_file, 10, 5, 2)
    print(res)
    assert os.path.exists(test_file), "File was not created"
    
    print("\n--- Testing read_xp_info ---")
    info = read_xp_info(test_file)
    print(info)
    assert info['layer_count'] == 2
    assert info['layers'][0]['width'] == 10
    
    print("\n--- Testing write_cell ---")
    # Write red 'A' at (0,0) on layer 1
    res = write_cell(test_file, 1, 0, 0, ord('A'), "#FF0000", "#000000")
    print(res)
    
    print("\n--- Testing fill_rect ---")
    # Fill 3x3 rect with green 'X' at (2,2) on layer 1
    res = fill_rect(test_file, 1, 2, 2, 3, 3, ord('X'), "#00FF00", "#000000")
    print(res)
    
    print("\n--- Testing set_metadata ---")
    res = set_metadata(test_file, 8, [5, 5])
    print(res)
    
    print("\n--- Verifying Content ---")
    region = read_layer_region(test_file, 1, 0, 0, 5, 5)
    data = region['data']
    
    # Check (0,0) - Should be 'A' (65)
    cell_00 = data[0][0]
    print(f"Cell(0,0): {cell_00}")
    assert cell_00[0] == 65
    assert cell_00[1] == "#ff0000"
    
    # Check (2,2) - Should be 'X' (88)
    cell_22 = data[2][2]
    print(f"Cell(2,2): {cell_22}")
    assert cell_22[0] == 88
    assert cell_22[1] == "#00ff00"
    
    print("\n--- Testing add_layer ---")
    res = add_layer(test_file)
    print(res)
    info = read_xp_info(test_file)
    assert info['layer_count'] == 3
    
    print("\nSUCCESS: All tests passed!")
    
    # Clean up
    if os.path.exists(test_file):
        os.remove(test_file)

if __name__ == "__main__":
    test_xp_mcp()
